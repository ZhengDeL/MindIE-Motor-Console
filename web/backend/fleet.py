"""Authenticated node communication and bounded native Motor observations."""
from __future__ import annotations

import base64
import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx

from .store import now
from .compatibility import CompatibilityError, check_binding
from .version import (ADAPTER_DRIVER, AGENT_PROTOCOL_VERSION, BASELINE_PROFILE_ID, BASELINE_MOTOR_COMMIT,
                      PRODUCT_ID, PRODUCT_HEADER, CONSOLE_PROTOCOL_HEADER, OBSERVATION_NONCE_HEADER)
from .agent_security import agent_tls_context, validate_agent_url
from .agent_metadata_projection import AGENT_META_BYTES, AGENT_RESPONSE_BYTES, project_agent_metadata
from .resource_inventory import InventoryError, project_inventory
from .network_budget import run_network


class FleetError(RuntimeError):
    def __init__(self, message: str, *, operation_id: str | None = None, outcome_unknown: bool = False,
                 receipt: dict | None = None, status_code: int | None = None):
        super().__init__(message)
        self.operation_id, self.outcome_unknown, self.receipt = operation_id, outcome_unknown, receipt
        self.status_code = status_code


CLUSTER_NODES_LIMIT = 500
CLUSTER_NODES_BYTES = 2 * 1024 * 1024
NPU_RESOURCE_KEYS = {"huawei.com/Ascend910", "huawei.com/npu"}


def cluster_nodes_projection(payload: dict) -> dict:
    """Validate the bounded, public Node view; discard unrecognized Agent fields."""
    def text(value, maximum=1024, *, empty=True):
        if not isinstance(value, str) or len(value) > maximum or (not empty and not value):
            raise FleetError("Kubernetes node inventory contains an invalid text field")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise FleetError("Kubernetes node inventory contains invalid Unicode") from exc
        return value

    def sequence(value, maximum):
        if not isinstance(value, list) or len(value) > maximum:
            raise FleetError("Kubernetes node inventory exceeds its list bounds")
        return value

    if not isinstance(payload, dict):
        raise FleetError("Kubernetes node inventory must be a JSON object")
    items = sequence(payload.get("items"), CLUSTER_NODES_LIMIT)
    collected_at = text(payload.get("collected_at"), 64, empty=False)
    try:
        if datetime.fromisoformat(collected_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("missing timezone")
    except ValueError as exc:
        raise FleetError("Kubernetes node inventory has an invalid collection timestamp") from exc
    result = {"items": [], "collected_at": collected_at,
              "cluster_version": text(payload.get("cluster_version", ""))}
    names, uids = set(), set()
    for item in items:
        if not isinstance(item, dict):
            raise FleetError("Kubernetes node inventory contains an invalid node")
        name, uid = text(item.get("name"), 253, empty=False), text(item.get("uid"), 256, empty=False)
        if name in names or uid in uids:
            raise FleetError("Kubernetes node inventory contains duplicate node identities")
        names.add(name)
        uids.add(uid)
        ready, unschedulable = item.get("ready"), item.get("unschedulable")
        if not isinstance(ready, str) or ready not in {"True", "False", "Unknown"} or type(unschedulable) is not bool:
            raise FleetError("Kubernetes node inventory contains an invalid scheduling state")
        labels = item.get("labels")
        if not isinstance(labels, dict) or len(labels) > 128:
            raise FleetError("Kubernetes node inventory exceeds its label bounds")
        labels = {text(k, 253, empty=False): text(v) for k, v in labels.items()}
        roles = [text(v, 253, empty=False) for v in sequence(item.get("roles"), 16)]
        ips = []
        for value in sequence(item.get("internal_ips"), 16):
            try:
                address = str(ipaddress.ip_address(text(value, 64, empty=False)))
            except ValueError as exc:
                raise FleetError("Kubernetes node inventory contains an invalid InternalIP") from exc
            if address not in ips:
                ips.append(address)
        taints = []
        for value in sequence(item.get("taints"), 64):
            if (not isinstance(value, dict) or not isinstance(value.get("effect"), str)
                    or value["effect"] not in {"NoSchedule", "PreferNoSchedule", "NoExecute"}):
                raise FleetError("Kubernetes node inventory contains an invalid taint")
            taints.append({"key": text(value.get("key"), 253, empty=False),
                           "value": text(value.get("value", "")), "effect": value["effect"]})
        resources = item.get("resources")
        if not isinstance(resources, dict):
            raise FleetError("Kubernetes node inventory contains invalid resources")
        projected_resources = {}
        for field in ("capacity", "allocatable"):
            quantities = resources.get(field)
            if not isinstance(quantities, dict):
                raise FleetError("Kubernetes node inventory contains invalid resource quantities")
            projected_resources[field] = {}
            for key in sorted(NPU_RESOURCE_KEYS & quantities.keys()):
                quantity = text(quantities[key], 128, empty=False)
                if not re.fullmatch(r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+|[EPTGMK]i|[EPTGMkmun])?", quantity):
                    raise FleetError("Kubernetes node inventory contains an invalid NPU quantity")
                projected_resources[field][key] = quantity
        node = {"name": name, "uid": uid, "roles": roles, "internal_ips": ips,
                "ready": ready, "unschedulable": unschedulable, "labels": labels,
                "taints": taints, "resources": projected_resources}
        for field in ("kubelet_version", "container_runtime", "architecture", "os"):
            node[field] = text(item.get(field, ""))
        result["items"].append(node)
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > CLUSTER_NODES_BYTES:
        raise FleetError("Kubernetes node inventory exceeds the 2 MiB response limit")
    return result


class Secrets:
    def __init__(self, seed: str):
        self.box = Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest()))

    def encrypt(self, value: str) -> str:
        return self.box.encrypt(value.encode()).decode() if value else ""

    def decrypt(self, value: str) -> str:
        if not value: return ""
        try: return self.box.decrypt(value.encode()).decode()
        except InvalidToken as error: raise FleetError("节点凭据无法解密，请核对 MOTOR_SECRET_KEY 或重新录入凭据") from error


