"""Control-side capacity gates over native registrations and Agent UID proofs.

Native Coordinator readiness is retained unchanged. A native ready response is
not evidence that all desired P/D instances exist. Pod endpoint IPs are not
matched to Pod UIDs by this protocol and are never advertised as such proof.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import re

from .deployment_view import runtime_service_view
from .fleet import ready_value
from .service_health import registered_instances

ROLES = ("encode", "prefill", "decode", "union")


def _integer(value, maximum=256):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("原生角色副本数或 Pod 数无效")
    return value


def _stamp(value, captured):
    if not isinstance(value, str):
        raise ValueError("Kubernetes 观测缺少来源时间")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or not -2 <= (captured - parsed).total_seconds() <= 45:
        raise ValueError("Kubernetes 观测过期或来源时钟异常")


def _uid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}", value):
        raise ValueError("Kubernetes 资源 UID 无效")
    return value


def _targets(service, observation, status, captured):
    namespace = "motor-console-" + hashlib.sha256(service["id"].encode()).hexdigest()[:24]
    _stamp(observation.get("observed_at"), captured)
    if not isinstance(status, dict) or status.get("namespace") != namespace:
        raise ValueError("Kubernetes 受管命名空间未核实")
    _stamp(status.get("observed_at"), captured)
    resources = status.get("resources")
    if not isinstance(resources, list) or len(resources) > 256:
        raise ValueError("Kubernetes 受管资源 UID 清单不完整")
    tracked = set()
    for ref in resources:
        if not isinstance(ref, dict):
            raise ValueError("Kubernetes 受管资源引用无效")
        uid = _uid(ref.get("uid"))
        if ref.get("kind") == "InferServiceSet":
            if ref.get("namespace") != namespace or ref.get("apiVersion") != "mindcluster.huawei.com/v1":
                raise ValueError("原生 InferServiceSet 超出受管范围")
            tracked.add((ref.get("apiVersion"), ref.get("kind"), ref.get("name"), ref.get("namespace"), uid))
    runtime = observation.get("runtime_readiness")
    if not isinstance(runtime, dict) or type(runtime.get("ready")) is not bool or type(status.get("ready")) is not bool:
        raise ValueError("Kubernetes 工作负载就绪观测缺失")
    if any(not isinstance(value, list) for value in (runtime.get("pending_deletions"), status.get("pending_deletions"))):
        raise ValueError("Kubernetes 删除收敛观测缺失")
    pods = status.get("items")
    if not isinstance(pods, list) or len(pods) > 512:
        raise ValueError("Kubernetes Pod UID 清单不完整")
    ids, pod_counts, ready_counts = set(), {r: 0 for r in ROLES}, {r: 0 for r in ROLES}
    for pod in pods:
        if not isinstance(pod, dict) or type(pod.get("ready")) is not bool:
            raise ValueError("Kubernetes Pod 状态格式无效")
        uid = _uid(pod.get("uid"))
        if uid in ids:
            raise ValueError("Kubernetes Pod UID 重复")
        ids.add(uid)
        if pod.get("phase") in {"Succeeded", "Failed"} or pod.get("deleting") is True:
            continue
        role = pod.get("role")
        if role in ROLES:
            pod_counts[role] += 1
            ready_counts[role] += pod["ready"] is True
    deploy = service["user_config"].get("motor_deploy_config") or {}
    configured = {role: _integer(deploy.get(field, 0)) for role, field in
                  (("encode", "e_instances_num"), ("prefill", "p_instances_num"), ("decode", "d_instances_num"), ("union", "hybrid_instances_num"))}
    mode = deploy.get("deploy_mode", "infer_service_set")
    converged = (runtime["ready"] and status["ready"] and not runtime["pending_deletions"] and not status["pending_deletions"])
    if not tracked:
        if mode == "infer_service_set":
            raise ValueError("未取得与已执行布局匹配的 InferServiceSet UID")
        # Non-ISS modes do not expose live per-role targets in this protocol.
        # Preserve configured counts, without pretending external HPA targets
        # or Pod-to-registered-endpoint identity have been individually proved.
        return configured, configured, "immutable_deployed_config", converged, []
    if len(tracked) != 1:
        raise ValueError("多个原生 InferServiceSet 的目标归属存在歧义")
    first, second = runtime.get("role_observations"), status.get("role_observations")
    if not isinstance(first, list) or not isinstance(second, list) or len(first) > 32 or len(second) > 32:
        raise ValueError("原生角色目标观测缺失或超界")
    def project(rows):
        result = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("resource"), dict):
                raise ValueError("原生角色目标引用无效")
            ref = row["resource"]
            identity = (ref.get("apiVersion"), ref.get("kind"), ref.get("name"), ref.get("namespace"), _uid(ref.get("uid")))
            if identity not in tracked:
                raise ValueError("原生角色目标的 InferServiceSet UID 与受管记录不一致")
            role = row.get("role")
            if not isinstance(role, str) or role in result:
                raise ValueError("原生角色目标缺失或重复")
            if row.get("observable") is not True or row.get("source") != "native-role-template-and-pod-ROLE":
                raise ValueError("原生角色布局尚不能无歧义核对")
            if type(row.get("converged")) is not bool:
                raise ValueError("原生角色收敛状态无效")
            result[role] = tuple(_integer(row.get(field), 4096) for field in
                                 ("desired_instances", "pods_per_instance", "desired_pods", "active_pods", "ready_pods")) + (row["converged"],)
        return result
    a, b = project(first), project(second)
    if a != b:
        raise ValueError("读取期间原生角色目标或 Pod 数发生变化，请重新核对")
    expected, details, multipliers = {}, [], set()
    for role in ROLES:
        if role not in a:
            if configured[role] or pod_counts[role] or ready_counts[role]:
                raise ValueError("已配置或已观察到的原生角色目标清单不完整")
            expected[role] = 0
            details.append({"role": role, "effective_instances": 0, "absent_non_target": True})
            continue
        replicas, per_instance, desired_pods, active_pods, ready_pods, role_converged = a[role]
        if per_instance <= 0 or desired_pods % per_instance:
            raise ValueError("原生每实例 Pod 布局存在歧义")
        effective = desired_pods // per_instance
        _integer(effective)
        if replicas:
            if effective % replicas:
                raise ValueError("原生集合与角色副本倍数存在歧义")
            multipliers.add(effective // replicas)
        elif effective:
            raise ValueError("零角色副本与原生 Pod 目标不一致")
        expected[role] = effective
        if active_pods != pod_counts[role] or ready_pods != ready_counts[role]:
            raise ValueError("原生角色计数与本次受管 Pod UID 清单不一致")
        converged = (converged and role_converged and active_pods == ready_pods == desired_pods)
        details.append({"role": role, "role_replicas": replicas, "pods_per_instance": per_instance,
                        "desired_pods": desired_pods, "effective_instances": effective})
    if len(multipliers) > 1 or runtime.get("role_converged") is not True or status.get("role_converged") is not True:
        if len(multipliers) > 1:
            raise ValueError("原生集合副本倍数在不同角色之间不一致")
        converged = False
    return configured, expected, "native_owned_role_target", converged, details


def kubernetes_observation_gates(service, observation, status, *, captured=None):
    """Add capacity gates; never mutate the native readiness or user JSON."""
    result = {**observation}
    captured = captured or datetime.now(timezone.utc)
    native = copy.deepcopy(observation.get("native_readiness"))
    result["native_readiness"] = native
    configured, expected, observed = ({role: None for role in ROLES} for _ in range(3))
    source, issues, details = "unverified", [], []
    state, complete, serving, native_ready = "unknown", False, False, ready_value(native)
    try:
        if not isinstance(native, dict):
            raise ValueError("原生就绪观测缺失，不能用平台状态替代")
        current = runtime_service_view(service)
        config = current["user_config"]
        configured, expected, source, runtime_ready, details = _targets(current, observation, status, captured)
        models = {}
        for role in ROLES:
            if not expected[role]:
                continue
            engine = (config.get("motor_engine_" + role + "_config") or {}).get("engine_config") or {}
            name = engine.get("served_model_name") or engine.get("served-model-name")
            if name is not None:
                if not isinstance(name, str) or not 1 <= len(name) <= 256:
                    raise ValueError("已部署模型标识无效")
                models[role] = name
        names = set(models.values())
        fallback = next(iter(names)) if len(names) == 1 else current.get("model") if not current.get("has_unapplied_changes") else None
        for role in ROLES:
            if expected[role] and role not in models:
                if not isinstance(fallback, str) or not fallback or current.get("has_unapplied_changes") and not names:
                    raise ValueError("已部署角色的模型标识尚不能核实")
                models[role] = fallback
        # EPD may explicitly name its encoder differently. Validate each native
        # role against that role's immutable engine configuration, never a draft
        # display alias. Docker retains the shared parser's original model rule.
        records = registered_instances({**current, "model": None}, observation.get("instances"), allowed_roles=ROLES)
        if any(record["item"].get("model_name") != models.get(record["role"]) for record in records):
            raise ValueError("原生实例的模型与该角色已部署配置不匹配")
        namespace = "motor-console-" + hashlib.sha256(current["id"].encode()).hexdigest()[:24]
        if any(not record["name"].startswith(namespace + "-") or not record["addresses"] for record in records):
            raise ValueError("原生实例名称或端点不属于已部署服务的注册约定")
        observed = {role: sum(record["role"] == role for record in records) for role in ROLES}
        complete = observed == expected and all(record["active"] for record in records) and sum(expected.values()) > 0
        serving = (runtime_ready and all(any(record["role"] == role and record["active"] for record in records)
                                         for role in ROLES if expected[role]) and sum(expected.values()) > 0)
        if not complete:
            issues.append({"source": "registration", "detail": "原生注册实例尚未达到当前受管角色目标的完整健康可用数量。"})
        if not runtime_ready:
            issues.append({"source": "runtime", "detail": "受管资源 UID、角色目标或 Pod 就绪数量尚未收敛。"})
        critical = [error for error in observation.get("errors", []) if not isinstance(error, dict)
                    or error.get("source") != "obs/metrics?type=full&format=prometheus"]
        state = "unknown" if critical else "ready" if complete and runtime_ready and native_ready else "starting"
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) and not isinstance(error, UnicodeError) else "已部署修订或 Kubernetes 注册容量证据尚不能完整核对。"
        issues.append({"source": "registration", "detail": detail})
        complete, serving = False, False
    registration = {"state": "unknown" if state == "unknown" else "ready" if complete else "starting", "ready": complete and state != "unknown",
                    "serving_ready": serving, "configured": configured, "expected": expected, "expected_source": source,
                    "observed": observed, "observed_at": captured.isoformat(), "target_layout": details,
                    "issues": issues[:128], "endpoint_pod_uid_binding": "not_available_in_this_protocol"}
    result["registration_gate"] = registration
    result["readiness_gate"] = {**registration, "state": state, "ready": state == "ready", "native_ready": native_ready,
                                 "serving_ready": state == "ready" and serving}
    if isinstance(observation.get("readiness"), dict):
        result["readiness"] = {**observation["readiness"], "ready": state == "ready"}
    return result
