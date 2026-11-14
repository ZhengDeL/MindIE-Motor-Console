"""Read-only Docker lifecycle and fixed-layout capacity evidence.

Coordinator readiness retains its native meaning. These gates separately answer
whether the platform's deployed containers and requested instance layout agree.
No probe writes to an Agent, changes Motor configuration, or sends inference.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import contextvars
from datetime import datetime, timezone
import ipaddress
import re
import threading
import time
from urllib.parse import urlencode

from .motor_adapter import docker_container_name, official_helpers
from .store import now


ROLES = ("prefill", "decode", "union")
ROLE_NAMES = {"prefill": "Prefill", "decode": "Decode", "union": "PD 混部"}
_STATES = {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")


def _issue(source, detail, *, node_id=None, role=None):
    return {"source": source, **({"node_id": node_id} if node_id else {}),
            **({"role": role} if role else {}), "detail": detail}


def _text(value, maximum=256, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not value and not empty) \
            or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("观测文本字段无效")
    value.encode("utf-8")
    return value


def _ip(value):
    try:
        return str(ipaddress.ip_address(_text(value, 64)))
    except ValueError as exc:
        raise ValueError("实例或节点 IP 地址无效") from exc


def _counts(value=None):
    return {role: value for role in ROLES}


def _render_rules(upstream_dir=None):
    with official_helpers(upstream_dir) as helpers:
        return (helpers.render.is_render_enabled, helpers.render.role_needs_docker_render,
                helpers.render.docker_render_container_name)


def _layout(service, nodes, render_rules=None):
    placements = service.get("deployed_placements", service.get("placements"))
    if not isinstance(placements, list) or not 1 <= len(placements) <= 256:
        raise ValueError("缺少可核对的已部署布局")
    enabled, needs_render, render_name = render_rules or _render_rules()
    render_enabled = enabled(service.get("user_config", {}))
    expected, groups, hosts = {}, {}, set()
    single = any(isinstance(p, dict) and p.get("role") == "single_container" for p in placements)
    for placement in placements:
        node_id, role = placement.get("node_id"), placement.get("role")
        if node_id not in nodes:
            raise ValueError("已部署布局的执行节点不存在")
        instance = placement.get("instance_name") or ("single" if role == "single_container" else "ctrl")
        # Use the same name budget and native sibling rule as the deployer.
        generated = docker_container_name(service["id"], node_id, role, instance, render_suffix=render_name(""))
        expected.setdefault(node_id, {})[(role, generated)] = {"instance": instance, "role": role}
        if render_enabled and needs_render(role):
            expected[node_id][("render", render_name(generated))] = {"instance": instance + "/render", "role": "render"}
        host = _ip(nodes[node_id].get("address"))
        hosts.add(host)
        if role in ROLES:
            group = groups.setdefault((role, instance), {"hosts": set(), "node_id": node_id})
            group["hosts"].add(host)
    counts = {role: sum(key[0] == role for key in groups) for role in ROLES}
    if single:
        deploy = service.get("user_config", {}).get("motor_deploy_config", {})
        counts = {role: deploy.get(key, 0) for role, key in
                  (("prefill", "p_instances_num"), ("decode", "d_instances_num"), ("union", "hybrid_instances_num"))}
        if not any(counts.values()) and "motor_engine_union_config" in service.get("user_config", {}):
            counts["union"] = 1
        if any(type(value) is not int or not 0 <= value <= 256 for value in counts.values()):
            raise ValueError("单容器原生副本数无效")
    if not any(counts.values()):
        raise ValueError("已部署布局未包含可核对的推理角色")
    return {"containers": expected, "groups": groups, "counts": counts, "single": single, "hosts": hosts}


def project_container_status(payload, node_id, *, received_at=None):
    """Whitelist the existing Agent /status protocol; unknown is never absence."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or len(payload["items"]) > 256:
        raise ValueError("容器状态响应格式或数量无效")
    stamp = _text(payload.get("observed_at"), 64)
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("容器状态缺少时区")
    age = ((received_at or datetime.now(timezone.utc)) - parsed).total_seconds()
    if not -30 <= age <= 45:
        raise ValueError("容器状态已过期或节点时钟异常")
    result, identities, names = [], set(), set()
    for item in payload["items"]:
        if not isinstance(item, dict) or item.get("node_id") != node_id:
            raise ValueError("容器状态的节点归属不匹配")
        identifier, name = _text(item.get("id"), 128), _text(item.get("name"), 128)
        if not re.fullmatch(r"[0-9a-f]{64}", identifier) or identifier in identities or name in names:
            raise ValueError("容器身份重复或无效")
        status = item.get("status")
        health = item.get("health")
        role, revision = _text(item.get("role"), 64), _text(item.get("revision"), 96)
        if status not in _STATES or health not in {None, "starting", "healthy", "unhealthy"} or not _SAFE.fullmatch(revision):
            raise ValueError("容器状态或执行修订无效")
        identities.add(identifier); names.add(name)
        result.append({"id": identifier, "name": name, "node_id": node_id, "role": role,
                       "status": status, "health": health, "revision": revision})
    return {"containers": result, "observed_at": stamp}