def public_node(value: dict) -> dict:
    return {key: item for key, item in value.items() if not key.startswith("_") and key != "agent_token"}


class Fleet:
    def __init__(self, secrets: Secrets):
        self.secrets = secrets

    def call(self, node: dict, method: str, path: str, body: dict | None = None, *, timeout: float = 90,
             max_response_bytes: int | None = None, total_timeout: float | None = None):
        # The budget spans negotiation, headers, every body read and receipt
        # reconciliation. HTTPX's idle timeout alone does not bound these phases.
        for value in (timeout, total_timeout if total_timeout is not None else timeout):
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= 3600:
                raise FleetError("Agent request time budget is invalid")
        deadline = time.monotonic() + (total_timeout if total_timeout is not None else timeout)
        image_action = {"/agent/images/pull": "image_pull", "/agent/images/remove": "image_remove"}.get(path)
        operation_id = (body or {}).get("operation_id") if method.upper() == "POST" and (image_action or path in {
            "/agent/deploy", "/agent/restart", "/agent/stop", "/agent/remove"}) else None
        if operation_id is not None and (not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_id)):
            raise FleetError("Agent operation ID is invalid")
        try:
            options = {"timeout": timeout, "max_response_bytes": max_response_bytes}
            if deadline is not None:
                options["deadline"] = deadline
            result = self._call_once(node, method, path, body, **options)
        except FleetError as error:
            if not operation_id or not error.outcome_unknown:
                raise
            # An HTTP write is never retried. Only a durable receipt is read.
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FleetError("Agent receipt must be reconciled after the request deadline")
                result = self._call_once(node, "GET", "/agent/operations/" + operation_id, timeout=min(timeout, 10),
                                         max_response_bytes=256 * 1024, deadline=deadline)
            except FleetError:
                raise FleetError("节点执行结果尚未确认；已保留操作标识，请查询回执后再决定后续操作",
                                 operation_id=operation_id, outcome_unknown=True) from error
        if operation_id:
            expected_node = (body or {}).get("node_id") or ((body or {}).get("node") or {}).get("id") or ((body or {}).get("placement") or {}).get("node_id")
            if (not isinstance(result, dict) or result.get("operation_id") != operation_id
                    or result.get("action") != (image_action or path.rsplit("/", 1)[-1])
                    or result.get("service_id") != ("" if image_action else (body or {}).get("service_id"))
                    or expected_node is not None and result.get("node_id") != expected_node
                    or result.get("operation_status") not in {"completed", "failed", "accepted", "running", "interrupted"}):
                raise FleetError("Agent 操作回执无效；不能确认执行结果", operation_id=operation_id, outcome_unknown=True)
            state = result["operation_status"]
            if image_action:
                return result  # Accepted image tasks are observed asynchronously.
            if state != "completed":
                raise FleetError("节点操作失败" if state == "failed" else "节点操作仍在执行或结果中断；请查询持久回执，禁止盲目重放",
                    operation_id=operation_id, outcome_unknown=state != "failed" or result.get("resources_may_exist") is True,
                    receipt=result)
        return result

    def _call_once(self, node: dict, method: str, path: str, body: dict | None = None, *, timeout: float = 90,
                   max_response_bytes: int | None = None, deadline: float | None = None):
        if not node.get("agent_url"): raise FleetError("节点尚未设置 Agent 地址")
        target = path
        if method.upper() == "GET" and path == "/agent/inventory":
            try:
                raw_address = node.get("address")
                if not isinstance(raw_address, str):
                    raise ValueError("Missing model deployment address")
                address = ipaddress.IPv4Address(raw_address)
                if address.is_unspecified or address.is_multicast or address.is_link_local:
                    raise ValueError("Invalid model deployment address")
            except ValueError as error:
                raise FleetError("节点模型部署 IPv4 地址无效；未请求资源观测") from error
            # Keep path unchanged for nonce, response projection and byte limits.
            # Older Agents safely ignore this optional query parameter.
            target += "?node_address=" + str(address)
        token = self.secrets.decrypt(node.get("_token", ""))
        if not token: raise FleetError("节点未配置 Agent 访问凭据")
        from .observability import register_secret
        register_secret(token)
        if max_response_bytes is not None and (type(max_response_bytes) is not int or max_response_bytes < 1):
            raise FleetError("Agent response limit must be a positive integer")
        route = path.split("?", 1)[0]
        default_limit = (8 * 1024 * 1024 if route.endswith("/logs") else
                         4 * 1024 * 1024 if route in {"/agent/images", "/agent/images/inspect"} else
                         2 * 1024 * 1024)
        limit = min(max_response_bytes if max_response_bytes is not None else default_limit, AGENT_RESPONSE_BYTES)
        if path == "/agent/meta":
            limit = min(limit, AGENT_META_BYTES)

        deadline = deadline if deadline is not None else time.monotonic() + timeout
        write_started = False
        mtls_policy = os.getenv("MOTOR_AGENT_MTLS_REQUIRED", "false").lower()
        if mtls_policy not in {"true", "false"}:
            raise FleetError("MOTOR_AGENT_MTLS_REQUIRED must be true or false")
        require_peer_certificate = mtls_policy == "true"

        def verify_transport(metadata):
            security = metadata.get("transport_security") or {}
            if require_peer_certificate and (security.get("tls_enabled") is not True
                                            or security.get("client_certificate_required") is not True):
                raise FleetError("Agent 未声明强制验证客户端证书；控制面 mTLS 策略拒绝接入，未提交变更", status_code=409)

        async def bounded(client, operation, target, *, payload=None, maximum=limit):
            options = {}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Agent request deadline expired")
            options["timeout"] = min(timeout, remaining)
            async with client.stream(operation, base + target, json=payload, headers=headers, **options) as streamed:
                if streamed.headers.get(PRODUCT_HEADER) != PRODUCT_ID:
                    raise FleetError("Agent response has no matching Qinghuan platform product identity", outcome_unknown=operation.upper() == "POST")
                if streamed.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
                    raise FleetError("Agent responses must use identity encoding for bounded reads", outcome_unknown=operation.upper() == "POST")
                chunks, size = [], 0
                # MockTransport may return an already materialized response;
                # real network streams are read without invoking decompression.
                def add(chunk):
                    nonlocal size
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Agent request deadline expired while reading response")
                    size += len(chunk)
                    if size > maximum:
                        if target == "/agent/kubernetes/nodes" and maximum == CLUSTER_NODES_BYTES:
                            raise FleetError("Kubernetes node inventory exceeds the 2 MiB response limit")
                        raise FleetError("Agent response exceeds its bounded size limit", outcome_unknown=operation.upper() == "POST")
                    chunks.append(chunk)
                if streamed.is_stream_consumed:
                    add(streamed.content)
                else:
                    async for chunk in streamed.aiter_raw():
                        add(chunk)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Agent request deadline expired after reading response")
                projected_headers = {key: value for key, value in streamed.headers.items()
                                     if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}}
                return httpx.Response(streamed.status_code, content=b"".join(chunks), headers=projected_headers)
        def interpret(response, request_elapsed, response_received):
            if not response.is_success:
                try:
                    error_payload = response.json()
                    detail = error_payload.get("detail", response.text[:400]) if isinstance(error_payload, dict) else response.text[:400]
                except ValueError:
                    detail = response.text[:400]
                raise FleetError(f"{node['name']}: {response.status_code} {detail}",
                    status_code=response.status_code,
                    outcome_unknown=method.upper() == "POST" and response.status_code >= 500)
            payload = response.json()
            if path == "/agent/meta":
                metadata = project_agent_metadata(payload)
                verify_transport(metadata)
                return metadata
            if path == "/agent/inventory":
                from .resource_freshness import receive_inventory
                payload = receive_inventory(payload, nonce=inventory_nonce, received_at=response_received,
                                            round_trip_ns=request_elapsed)
                return project_inventory(payload, received_at=response_received)
            return payload

        async def perform():
            nonlocal write_started
            from .resource_freshness import elapsed_ns
            # Parsing a replacement CA/key can involve blocking local crypto or
            # filesystem APIs. Keep it off the I/O loop and inside the same
            # cancellable budget, using its fixed-size helper executor.
            trust = await asyncio.to_thread(agent_tls_context, node["_agent_ca_pem"]) \
                if node.get("_agent_ca_pem") else await asyncio.to_thread(agent_tls_context)
            async with httpx.AsyncClient(timeout=timeout, verify=trust, trust_env=False, follow_redirects=False,
                                         limits=httpx.Limits(max_connections=2, max_keepalive_connections=0)) as client:
                mutating_method = method.upper() not in {"GET", "HEAD", "OPTIONS"}
                if mutating_method or require_peer_certificate and path != "/agent/meta":
                    metadata_response = await bounded(client, "GET", "/agent/meta", maximum=AGENT_META_BYTES)
                    if not metadata_response.is_success:
                        raise FleetError(node["name"] + ": Agent protocol negotiation failed before the write operation")
                    metadata = project_agent_metadata(metadata_response.json())
                    verify_transport(metadata)
                if mutating_method:
                    mutates = path in {"/agent/deploy", "/agent/restart", "/agent/stop", "/agent/remove",
                                       "/agent/images/pull", "/agent/images/remove", "/agent/images/inspect",
                                       "/agent/kubernetes/deploy", "/agent/kubernetes/stop",
                                       "/agent/kubernetes/restart", "/agent/kubernetes/remove"} \
                        or path == "/agent/kubernetes/scale" and (body or {}).get("dry_run") is not True
                    if mutates and (metadata.get("read_only") is not False
                                    or (metadata.get("execution_capabilities") or {}).get("mutations") is not True):
                        raise FleetError(node["name"] + ": Agent 处于只读模式或尚未声明管理能力；未提交资源变更",
                                         status_code=403)
                    image_action = path in {"/agent/images/pull", "/agent/images/remove"}
                    if image_action and (metadata.get("execution_capabilities") or {}).get("image_operations") is not True:
                        raise FleetError(node["name"] + ": Agent does not support persistent image operations; no mutation was submitted", status_code=409)
                    if path in {"/agent/deploy", "/agent/restart", "/agent/stop", "/agent/remove"} and (body or {}).get("operation_id"):
                        capabilities = metadata.get("execution_capabilities") or {}
                        if metadata.get("operation_protocol") != 1 or not all(capabilities.get(name) is True for name in (
                                "operation_receipts", "resource_ledger", "resource_admission")):
                            raise FleetError(node["name"] + ": Agent 需要升级资源准入、账本和回执协议；未提交节点写操作")
                    if (type(metadata.get("agent_protocol")) is not int or metadata["agent_protocol"] != AGENT_PROTOCOL_VERSION
                            or not isinstance(metadata.get("supported_control_protocols"), list)
                            or not any(v == AGENT_PROTOCOL_VERSION for v in metadata["supported_control_protocols"])):
                        raise FleetError(node["name"] + ": incompatible Agent protocol; no write was submitted")
                    profile = metadata.get("motor_profile", {})
                    desired = (body or {}).get("motor_profile") or BASELINE_PROFILE_ID
                    if not image_action and (not isinstance(desired, str) or not isinstance(profile, dict) or profile.get("profile_id") != desired
                            or not isinstance(profile.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", profile["commit"])
                            or profile.get("driver") != ADAPTER_DRIVER
                            or (desired == BASELINE_PROFILE_ID and profile.get("commit") != BASELINE_MOTOR_COMMIT)
                            or (desired != BASELINE_PROFILE_ID and not desired.endswith("-" + str(profile.get("commit"))))):
                        raise FleetError(node["name"] + ": Motor profile binding differs; no write was submitted")
                request_started = elapsed_ns()
                write_started = method.upper() not in {"GET", "HEAD", "OPTIONS"}
                response = await bounded(client, method, target, payload=body)
                # JSON decoding and inventory validation are also part of the
                # caller's total budget. Their byte limits bound input size;
                # the fixed helper pool keeps them off the socket I/O loop.
                return await asyncio.to_thread(interpret, response, elapsed_ns() - request_started,
                                               datetime.now(timezone.utc))

        try:
            # Validate before constructing any network client. This policy also
            # applies to previously saved nodes after a production upgrade.
            base = validate_agent_url(node["agent_url"])
            headers = {"Authorization": "Bearer " + token, CONSOLE_PROTOCOL_HEADER: str(AGENT_PROTOCOL_VERSION),
                           PRODUCT_HEADER: PRODUCT_ID,
                           "Accept-Encoding": "identity"}
            inventory_nonce = secrets.token_hex(16) if path == "/agent/inventory" else None
            if inventory_nonce is not None:
                headers.update({OBSERVATION_NONCE_HEADER: inventory_nonce, "Cache-Control": "no-cache, no-store"})
            headers["Cache-Control"] = "no-cache, no-store"
            from .observability import context_fields
            request_id = context_fields().get("request_id")
            if request_id:
                headers["X-Request-ID"] = request_id
            return run_network(perform, deadline)
        except InventoryError:
            raise
        except TimeoutError as error:
            raise FleetError(f"{node['name']}: Agent 请求超过总时间预算",
                             outcome_unknown=write_started) from error
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, RecursionError) as error:
            raise FleetError(f"{node['name']}: Agent 连接失败 ({type(error).__name__})",
                outcome_unknown=write_started) from error

    def cluster_nodes(self, node: dict) -> dict:
        return cluster_nodes_projection(self.call(node, "GET", "/agent/kubernetes/nodes", timeout=90,
                                                   max_response_bytes=CLUSTER_NODES_BYTES))


