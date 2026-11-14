"""Read-only, UID-proven Kubernetes Service discovery, without constructing URLs.

``discover_endpoints(state, read_json, *, deadline)`` accepts an Agent's saved
ownership state. ``read_json(path, *, timeout, maximum)`` reads one fixed raw
Kubernetes API path and returns None only for NotFound. The caller and adapter
share the same absolute monotonic deadline; neither can reset it per request.

The raw schema is private to the authenticated Agent/Control connection:
schema_version/service_id/namespace/namespace_uid/observed_at/expires_at/source/
status/items/warnings. Items contain Service identity, native coordinator port
kind, observed NodePort/LB fields, verified Node identities and backend readiness.
They never contain a URL or a Pod IP. Control intersects Node IPs with registered
Agent addresses before producing a copyable address; it never connects to LB.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import math
import re
import time
from urllib.parse import urlencode

MAX_RESPONSE_BYTES = 96 * 1024
_MAX_READ_BYTES = 1024 * 1024
_MAX_TOTAL_BYTES = 4 * 1024 * 1024
_NAME = re.compile(r"(?:[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]|[a-z0-9])\Z")
_UID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,127}\Z")
_RESOURCES = {
    ("v1", "Service"): ("/api/v1", "services"),
    ("v1", "Pod"): ("/api/v1", "pods"),
    ("apps/v1", "Deployment"): ("/apis/apps/v1", "deployments"),
    ("apps/v1", "ReplicaSet"): ("/apis/apps/v1", "replicasets"),
    ("apps/v1", "StatefulSet"): ("/apis/apps/v1", "statefulsets"),
    ("mindcluster.huawei.com/v1", "InferServiceSet"): ("/apis/mindcluster.huawei.com/v1", "inferservicesets"),
    ("mindcluster.huawei.com/v1", "InferService"): ("/apis/mindcluster.huawei.com/v1", "inferservices"),
    ("mindcluster.huawei.com/v1", "InstanceSet"): ("/apis/mindcluster.huawei.com/v1", "instancesets"),
}
_ROOT_KINDS = {"Service", "Deployment", "StatefulSet", "InferServiceSet"}
_LIST_TYPES = {"ServiceList": ("v1", "Service"), "NodeList": ("v1", "Node"),
               "EndpointSliceList": ("discovery.k8s.io/v1", "EndpointSlice")}
_WARNINGS = {
    "undeployed": "尚无已记录 UID 的原生工作负载，等待实际部署",
    "unverified": "部分 Service 的所有权、端口或就绪后端无法确认，未生成其对外入口",
    "no_endpoints": "尚未观察到归属和就绪状态均已验证的后端",
    "internal": "部分端口仅为 ClusterIP，不能作为集群外访问地址",
    "limit": "入口发现超出有界采集限制，本次未发布地址",
    "failed": "Kubernetes 入口读取失败，请检查连接和只读权限后刷新",
}


class EndpointDiscoveryError(ValueError):
    pass


def _name(value):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise EndpointDiscoveryError("Invalid Kubernetes object name")
    return value


def _uid(value):
    if not isinstance(value, str) or not _UID.fullmatch(value):
        raise EndpointDiscoveryError("Invalid Kubernetes object UID")
    return value


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise EndpointDiscoveryError("Invalid observed TCP port")
    return value


def public_ip(value):
    """Normalize an observed unicast address; never resolve names or use a URL."""
    if not isinstance(value, str) or len(value) > 64 or "%" in value:
        raise EndpointDiscoveryError("Invalid observed IP address")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise EndpointDiscoveryError("Invalid observed IP address") from error
    if address.is_unspecified or address.is_multicast or address.is_link_local or address.is_loopback:
        raise EndpointDiscoveryError("Observed address is not an eligible management endpoint")
    return str(address)


def public_hostname(value):
    if not isinstance(value, str) or not value.isascii() or len(value) > 253:
        raise EndpointDiscoveryError("Invalid observed load balancer hostname")
    value = value.rstrip(".").lower()
    if value == "localhost" or value.endswith(".localhost") or not value:
        raise EndpointDiscoveryError("Invalid observed load balancer hostname")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in value.split(".")):
        raise EndpointDiscoveryError("Invalid observed load balancer hostname")
    return value


def _identity(document):
    if not isinstance(document, dict) or not isinstance(document.get("metadata"), dict):
        raise EndpointDiscoveryError("Invalid Kubernetes identity")
    meta = document["metadata"]
    return document.get("apiVersion"), document.get("kind"), _name(meta.get("name")), _uid(meta.get("uid"))


def _controller(document):
    refs = document.get("metadata", {}).get("ownerReferences", [])
    if not isinstance(refs, list) or len(refs) > 8:
        raise EndpointDiscoveryError("Invalid controller reference set")
    refs = [ref for ref in refs if isinstance(ref, dict) and ref.get("controller") is True]
    if len(refs) != 1:
        raise EndpointDiscoveryError("A unique controller owner is required")
    ref = refs[0]
    if (ref.get("apiVersion"), ref.get("kind")) not in _RESOURCES:
        raise EndpointDiscoveryError("Unsupported controller kind")
    _name(ref.get("name")); _uid(ref.get("uid"))
    return ref


def empty_snapshot(service_id, namespace, namespace_uid, *, status="unavailable", warning="failed", now=None):
    captured = now or datetime.now(timezone.utc)
    return {"schema_version": 1, "service_id": service_id, "namespace": namespace,
            "namespace_uid": namespace_uid, "observed_at": captured.isoformat(),
            "expires_at": (captured + timedelta(seconds=15)).isoformat(),
            "source": "kubernetes_services", "status": status, "items": [],
            "warnings": [_WARNINGS[warning]]}


def native_config_digest(value):
    """Bind an observation to the original, unmodified native JSON revision."""
    if not isinstance(value, dict):
        raise EndpointDiscoveryError("Missing saved native configuration")
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


class _Discovery:
    def __init__(self, state, reader, deadline, captured):
        self.state, self.reader, self.deadline, self.captured = state, reader, deadline, captured
        sid = state.get("service_id")
        if not isinstance(sid, str) or not 1 <= len(sid) <= 256:
            raise EndpointDiscoveryError("Invalid service identity")
        self.service_key = hashlib.sha256(sid.encode()).hexdigest()[:24]
        self.namespace = _name(state.get("namespace"))
        if self.namespace != "motor-console-" + self.service_key:
            raise EndpointDiscoveryError("Namespace is outside the saved service")
        self.namespace_uid = _uid(state.get("namespace_uid"))
        self.cache, self.roots, self.reads, self.total_bytes = {}, {}, 0, 0
        self.warnings = []

    def warn(self, key):
        message = _WARNINGS[key]
        if message not in self.warnings and len(self.warnings) < 16:
            self.warnings.append(message)

    def read(self, path):
        if path in self.cache:
            return self.cache[path]
        remaining = self.deadline - time.monotonic()
        if remaining <= .02 or self.reads >= 64:
            raise EndpointDiscoveryError("Discovery time/read budget exhausted")
        self.reads += 1
        value = self.reader(path, timeout=min(2.0, remaining), maximum=_MAX_READ_BYTES)
        if value is not None and not isinstance(value, dict):
            raise EndpointDiscoveryError("Invalid API document")
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        self.total_bytes += size
        if size > _MAX_READ_BYTES or self.total_bytes > _MAX_TOTAL_BYTES or time.monotonic() >= self.deadline:
            raise EndpointDiscoveryError("Discovery byte/time budget exhausted")
        self.cache[path] = value
        return value

    def object(self, version, kind, name):
        prefix, resource = _RESOURCES[(version, kind)]
        path = prefix + "/namespaces/" + self.namespace + "/" + resource + "/" + _name(name)
        value = self.read(path)
        if value is None:
            raise EndpointDiscoveryError("Controller object no longer exists")
        identity = _identity(value)
        if identity[:3] != (version, kind, name) or value["metadata"].get("namespace") != self.namespace:
            raise EndpointDiscoveryError("Controller identity changed")
        if value["metadata"].get("deletionTimestamp"):
            raise EndpointDiscoveryError("Controller is deleting")
        return value

    def namespace_identity(self):
        value = self.read("/api/v1/namespaces/" + self.namespace)
        if (value is None or _identity(value) != ("v1", "Namespace", self.namespace, self.namespace_uid)
                or value["metadata"].get("deletionTimestamp")):
            raise EndpointDiscoveryError("Namespace UID changed")
        labels = value["metadata"].get("labels", {})
        if labels.get("motor-console.managed") != "true" or labels.get("motor-console.service") != self.service_key:
            raise EndpointDiscoveryError("Namespace ownership changed")

    def load_roots(self):
        refs = self.state.get("resources", [])
        if not isinstance(refs, list) or len(refs) > 256:
            raise EndpointDiscoveryError("Invalid saved root references")
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("kind") not in _ROOT_KINDS:
                continue
            version, kind, name = ref.get("apiVersion"), ref.get("kind"), ref.get("name")
            if (version, kind) not in _RESOURCES or ref.get("namespace") != self.namespace:
                raise EndpointDiscoveryError("Saved root scope changed")
            value = self.object(version, kind, name)
            if value["metadata"]["uid"] != ref.get("uid"):
                raise EndpointDiscoveryError("Saved root UID changed")
            labels = value["metadata"].get("labels", {})
            if labels.get("motor-console.managed") != "true" or labels.get("motor-console.service") != self.service_key:
                raise EndpointDiscoveryError("Saved root ownership changed")
            self.roots[(version, kind, name, _uid(ref.get("uid")))] = value

    def chain(self, document):
        path, seen = [], set()
        for _ in range(8):
            identity = _identity(document)
            if document["metadata"].get("namespace") != self.namespace or identity in seen:
                raise EndpointDiscoveryError("Controller chain escaped or cycled")
            if document["metadata"].get("deletionTimestamp"):
                raise EndpointDiscoveryError("Controller chain is deleting")
            seen.add(identity); path.append(document)
            if identity in self.roots:
                return self.roots[identity], path
            owner = _controller(document)
            document = self.object(owner["apiVersion"], owner["kind"], owner["name"])
            if document["metadata"]["uid"] != owner["uid"]:
                raise EndpointDiscoveryError("Controller owner UID was replaced")
        raise EndpointDiscoveryError("Controller chain depth exceeded")

    @staticmethod
    def listed(value, kind, maximum):
        expected = _LIST_TYPES.get(kind)
        if (expected is None or not isinstance(value, dict) or value.get("kind") != kind
                or value.get("apiVersion") != expected[0] or not isinstance(value.get("items"), list)
                or len(value["items"]) > maximum or value.get("metadata", {}).get("continue")):
            raise EndpointDiscoveryError("API list is incomplete or exceeds its bound")
        result = []
        for item in value["items"]:
            # Kubernetes typed Lists legitimately omit TypeMeta on their items.
            # The fixed, verified envelope supplies only those absent fields;
            # an explicit conflicting type is never overwritten or accepted.
            if (not isinstance(item, dict)
                    or "apiVersion" in item and item["apiVersion"] != expected[0]
                    or "kind" in item and item["kind"] != expected[1]):
                raise EndpointDiscoveryError("API list item conflicts with its verified type")
            result.append({**item, "apiVersion": expected[0], "kind": expected[1]})
        return result

    def nodes(self):
        result = {}
        for node in self.listed(self.read("/api/v1/nodes?limit=501"), "NodeList", 500):
            version, kind, name, uid = _identity(node)
            if version != "v1" or kind != "Node" or node["metadata"].get("deletionTimestamp"):
                continue
            conditions = node.get("status", {}).get("conditions", [])
            if [c.get("status") for c in conditions if c.get("type") == "Ready"] != ["True"]:
                continue
            addresses = node.get("status", {}).get("addresses", [])
            if not isinstance(addresses, list) or len(addresses) > 32:
                raise EndpointDiscoveryError("Node address count exceeded")
            ips = []
            for address in addresses:
                if address.get("type") == "InternalIP":
                    ip = public_ip(address.get("address"))
                    if ip not in ips:
                        ips.append(ip)
            if ips:
                if name in result:
                    raise EndpointDiscoveryError("Node identity is duplicated")
                result[name] = {"uid": uid, "name": name, "internal_ips": ips}
        return result

    def port_kind(self, service, chain, port):
        role = next((d.get("spec", {}).get("name") for d in chain if d.get("kind") == "InstanceSet"), None)
        if role is None:
            # Directly tracked native multi-Deployment Services have no operator
            # owner. UID ownership is already proven; this selector only picks
            # the coordinator plane, never establishes ownership by itself.
            if service.get("spec", {}).get("selector", {}).get("app") == "mindie-motor-coordinator":
                role = "coordinator"
        if role != "coordinator":
            return None
        config = self.state.get("original_user_config") or {}
        api = (config.get("motor_coordinator_config") or {}).get("api_config") or {}
        fields = {"inference": ("coordinator_api_infer_port", 1025),
                  "management": ("coordinator_api_mgmt_port", 1026),
                  "observability": ("coordinator_obs_port", 1027)}
        target = port.get("targetPort", port.get("port"))
        if type(target) is not int:
            self.warn("unverified")  # Named target ports need an explicit native mapping.
            return None
        matches = [kind for kind, (key, default) in fields.items() if _port(api.get(key, default)) == target]
        if len(matches) != 1:
            self.warn("unverified")
            return None
        return matches[0]

    def endpoints(self, service, port, service_root):
        name, uid = service["metadata"]["name"], service["metadata"]["uid"]
        query = urlencode({"labelSelector": "kubernetes.io/service-name=" + name, "limit": 65})
        slices = self.read("/apis/discovery.k8s.io/v1/namespaces/" + self.namespace + "/endpointslices?" + query)
        values = self.listed(slices, "EndpointSliceList", 64) if slices is not None else []
        candidates = []
        target_port = port.get("targetPort", port.get("port"))
        if values:
            for item in values:
                meta = item.get("metadata", {})
                if (_identity(item)[:2] != ("discovery.k8s.io/v1", "EndpointSlice")
                        or meta.get("deletionTimestamp")
                        or meta.get("namespace") != self.namespace or meta.get("labels", {}).get("kubernetes.io/service-name") != name):
                    raise EndpointDiscoveryError("EndpointSlice scope changed")
                owner = _controller(item)
                if (owner["apiVersion"], owner["kind"], owner["name"], owner["uid"]) != ("v1", "Service", name, uid):
                    raise EndpointDiscoveryError("EndpointSlice Service UID changed")
                ports = item.get("ports", [])
                if not isinstance(ports, list) or len(ports) > 16:
                    raise EndpointDiscoveryError("EndpointSlice ports exceeded")
                if not any(p.get("protocol", "TCP") == "TCP" and p.get("port") == target_port
                           and (not port.get("name") or p.get("name") == port["name"]) for p in ports):
                    continue
                endpoints = item.get("endpoints", [])
                if not isinstance(endpoints, list) or len(endpoints) > 128:
                    raise EndpointDiscoveryError("EndpointSlice addresses exceeded")
                for endpoint in endpoints:
                    conditions = endpoint.get("conditions", {})
                    if conditions.get("ready") is not True or conditions.get("terminating") is True:
                        continue
                    addresses = endpoint.get("addresses", [])
                    if not isinstance(addresses, list) or len(addresses) > 8:
                        raise EndpointDiscoveryError("EndpointSlice address count exceeded")
                    candidates.extend((address, endpoint.get("nodeName"), endpoint.get("targetRef")) for address in addresses)
        else:
            # NotFound/empty SliceList only. A forbidden/failed Slice request
            # raises through read(); it must not silently lower the proof level.
            legacy = self.read("/api/v1/namespaces/" + self.namespace + "/endpoints/" + name)
            if legacy is None:
                return []
            if (_identity(legacy)[:3] != ("v1", "Endpoints", name)
                    or legacy["metadata"].get("namespace") != self.namespace):
                raise EndpointDiscoveryError("Endpoints scope changed")
            owners = legacy["metadata"].get("ownerReferences", [])
            if owners:
                owner = _controller(legacy)
                if (owner["apiVersion"], owner["kind"], owner["name"], owner["uid"]) != ("v1", "Service", name, uid):
                    raise EndpointDiscoveryError("Endpoints Service UID changed")
            subsets = legacy.get("subsets", [])
            if not isinstance(subsets, list) or len(subsets) > 16:
                raise EndpointDiscoveryError("Endpoints subset count exceeded")
            for subset in subsets:
                ports = subset.get("ports", [])
                if not isinstance(ports, list) or len(ports) > 16:
                    raise EndpointDiscoveryError("Endpoints port count exceeded")
                if not any(p.get("protocol", "TCP") == "TCP" and p.get("port") == target_port
                           and (not port.get("name") or p.get("name") == port["name"]) for p in ports):
                    continue
                addresses = subset.get("addresses", [])
                if not isinstance(addresses, list) or len(addresses) > 128:
                    raise EndpointDiscoveryError("Endpoints address count exceeded")
                candidates.extend((item.get("ip"), item.get("nodeName"), item.get("targetRef")) for item in addresses)
        if len(candidates) > 128:
            raise EndpointDiscoveryError("Ready endpoint count exceeded")
        selector = service.get("spec", {}).get("selector")
        if not isinstance(selector, dict) or not selector or len(selector) > 32:
            return []
        nodes, seen = set(), set()
        for address, node_name, ref in candidates:
            if (not isinstance(ref, dict) or ref.get("kind") != "Pod" or ref.get("apiVersion", "v1") != "v1"
                    or ref.get("namespace", self.namespace) != self.namespace):
                continue
            pod = self.object("v1", "Pod", _name(ref.get("name")))
            if pod["metadata"]["uid"] != ref.get("uid"):
                continue
            pod_root, _ = self.chain(pod)
            # A namespace can contain several tracked controller roots. A Pod
            # selected by an operator Service must reach that exact ISS UID.
            # Native multi-Deployment mode intentionally tracks its Service
            # and Deployment separately, so that mode uses both saved roots.
            if (service_root.get("kind") == "InferServiceSet"
                    and _identity(pod_root) != _identity(service_root)):
                continue
            if (service_root.get("kind") == "Service"
                    and pod_root.get("kind") not in {"Deployment", "StatefulSet"}):
                continue
            labels = pod["metadata"].get("labels", {})
            status = pod.get("status", {})
            if (any(labels.get(key) != value for key, value in selector.items()) or status.get("phase") != "Running"
                    or [c.get("status") for c in status.get("conditions", []) if c.get("type") == "Ready"] != ["True"]):
                continue
            ip = public_ip(address)
            pod_ips = [v.get("ip") for v in status.get("podIPs", [])] or [status.get("podIP")]
            if ip not in [public_ip(v) for v in pod_ips if v]:
                continue
            actual_node = _name(pod.get("spec", {}).get("nodeName"))
            if node_name is not None and node_name != actual_node:
                continue
            identity = (pod["metadata"]["uid"], ip)
            if identity not in seen:
                seen.add(identity); nodes.add(actual_node)
        return sorted(nodes)

    def discover(self):
        self.namespace_identity(); self.load_roots()
        if not self.roots:
            return empty_snapshot(self.state["service_id"], self.namespace, self.namespace_uid,
                                  status="unpublished", warning="undeployed", now=self.captured)
        services = self.listed(self.read("/api/v1/namespaces/" + self.namespace + "/services?limit=17"), "ServiceList", 16)
        nodes, result = self.nodes(), []
        for service in services:
            try:
                if _identity(service)[:2] != ("v1", "Service"):
                    raise EndpointDiscoveryError("Invalid Service")
                root, chain = self.chain(service)
                spec = service.get("spec", {})
                service_type = spec.get("type", "ClusterIP")
                if service_type not in {"NodePort", "LoadBalancer", "ClusterIP"}:
                    continue
                ports = spec.get("ports", [])
                if not isinstance(ports, list) or len(ports) > 8:
                    raise EndpointDiscoveryError("Service port count exceeded")
                for port in ports:
                    if port.get("protocol", "TCP") != "TCP":
                        continue
                    kind = self.port_kind(service, chain, port)
                    if kind is None:
                        continue
                    backend_nodes = self.endpoints(service, port, root)
                    ready = bool(backend_nodes)
                    policy = spec.get("externalTrafficPolicy", "Cluster")
                    if policy not in {"Cluster", "Local"}:
                        raise EndpointDiscoveryError("Unsupported traffic policy")
                    eligible = [node for name, node in nodes.items() if policy == "Cluster" or name in backend_nodes] if ready else []
                    if len(eligible) > 32:
                        raise EndpointDiscoveryError("Candidate node count exceeded")
                    lb = []
                    ingress = service.get("status", {}).get("loadBalancer", {}).get("ingress", [])
                    if not isinstance(ingress, list) or len(ingress) > 16:
                        raise EndpointDiscoveryError("Load balancer ingress count exceeded")
                    for entry in ingress:
                        value = {}
                        if entry.get("ip"):
                            value["ip"] = public_ip(entry["ip"])
                        if entry.get("hostname"):
                            value["hostname"] = public_hostname(entry["hostname"])
                        if value:
                            lb.append(value)
                    if not ready:
                        self.warn("no_endpoints")
                    if service_type == "ClusterIP":
                        self.warn("internal")
                    port_name = port.get("name", "")
                    if not isinstance(port_name, str) or len(port_name) > 63 or port_name and not _NAME.fullmatch(port_name):
                        raise EndpointDiscoveryError("Invalid service port name")
                    result.append({"kind": kind, "type": service_type, "port_name": port_name,
                        "service_name": service["metadata"]["name"], "service_uid": service["metadata"]["uid"],
                        "service_port": _port(port.get("port")), "target_port": _port(port.get("targetPort", port.get("port"))),
                        "node_port": _port(port["nodePort"]) if port.get("nodePort") is not None else None,
                        "external_traffic_policy": policy, "ready": ready, "node_candidates": eligible,
                        "load_balancer": lb, "owner_root_uid": root["metadata"]["uid"], "owner_chain_verified": True})
                    if len(result) > 32:
                        raise EndpointDiscoveryError("Port item count exceeded")
            except EndpointDiscoveryError:
                self.warn("unverified")
        # Bypass the memoized first Namespace read to detect replacement during collection.
        self.cache.pop("/api/v1/namespaces/" + self.namespace, None); self.namespace_identity()
        output = {**empty_snapshot(self.state["service_id"], self.namespace, self.namespace_uid, now=self.captured),
                  "native_config_digest": native_config_digest(self.state.get("original_user_config")),
                  "status": "partial" if result and self.warnings else "ready" if result else "unpublished",
                  "items": result, "warnings": self.warnings or ([] if result else [_WARNINGS["unverified"]])}
        if len(json.dumps(output, ensure_ascii=False).encode()) > MAX_RESPONSE_BYTES:
            raise EndpointDiscoveryError("Endpoint snapshot output exceeded")
        return output


def discover_endpoints(state: dict, read_json, *, deadline: float, now: datetime | None = None) -> dict:
    """Return verified structure or an explicit empty result; never a guessed URL."""
    captured = now or datetime.now(timezone.utc)
    sid = state.get("service_id", "") if isinstance(state, dict) else ""
    namespace = state.get("namespace", "") if isinstance(state, dict) else ""
    namespace_uid = state.get("namespace_uid", "") if isinstance(state, dict) else ""
    try:
        if (not isinstance(state, dict) or type(deadline) not in {int, float}
                or not math.isfinite(deadline) or not 0 < deadline - time.monotonic() <= 8.1):
            raise EndpointDiscoveryError("Invalid discovery deadline")
        return _Discovery(state, read_json, deadline, captured).discover()
    except Exception:
        # API errors can contain credential helper details. Do not return their
        # text, old addresses, or a partial ownership proof to the control plane.
        return empty_snapshot(sid, namespace, namespace_uid, warning="failed", now=captured)