def _runtime_gate(layout, responses):
    containers, issues, stamps = [], [], []
    degraded = starting = unknown = False
    for node_id, expected in layout["containers"].items():
        response = responses.get(node_id)
        if response is None:
            unknown = True
            issues.append(_issue("runtime", "Agent 容器状态未取得，请刷新后核对。", node_id=node_id))
            continue
        stamps.append(response["observed_at"])
        matched = set()
        for item in response["containers"]:
            key = (item["role"], item["name"])
            if key not in expected:
                continue
            if key in matched:
                unknown = True
                issues.append(_issue("runtime", "同一布局匹配到多个容器，请核对执行修订。", node_id=node_id, role=item["role"]))
                continue
            matched.add(key); containers.append({**item, "instance_name": expected[key]["instance"]})
            if item["status"] in {"exited", "dead", "paused", "restarting", "removing"} or item["health"] == "unhealthy":
                degraded = True
                detail = "容器健康检查失败" if item["health"] == "unhealthy" else f"容器状态为 {item['status']}"
                issues.append(_issue("runtime", f"{expected[key]['instance']}：{detail}，请查看运行日志。", node_id=node_id, role=item["role"]))
            elif item["status"] != "running" or item["health"] == "starting":
                starting = True
        for key in expected.keys() - matched:
            unknown = True
            issues.append(_issue("runtime", f"未取得 {expected[key]['instance']} 的当前受管容器，不能确认存活。", node_id=node_id, role=key[0]))
    state = "degraded" if degraded else "unknown" if unknown else "starting" if starting else "ready"
    return {"state": state, "observed_at": min(stamps) if stamps else None,
            "containers": containers, "issues": issues[:128]}