def endpoint(service: dict, nodes: dict[str, dict], plane: str = "infer", *, deployed: bool = False) -> str:
    if deployed:
        from .deployment_view import runtime_service_view
        service = runtime_service_view(service)
    # A Pod's Coordinator port is not a host port. An external Kubernetes entry
    # needs separately verified Service ownership and exposure; never infer it
    # from the execution Agent's address or trust a saved, unverified URL.
    if service.get("runtime") == "kubernetes":
        return ""
    placement = next((item for item in service.get("placements", []) if isinstance(item, dict)
                      and item.get("role") in ("coordinator_controller", "coordinator,controller", "coordinator", "single_container")), None)
    if not placement or placement.get("node_id") not in nodes: return ""
    host = nodes[placement["node_id"]]["address"]
    config = service["user_config"]
    coordinator = config.get("motor_coordinator_config") or {}
    if not isinstance(coordinator, dict): raise FleetError("Motor Coordinator configuration must be an object")
    api = coordinator.get("api_config") or {}
    if not isinstance(api, dict): raise FleetError("Motor Coordinator api_config must be an object")
    tls_name = "infer_tls_config" if plane == "infer" else "mgmt_tls_config"
    deploy = config.get("motor_deploy_config") or {}
    if not isinstance(deploy, dict): raise FleetError("Motor deployment configuration must be an object")
    tls_config = deploy.get("tls_config") or {}
    if not isinstance(tls_config, dict): raise FleetError("Motor deployment tls_config must be an object")
    tls = tls_config.get(tls_name, coordinator.get(tls_name) or {})
    if not isinstance(tls, dict): raise FleetError("Motor TLS configuration must be an object")
    scheme = "https" if tls.get("enable_tls") is True else "http"
    raw_port = api.get({"infer": "coordinator_api_infer_port", "mgmt": "coordinator_api_mgmt_port", "obs": "coordinator_obs_port"}[plane], {"infer": 1025, "mgmt": 1026, "obs": 1027}[plane])
    if type(raw_port) not in {int, str} or isinstance(raw_port, str) and (not raw_port.isascii() or not raw_port.isdecimal()):
        raise FleetError("Motor 服务端口必须是明确的整数")
    port = int(raw_port)
    if not 1 <= port <= 65535: raise FleetError("Motor 服务端口无效")
    try: parsed_host = ipaddress.ip_address(host)
    except ValueError as error: raise FleetError("Motor 节点地址必须为明确的 IP 地址") from error
    if parsed_host.is_unspecified or parsed_host.is_multicast or parsed_host.is_link_local:
        raise FleetError("Motor 服务地址不能使用未指定、组播或链路本地地址")
    formatted_host = f"[{host}]" if parsed_host.version == 6 else host
    return f"{scheme}://{formatted_host}:{port}"


