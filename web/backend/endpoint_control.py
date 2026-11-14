"""Read-only, short-lived projections of UID-verified Kubernetes exposure.

``create_endpoint_router(store, fleet, *, runtime_view=..., mode='control')`` is composed with
the application's existing authentication dependency. No observed URL is used
for an outbound request. LoadBalancer origins are display-only; NodePort origins
require the intersection of registered addresses and verified cluster Node IPs.
Neither projection changes the original Motor JSON nor the main database.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Request, Response

from .deployment_view import runtime_service_view
from .kubernetes_endpoints import (MAX_RESPONSE_BYTES, EndpointDiscoveryError, _name, _port, _uid,
                                   native_config_digest, public_hostname, public_ip)
from .node_configuration import execution_node

_KINDS = {"inference", "management", "observability"}
_STATUSES = {"ready", "partial", "unpublished", "unavailable"}
_INACTIVE = {"draft", "stopped", "stopping", "removing", "removed"}


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise EndpointDiscoveryError("Missing observation timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise EndpointDiscoveryError("Observation timestamps must be explicit UTC")
    return result


def _empty(identifier, runtime, warning, *, status="unavailable"):
    return {"service_id": identifier, "runtime": runtime, "observed_at": None, "expires_at": None,
            "source": "kubernetes_services", "status": status, "items": [], "warnings": [warning]}


def _scheme(config, kind):
    coordinator = config.get("motor_coordinator_config") or {}
    deployment = config.get("motor_deploy_config") or {}
    if not isinstance(coordinator, dict) or not isinstance(deployment, dict):
        raise EndpointDiscoveryError("Invalid immutable native configuration")
    settings = deployment.get("tls_config") or {}
    if not isinstance(settings, dict):
        raise EndpointDiscoveryError("Invalid native TLS settings")
    key = "infer_tls_config" if kind == "inference" else "mgmt_tls_config"
    tls = settings.get(key, coordinator.get(key) or {})
    if not isinstance(tls, dict) or type(tls.get("enable_tls", False)) is not bool:
        raise EndpointDiscoveryError("Invalid native TLS flag")
    return "https" if tls.get("enable_tls") is True else "http"


def _origin(scheme, host, port):
    # All hosts originate in validated IP/ASCII-DNS fields, never raw URLs.
    formatted = "[" + host + "]" if ":" in host else host
    return scheme + "://" + formatted + ":" + str(_port(port))


def project_endpoints(raw, service, registered_nodes, *, request_started, captured=None):
    """Validate an Agent proof and produce a bounded display-only API schema.

    ``request_started`` comes from the Control clock, so a slow Agent cannot
    obtain a fresh 15-second lease after a nearly expired observation returns.
    The Agent's own timestamps can shorten, but never extend, this lifetime.
    """
    captured = captured or datetime.now(timezone.utc)
    identifier = service["id"]
    if not isinstance(raw, dict) or len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
        raise EndpointDiscoveryError("Endpoint snapshot exceeds supported bounds")
    key = hashlib.sha256(identifier.encode()).hexdigest()[:24]
    namespace = "motor-console-" + key
    if (raw.get("schema_version") != 1 or raw.get("source") != "kubernetes_services"
            or raw.get("service_id") != identifier or raw.get("namespace") != namespace
            or raw.get("status") not in _STATUSES):
        raise EndpointDiscoveryError("Endpoint snapshot identity changed")
    _uid(raw.get("namespace_uid"))
    observed, expires = _stamp(raw.get("observed_at")), _stamp(raw.get("expires_at"))
    if (observed > captured + timedelta(seconds=2) or observed < request_started - timedelta(seconds=2)
            or not observed < expires <= observed + timedelta(seconds=15) or expires <= captured):
        raise EndpointDiscoveryError("Endpoint observation is stale or has invalid time bounds")
    deadline = min(request_started + timedelta(seconds=15), expires)
    if deadline <= captured:
        raise EndpointDiscoveryError("Endpoint observation expired during collection")
    items, warnings = raw.get("items"), raw.get("warnings")
    if (not isinstance(items, list) or len(items) > 32 or not isinstance(warnings, list) or len(warnings) > 16
            or any(not isinstance(value, str) or len(value) > 300
                   or any(ord(c) < 32 for c in value) for value in warnings)):
        raise EndpointDiscoveryError("Endpoint snapshot has invalid collections")
    warnings = list(dict.fromkeys(warnings))
    result = {"service_id": identifier, "runtime": "kubernetes", "source": "kubernetes_services",
              "observed_at": request_started.isoformat(), "expires_at": deadline.isoformat(),
              "status": raw["status"], "items": [], "warnings": warnings}
    if raw["status"] in {"unpublished", "unavailable"}:
        if items:
            raise EndpointDiscoveryError("Unavailable snapshot contains exposure candidates")
        return result
    if raw.get("native_config_digest") != native_config_digest(service.get("user_config")):
        raise EndpointDiscoveryError("Observed native revision differs from the executed revision")
    if not isinstance(registered_nodes, list) or len(registered_nodes) > 500:
        raise EndpointDiscoveryError("Registered node bound exceeded")
    allowed_ips = set()
    for node in registered_nodes:
        if isinstance(node, dict):
            try:
                allowed_ips.add(public_ip(node.get("address")))
            except EndpointDiscoveryError:
                pass
    identities, ip_owners, seen = {}, {}, set()
    for item in items:
        if (not isinstance(item, dict) or item.get("kind") not in _KINDS
                or item.get("type") not in {"NodePort", "LoadBalancer", "ClusterIP"}
                or item.get("owner_chain_verified") is not True or type(item.get("ready")) is not bool
                or item.get("external_traffic_policy") not in {"Cluster", "Local"}):
            raise EndpointDiscoveryError("Exposure has invalid ownership or type")
        service_name, service_uid = _name(item.get("service_name")), _uid(item.get("service_uid"))
        _uid(item.get("owner_root_uid"))
        port_name = item.get("port_name")
        if not isinstance(port_name, str) or len(port_name) > 63 or port_name and not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", port_name):
            raise EndpointDiscoveryError("Invalid exposure port name")
        service_port, target_port = _port(item.get("service_port")), _port(item.get("target_port"))
        api = (service["user_config"].get("motor_coordinator_config") or {}).get("api_config") or {}
        field, default = {"inference": ("coordinator_api_infer_port", 1025),
                          "management": ("coordinator_api_mgmt_port", 1026),
                          "observability": ("coordinator_obs_port", 1027)}[item["kind"]]
        if target_port != _port(api.get(field, default)):
            raise EndpointDiscoveryError("Observed port differs from immutable native configuration")
        common = {"kind": item["kind"], "type": item["type"], "port_name": port_name,
                  "service_name": service_name, "service_uid": service_uid}
        candidates, balancers = item.get("node_candidates"), item.get("load_balancer")
        if not isinstance(candidates, list) or len(candidates) > 32 or not isinstance(balancers, list) or len(balancers) > 16:
            raise EndpointDiscoveryError("Exposure candidate bound exceeded")
        scheme = _scheme(service["user_config"], item["kind"])
        for node in candidates:
            if not isinstance(node, dict):
                raise EndpointDiscoveryError("Invalid cluster Node candidate")
            uid, name, ips = _uid(node.get("uid")), _name(node.get("name")), node.get("internal_ips")
            if not isinstance(ips, list) or not 1 <= len(ips) <= 32:
                raise EndpointDiscoveryError("Invalid cluster Node addresses")
            identity = (name, tuple(sorted(public_ip(ip) for ip in ips)))
            if uid in identities and identities[uid] != identity:
                raise EndpointDiscoveryError("Cluster Node UID has inconsistent identity")
            identities[uid] = identity
            for ip in identity[1]:
                if ip in ip_owners and ip_owners[ip] != uid:
                    raise EndpointDiscoveryError("Cluster Node IP is shared by conflicting UIDs")
                ip_owners[ip] = uid
                if item["type"] == "NodePort" and item["ready"] and ip in allowed_ips:
                    result["items"].append({**common, "node_ip": ip, "ready": True,
                                             "url": _origin(scheme, ip, item.get("node_port"))})
        if item["type"] == "LoadBalancer":
            for value in balancers:
                if not isinstance(value, dict) or not value or set(value) - {"ip", "hostname"}:
                    raise EndpointDiscoveryError("Invalid observed load balancer address")
                for field, normalize in (("ip", public_ip), ("hostname", public_hostname)):
                    if field in value:
                        result["items"].append({**common, "ready": False,
                                               "url": _origin(scheme, normalize(value[field]), service_port)})
            message = "负载均衡地址仅展示；尚未验证外部连通性、证书或访问权限"
            if message not in warnings:
                warnings.append(message)
        if item["type"] == "ClusterIP":
            message = "ClusterIP 仅供集群内部访问，未生成外部地址"
            if message not in warnings:
                warnings.append(message)
    unique = []
    for value in result["items"]:
        identity = tuple(value.get(field) for field in ("kind", "type", "url", "service_uid"))
        if identity not in seen:
            seen.add(identity)
            unique.append(value)
    if len(unique) > 128:
        raise EndpointDiscoveryError("Projected address count exceeded")
    result["items"] = unique
    if not unique:
        result["status"] = "unpublished"
        warnings.append("尚无与已登记节点地址匹配且就绪已核验的对外入口")
    else:
        result["status"] = "partial" if warnings or any(not item["ready"] for item in unique) else "ready"
    return result


class EndpointReader:
    """At most 64 service observations, one authenticated read per identity."""
    def __init__(self, store, fleet, runtime_view=runtime_service_view):
        self.store, self.fleet, self.runtime_view = store, fleet, runtime_view
        self.lock, self.cache, self.flights = threading.Lock(), OrderedDict(), {}

    @staticmethod
    def live(entry, identity):
        if not entry or entry[0] != identity or entry[1] <= time.monotonic():
            return False
        expires = entry[2].get("expires_at")
        # A ten-second UI poll must refresh a fifteen-second proof before it
        # expires. Returning the t=0 proof at t=10 would leave a visible gap;
        # refreshing never extends that original proof's absolute validity.
        return not expires or _stamp(expires) > datetime.now(timezone.utc) + timedelta(seconds=6)

    def binding(self, identifier):
        saved = self.store.get("services", identifier)
        if saved is None:
            raise HTTPException(404, "推理服务不存在")
        service = self.runtime_view(saved)
        nodes = self.store.list("nodes")
        if not isinstance(nodes, list) or len(nodes) > 500:
            raise EndpointDiscoveryError("Registered node bound exceeded")
        placements = service.get("placements")
        target = next((node for node in nodes if isinstance(placements, list) and len(placements) == 1
                       and node.get("id") == placements[0].get("node_id")), None)
        identity = {"service": {key: saved.get(key) for key in (
                        "id", "runtime", "status", "config_revision", "deployed_digest", "deployed_runtime",
                        "deployed_user_config", "deployed_env", "deployed_placements", "deployed_motor_profile",
                        "user_config", "env", "placements", "last_job_id", "job_id", "resources_may_exist")},
                    "nodes": [{key: node.get(key) for key in (
                        "id", "address", "agent_url", "_token", "_agent_ca_pem", "configuration_revision")}
                        for node in sorted(nodes, key=lambda value: value.get("id", ""))]}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        return service, nodes, target, digest

    def read(self, identifier):
        try:
            service, nodes, target, identity = self.binding(identifier)
        except HTTPException:
            raise
        except Exception:
            return _empty(identifier, "kubernetes", "无法确认已执行配置，请先核对部署修订")
        runtime = service.get("runtime", "")
        if runtime != "kubernetes":
            return _empty(identifier, runtime, "此入口发现仅适用于 Kubernetes Service", status="unpublished")
        if service.get("status") in _INACTIVE or not service.get("resources_may_exist"):
            return _empty(identifier, runtime, "服务尚未部署或已停止，未发布外部地址", status="unpublished")
        if target is None:
            return _empty(identifier, runtime, "未找到唯一 Kubernetes 执行 Agent")
        flight_key = (identifier, identity)
        with self.lock:
            entry = self.cache.get(identifier)
            if self.live(entry, identity):
                self.cache.move_to_end(identifier)
                return copy.deepcopy(entry[2])
            self.cache.pop(identifier, None)
            event = self.flights.get(flight_key)
            leader = event is None
            if leader:
                if len(self.flights) >= 64:
                    return _empty(identifier, runtime, "入口读取繁忙，请稍后刷新")
                event = threading.Event()
                self.flights[flight_key] = event
        if not leader:
            event.wait(11)
            try:
                if self.binding(identifier)[3] != identity:
                    return _empty(identifier, runtime, "服务或节点配置已变化，请重新读取")
            except Exception:
                return _empty(identifier, runtime, "读取期间服务或节点身份已变化")
            with self.lock:
                entry = self.cache.get(identifier)
                if self.live(entry, identity):
                    return copy.deepcopy(entry[2])
            return _empty(identifier, runtime, "入口读取尚未完成，请稍后刷新")
        started, monotonic_started = datetime.now(timezone.utc), time.monotonic()
        result = _empty(identifier, runtime, "入口读取失败，请检查 Agent 连接和只读权限")
        try:
            raw = self.fleet.call(execution_node(target), "GET", "/agent/kubernetes/" + identifier + "/endpoints",
                                  timeout=10, total_timeout=10, max_response_bytes=MAX_RESPONSE_BYTES)
            result = project_endpoints(raw, service, nodes, request_started=started)
            if self.binding(identifier)[3] != identity:
                result = _empty(identifier, runtime, "服务或节点配置已变化，本次地址已丢弃")
            return result
        except Exception:
            result = _empty(identifier, runtime, "入口读取失败或已执行配置无法确认，请重新读取")
            return result
        finally:
            # Failures replace old addresses with an empty, bounded result.
            remaining = 1.0
            if result.get("expires_at"):
                remaining = max(0.0, min(15 - (time.monotonic() - monotonic_started),
                                        (_stamp(result["expires_at"]) - datetime.now(timezone.utc)).total_seconds()))
            with self.lock:
                self.cache[identifier] = (identity, time.monotonic() + remaining, copy.deepcopy(result))
                self.cache.move_to_end(identifier)
                while len(self.cache) > 64:
                    self.cache.popitem(last=False)
                self.flights.pop(flight_key, None)
                event.set()


def create_endpoint_router(store, fleet, *, runtime_view=runtime_service_view, mode="control"):
    router = APIRouter(prefix="/api/services", tags=["service-endpoints"])
    reader = EndpointReader(store, fleet, runtime_view)

    @router.get("/{identifier}/endpoints")
    def endpoints(identifier: str, request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if request.query_params:
            raise HTTPException(422, "入口查询不接受地址、路径或其它查询参数")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
            raise HTTPException(422, "服务标识格式无效")
        if mode != "control":
            service = store.get("services", identifier)
            if service is None:
                raise HTTPException(404, "推理服务不存在")
            return _empty(identifier, service.get("runtime", ""), "演示环境不读取真实集群入口", status="unpublished")
        return reader.read(identifier)

    return router