def registered_instances(service, payload, *, allowed_roles=ROLES):
    """Validate native registration fields without imposing a runtime layout.

    Docker subsequently matches declared host groups. Kubernetes separately
    verifies tracked resource UIDs and role targets; endpoint IPs are never
    treated as Pod UID proof by this shared native-field parser.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("instances"), list) or len(payload["instances"]) > 512:
        raise ValueError("原生实例清单未取得或格式无效")
    if type(payload.get("count")) is not int or payload["count"] != len(payload["instances"]):
        raise ValueError("原生实例计数与清单不一致")
    records, ids, groups = [], set(), set()
    for item in payload["instances"]:
        if not isinstance(item, dict) or type(item.get("id")) not in {int, str}:
            raise ValueError("原生实例身份无效")
        identifier = str(item["id"])
        if not identifier or len(identifier) > 96 or identifier in ids:
            raise ValueError("原生实例身份重复或无效")
        ids.add(identifier)
        role = item.get("role")
        if role not in allowed_roles:
            raise ValueError("原生实例包含非目标推理角色")
        name = _text(item.get("job_name"), 96)
        group = (role, name)
        if group in groups:
            raise ValueError("原生实例角色与名称重复")
        groups.add(group)
        if service.get("model") and item.get("model_name") != service["model"]:
            raise ValueError("原生实例的模型与当前服务不匹配")
        endpoints = item.get("endpoints")
        if not isinstance(endpoints, list) or len(endpoints) > 512:
            raise ValueError("原生实例端点清单无效")
        addresses = set()
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                raise ValueError("原生实例端点无效")
            if endpoint.get("headless") is True:
                continue
            port = endpoint.get("business_port")
            if isinstance(port, bool) or not re.fullmatch(r"[0-9]{1,5}", str(port)) or not 1 <= int(port) <= 65535:
                raise ValueError("原生实例端口无效")
            addresses.add(_ip(endpoint.get("ip")))
        active = (item.get("healthy") is True and str(item.get("status", "")).lower() == "active"
                  and item.get("pool") == "available" and (item.get("circuit_breaker") or {}).get("state", "closed") == "closed")
        records.append({"item": item, "role": role, "name": name, "group": group, "addresses": addresses, "active": active})
    return records


def registration_gate(service, nodes, layout, payload, runtime):
    expected, observed, issues = layout["counts"], _counts(0), []
    stamp = now()
    try:
        matched, active = set(), set()
        for record in registered_instances(service, payload):
            role, name, group, addresses = record["role"], record["name"], record["group"], record["addresses"]
            observed[role] += 1
            matched.add(group)
            targets = layout["hosts"] if layout["single"] else (layout["groups"].get(group) or {}).get("hosts")
            if not targets or not targets.issubset(addresses) or not addresses.issubset(layout["hosts"]):
                raise ValueError("原生实例名称或宿主与已部署布局不匹配")
            if record["active"]:
                active.add(group)
            else:
                issues.append(_issue("registration", f"{name} 已注册，尚未达到健康可用状态。", role=role))
        if layout["single"]:
            complete = observed == expected and len(active) == sum(expected.values())
        else:
            for group, target in layout["groups"].items():
                if group not in matched:
                    issues.append(_issue("registration", f"{ROLE_NAMES[group[0]]} 实例 {group[1]} 尚未注册。", node_id=target["node_id"], role=group[0]))
            complete = set(layout["groups"]) == active and observed == expected
        if not complete and not issues:
            issues.append(_issue("registration", "配置目标的推理实例数量尚未齐全。"))
        alive = {(item["role"], item["instance_name"]) for item in runtime["containers"]
                 if item["status"] == "running" and item["health"] in {None, "healthy"}}
        available = active if layout["single"] and runtime["state"] == "ready" else set() if layout["single"] else active & alive
        serving = all(any(group[0] == role for group in available) for role in ROLES if expected[role])
        return {"state": "ready" if complete else "starting", "ready": complete, "serving_ready": serving, "expected": expected,
                "observed": observed, "observed_at": stamp, "issues": issues[:128]}
    except (ValueError, TypeError, AttributeError) as error:
        detail = str(error) if isinstance(error, ValueError) and not isinstance(error, UnicodeError) else "原生实例字段类型或编码无效"
        return {"state": "unknown", "ready": False, "serving_ready": False, "expected": expected, "observed": _counts(),
                "observed_at": stamp, "issues": [_issue("registration", detail)]}


class DockerServiceObserver:
    """One bounded Control reader: no queues, polling loops, or Agent writes.

    A request returns by its overall budget even if a dependency is slow. At
    most eight read-only tasks exist across callers; pending work is never
    admitted to an unbounded executor queue. Worker results never mutate Store.
    """
    def __init__(self, fleet, *, budget=5.0, workers=8, upstream_dir=None):
        self.fleet, self.budget = fleet, budget
        # Native helpers below are pure functions. Capture them during app
        # setup, so a request never waits on the native module import lock.
        self.render_rules = _render_rules(upstream_dir)
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="service-observation")
        self.slots = threading.BoundedSemaphore(workers)
        self.lock, self.closed = threading.Lock(), False
        self.active = {}

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _submit(self, key, operation):
        with self.lock:
            previous = self.active.get(key)
            if previous is not None and not previous.done():
                return previous
            if self.closed or not self.slots.acquire(blocking=False):
                return None
            try:
                future = self.pool.submit(contextvars.copy_context().run, operation)
            except BaseException:
                self.slots.release()
                raise
            self.active[key] = future
        def release(_):
            with self.lock:
                if self.active.get(key) is future:
                    self.active.pop(key, None)
            self.slots.release()
        future.add_done_callback(release)
        return future

    def observe(self, service, nodes, native_observer):
        deadline = time.monotonic() + self.budget
        stamp = now()
        result = {"instances": None, "readiness": None, "metrics_text": "", "observed_at": stamp, "errors": []}
        try:
            layout = _layout(service, nodes, self.render_rules)
        except (ValueError, TypeError, KeyError) as error:
            issue = _issue("runtime", "已部署布局无法核对：" + str(error))
            result.update(runtime={"state": "unknown", "observed_at": None, "containers": [], "issues": [issue]},
                          registration_gate={"state": "unknown", "ready": False, "serving_ready": False, "expected": _counts(0), "observed": _counts(), "observed_at": stamp, "issues": [issue]},
                          readiness_gate={"state": "unknown", "ready": False, "native_ready": False, "serving_ready": False, "expected": _counts(0), "observed": _counts(), "observed_at": stamp, "issues": [issue]})
            return result
        revision = (service["id"], service.get("updated_at"), service.get("deployed_digest"))
        def read_native():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Observation deadline elapsed before the read")
            return native_observer(service, nodes, timeout=min(3.0, remaining))
        native = None
        futures = {}
        def submit_status(node_id):
            def read_status(identifier=node_id):
                remaining = min(3.0, deadline - time.monotonic())
                if remaining <= 0:
                    raise TimeoutError("Observation deadline elapsed before the read")
                path = "/agent/status?" + urlencode({"service_id": service["id"], "node_id": identifier})
                payload = self.fleet.call(nodes[identifier], "GET", path, timeout=min(1.5, remaining),
                                          total_timeout=remaining, max_response_bytes=256 * 1024)
                return project_container_status(payload, identifier)
            return self._submit((*revision, node_id), read_status)
        pending = list(layout["containers"])
        while time.monotonic() < deadline and not self.closed:
            if native is None:
                native = self._submit((*revision, "native"), read_native)
            while pending:
                future = submit_status(pending[0])
                if future is None:
                    break
                futures[pending.pop(0)] = future
            if not pending and native is not None:
                break
            inflight = [f for f in [native, *futures.values()] if f is not None and not f.done()]
            if inflight:
                wait(inflight, timeout=max(0.0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
            else:
                # Capacity belongs to other requests: do not queue or poll it.
                break
        admitted = [f for f in [native, *futures.values()] if f is not None]
        if admitted:
            wait(admitted, timeout=max(0.0, deadline - time.monotonic()))
        if native is not None and native.done() and not native.cancelled():
            try:
                value = native.result()
                if isinstance(value, dict):
                    result.update(value)
            except Exception:
                result["errors"].append(_issue("native", "原生观测读取失败，请刷新后核对。"))
        else:
            result["errors"].append(_issue("native", "原生观测未在本次预算内完成，请稍后刷新。"))
        responses = {}
        for node_id, future in futures.items():
            if future is not None and future.done() and not future.cancelled():
                try:
                    responses[node_id] = future.result()
                except Exception:
                    pass
        for future in admitted:
            if not future.done():
                future.cancel()
        runtime = _runtime_gate(layout, responses)
        registration = registration_gate(service, nodes, layout, result.get("instances"), runtime)
        from .fleet import ready_value
        native_ready = ready_value(result.get("readiness"))
        # Metrics completeness is an observability warning, not evidence that
        # live containers or the registered model capacity have failed.
        critical_errors = [error for error in result.get("errors", [])
                           if not isinstance(error, dict)
                           or error.get("source") != "obs/metrics?type=full&format=prometheus"]
        if runtime["state"] == "degraded":
            state = "degraded"
        elif runtime["state"] == "unknown" or registration["state"] == "unknown" or critical_errors:
            state = "unknown"
        elif runtime["state"] == registration["state"] == "ready" and native_ready:
            state = "ready"
        else:
            state = "starting"
        issues = [*runtime["issues"], *registration["issues"]]
        for error in result.get("errors", []):
            detail = error.get("detail") if isinstance(error, dict) else None
            issues.append(_issue("native", detail[:512] if isinstance(detail, str) else "原生观测尚未完整取得。"))
        if not native_ready and not result.get("errors"):
            issues.append(_issue("native", "原生服务尚未报告就绪。"))
        result.update(runtime=runtime, registration_gate=registration,
                      readiness_gate={"state": state, "ready": state == "ready", "native_ready": native_ready,
                                      "serving_ready": native_ready and registration["serving_ready"],
                                      "expected": registration["expected"], "observed": registration["observed"],
                                      "observed_at": now(), "issues": issues[:128]})
        return result