def _motor_headers(service: dict, plane: str) -> dict[str, str]:
    coordinator = service["user_config"].get("motor_coordinator_config") or {}
    result = {"Content-Type": "application/json"}
    if plane == "infer":
        key = coordinator.get("api_key_config") or {}
        if not isinstance(key, dict): raise FleetError("Motor inference API-key configuration must be an object")
        if key.get("enable_api_key"):
            client_key = os.getenv("MOTOR_INFERENCE_API_KEY", "")
            if not client_key: raise FleetError("推理鉴权已启用；请配置 MOTOR_INFERENCE_API_KEY 客户端凭据，不能将服务端校验值当作客户端 Key")
            from .observability import register_secret
            register_secret(client_key)
            result[key.get("header_name", "Authorization")] = key.get("key_prefix", "Bearer ") + client_key
    else:
        key = coordinator.get("mgmt_api_key_config") or {}
        if not isinstance(key, dict): raise FleetError("Motor management API-key configuration must be an object")
        if key.get("enable_api_key"):
            from .motor_http import management_headers
            return management_headers()
    return result


def motor_request(service: dict, nodes: dict[str, dict], plane: str, path: str, *, method: str = "GET", body: dict | None = None, timeout: float = 8,
                  max_response_bytes: int | None = None):
    from .motor_http import request_motor
    return request_motor(service, nodes, plane, path, method=method, body=body, timeout=timeout,
                         max_response_bytes=max_response_bytes)


def ready_value(payload) -> bool:
    if not isinstance(payload, dict): return False
    if payload.get("ready") is True or payload.get("is_ready") is True: return True
    if isinstance(payload.get("data"), dict): return ready_value(payload["data"])
    return False


def observations(service: dict, nodes: dict[str, dict], *, timeout: float = 8) -> dict:
    result = {"instances": None, "metrics_text": "", "readiness": None, "errors": [], "observed_at": now()}
    deadline = time.monotonic() + timeout
    for field, plane, path in (("instances", "obs", "/instances"), ("readiness", "mgmt", "/readiness"), ("metrics_text", "obs", "/metrics?type=full&format=prometheus")):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FleetError("原生观测读取超过本次时间预算")
            result[field] = motor_request(service, nodes, plane, path, timeout=remaining,
                                           max_response_bytes=2 * 1024 * 1024)
        except FleetError as error: result["errors"].append({"source": plane + path, "detail": str(error)})
    return result
