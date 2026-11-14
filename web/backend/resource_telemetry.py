"""Read-only, bounded host observations; device allocation remains a separate contract.

Ascend NPU Exporter metric names, labels and MB units follow its official metrics
API. Exporter IDs and PID namespaces are not assumed to be Docker host identities.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone, timedelta
from functools import lru_cache
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import ssl
import threading
import time
from urllib.parse import urlsplit

import httpx
import asyncio

from .network_budget import run_network

from .resource_freshness import elapsed_ns, host_snapshot_age, mark_sample, rebase_times

MAX_METRICS_BYTES = 2 * 1024 * 1024
MAX_DEVICES = 256
MAX_PROCESSES = 128
_MIB = 1024 * 1024
_MAX_MEMORY_MB = (2**53 - 1) // _MIB
_WANTED = {
    "npu_chip_info_utilization": "utilization_percent",
    "npu_chip_info_hbm_total_memory": "memory_total_bytes",
    "npu_chip_info_hbm_used_memory": "memory_used_bytes",
    "npu_chip_info_health_status": "health",
    "npu_chip_info_temperature": "temperature_celsius",
    "npu_chip_info_power": "power_watts",
    "npu_chip_info_process_info": "process",
    "npu_chip_info_process_info_num": "process_count",
}
_SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+([^\s]+)(?:\s+([^\s]+))?\s*$')
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\[\\"n])*)"(?:,|$)')


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _error(source: str, code: str, message: str) -> dict:
    return {"source": source, "code": code, "message": message}


def _setting(name: str, default: int, low: int, high: int) -> int:
    value = os.getenv(name, str(default))
    if not value.isascii() or not value.isdecimal() or not low <= int(value) <= high:
        raise ValueError(name + " is outside its supported bounds")
    return int(value)


def _labels(raw: str) -> dict[str, str]:
    result, offset = {}, 0
    for match in _LABEL.finditer(raw):
        if match.start() != offset or match[1] in result or len(result) >= 32:
            raise ValueError("invalid labels")
        value = match[2].replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
        if len(value) > 512:
            raise ValueError("label exceeds bounds")
        result[match[1]] = value
        offset = match.end()
    if offset != len(raw):
        raise ValueError("invalid labels")
    return result


def _number(raw: str, low: float, high: float) -> float:
    value = float(raw)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("metric exceeds bounds")
    return value


def _identity(raw: str, maximum: int = 128) -> str:
    if not raw or len(raw) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise ValueError("invalid public identity")
    return raw


def parse_npu_metrics(text: str, *, collected_at: datetime | None = None,
                      ttl_seconds: int = 45, device_map: dict[str, int] | None = None,
                      pid_scope: str = "unknown", sample_started_ns: int | None = None) -> dict:
    """Project only documented Ascend metrics. Missing/conflicting values stay unknown."""
    started = elapsed_ns() if sample_started_ns is None else sample_started_ns
    captured = collected_at or _utc()
    devices: dict[str, dict] = {}
    values: dict[tuple[str, str], float | None] = {}
    counts: dict[str, int] = {}
    processes: dict[str, dict[tuple[int, str], dict]] = {}
    invalid_processes: set[str] = set()
    errors, stamps = [], []
    mapping = device_map or {}
    if len(text.encode("utf-8")) > MAX_METRICS_BYTES:
        raise ValueError("metric response exceeds limit")
    for index, line in enumerate(text.splitlines()):
        if index >= 30000 or len(line) > 8192:
            raise ValueError("metric exposition exceeds limits")
        if not line.strip() or line.startswith("#"):
            continue
        metric = line.split("{", 1)[0].split(None, 1)[0]
        if metric not in _WANTED:
            continue
        match = _SAMPLE.fullmatch(line)
        try:
            if not match:
                raise ValueError("invalid sample")
            labels = _labels(match[2] or "")
            identifier = _identity(labels.get("id", ""), 64)
            observed = None
            if match[4]:
                timestamp = _number(match[4], 1, 10**16)
                if timestamp > 10**11:
                    timestamp /= 1000
                observed = datetime.fromtimestamp(timestamp, timezone.utc)
                if observed > captured + timedelta(seconds=15):
                    raise ValueError("sample timestamp is in the future")
            if identifier not in devices:
                if len(devices) >= MAX_DEVICES:
                    raise ValueError("device count exceeds limit")
                mapped = mapping.get(identifier)
                devices[identifier] = {
                    "device_id": identifier, "device_index": mapped,
                    "physical_device_id": mapped, "logical_device_id": None,
                    "mapping_status": "configured" if mapped is not None else "unknown",
                    "pci_bus_id": "", "uuid": "", "model_name": "",
                    "utilization_percent": None, "memory_total_bytes": None,
                    "memory_used_bytes": None, "memory_usage_percent": None,
                    "health": "unknown", "temperature_celsius": None, "power_watts": None,
                    "process_pid_scope": pid_scope, "processes_status": "unavailable", "processes": [],
                }
            device = devices[identifier]
            for source, target in (("pcie_bus_info", "pci_bus_id"), ("vdie_id", "uuid"), ("model_name", "model_name")):
                if labels.get(source):
                    identity = _identity(labels[source])
                    if device[target] and device[target] != identity:
                        raise ValueError("conflicting device identity")
                    device[target] = identity
            field = _WANTED[metric]
            if field == "process":
                pid = labels.get("process_id", "")
                # Official exporter emits an empty-PID, zero-memory placeholder
                # when its reported process count is zero. It is not a PID 0.
                if not pid and _number(match[3], 0, _MAX_MEMORY_MB) == 0:
                    if observed is not None:
                        stamps.append(observed)
                    continue
                if not pid.isascii() or not pid.isdecimal() or not 1 <= int(pid) <= 2**31 - 1:
                    invalid_processes.add(identifier)
                    raise ValueError("missing/invalid process identity")
                memory = round(_number(match[3], 0, _MAX_MEMORY_MB) * _MIB)
                owner = labels.get("container_id", "")
                # No arbitrary container names, namespaces or command lines leave the collector.
                if owner and not re.fullmatch(r"(?:[a-z]+://)?[a-fA-F0-9]{12,64}", owner):
                    owner = ""
                item = {"pid": int(pid), "memory_used_bytes": memory}
                if owner:
                    item["container_id"] = owner
                entries = processes.setdefault(identifier, {})
                key = (int(pid), owner)
                if key in entries and entries[key] != item:
                    invalid_processes.add(identifier)
                    raise ValueError("conflicting process observation")
                if len(entries) >= MAX_PROCESSES and key not in entries:
                    invalid_processes.add(identifier)
                    raise ValueError("process count exceeds limit")
                entries[key] = item
            else:
                bounds = {"utilization_percent": (0, 100), "memory_total_bytes": (0, _MAX_MEMORY_MB),
                          "memory_used_bytes": (0, _MAX_MEMORY_MB), "health": (-1, 1),
                          "temperature_celsius": (-40, 200), "power_watts": (0, 10000),
                          "process_count": (0, MAX_PROCESSES)}
                number = _number(match[3], *bounds[field])
                if field in {"health", "process_count"} and not number.is_integer():
                    raise ValueError("integer metric is fractional")
                key = (identifier, field)
                if key in values and values[key] != number:
                    values[key] = None
                    raise ValueError("conflicting sample")
                if key not in values:
                    values[key] = number
            if observed is not None:
                stamps.append(observed)
        except (ValueError, OverflowError, OSError):
            if len(errors) < 16:
                errors.append(_error("npu_exporter", "invalid_sample", "部分 NPU 指标无效或互相冲突，未作为可信值使用"))
    for (identifier, field), number in values.items():
        device = devices[identifier]
        if number is None:
            continue
        if field == "process_count":
            counts[identifier] = int(number)
        elif field == "health":
            device[field] = {1: "healthy", 0: "unhealthy", -1: "unknown"}[int(number)]
        elif field.startswith("memory_"):
            device[field] = round(number * _MIB)
        else:
            device[field] = number
    for identifier, device in devices.items():
        used, total = device["memory_used_bytes"], device["memory_total_bytes"]
        if used is not None and total is not None:
            if total > 0 and used <= total:
                device["memory_usage_percent"] = round(used / total * 100, 3)
            else:
                device["memory_used_bytes"] = None
                errors.append(_error("npu_exporter", "invalid_memory", "NPU 显存观测不一致"))
        observed_processes = list(processes.get(identifier, {}).values())
        device["processes"] = sorted(observed_processes, key=lambda value: (value["pid"], value.get("container_id", "")))
        # Exporter currently emits an empty process structure on some DCMI
        # failures. A reported zero is not proof of successful empty coverage.
        if observed_processes and identifier not in invalid_processes and counts.get(identifier) == len(observed_processes):
            device["processes_status"] = "ready"
    observed_at = min(stamps) if stamps else captured
    expires = observed_at + timedelta(seconds=ttl_seconds)
    if expires <= captured:
        errors.append(_error("npu_exporter", "stale_samples", "NPU 指标已过期，保留为历史观测"))
    complete = bool(devices) and all(
        d["utilization_percent"] is not None and d["memory_total_bytes"] is not None
        and d["memory_used_bytes"] is not None and d["health"] != "unknown"
        for d in devices.values())
    if not devices:
        errors.append(_error("npu_exporter", "no_device_metrics", "未读取到受支持的 NPU 指标"))
    result = {"status": "ready" if complete and not errors else "partial" if devices else "error",
            "source": "ascend-npu-exporter", "collected_at": observed_at.isoformat(),
            "expires_at": expires.isoformat(), "timestamp_source": "exporter" if stamps else "scrape",
            "memory_unit_source": "exporter-MB-as-MiB", "devices": list(devices.values()),
            "errors": errors[:16]}
    # Explicit exporter event times belong to the exporter's UTC clock. Their
    # age cannot be inferred from the Agent's clock or its elapsed scrape time.
    return result if stamps else mark_sample(result, started_ns=started)


def _exporter_url(raw: str) -> str:
    parsed = urlsplit(raw)
    allowed = {v.strip().lower() for v in os.getenv("MOTOR_NPU_METRICS_ALLOWED_HOSTS", "").split(",") if v.strip()}
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not parsed.path.endswith("/metrics")):
        raise ValueError("invalid configured NPU metrics endpoint")
    host = parsed.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Hostnames need explicit deployment-owned authorization; request input cannot extend it.
        if host not in allowed:
            raise ValueError("NPU exporter host is not authorized") from None
    else:
        if address.is_unspecified or address.is_multicast or address.is_link_local or not (address.is_private or address.is_loopback):
            raise ValueError("NPU exporter must use a private management endpoint")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("invalid NPU exporter port")
    return raw


def _fetch_exporter(raw: str, *, timeout: float = 6) -> str:
    url = _exporter_url(raw)
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("invalid exporter time budget")
    deadline = time.monotonic() + timeout

    def prepare():
        ca = os.getenv("MOTOR_NPU_METRICS_CA_FILE", "")
        trust = ssl.create_default_context(cafile=ca or None) if urlsplit(url).scheme == "https" else ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trust.keylog_filename = None
        trust.minimum_version = ssl.TLSVersion.TLSv1_2
        headers = {"Accept": "text/plain, application/openmetrics-text", "Accept-Encoding": "identity"}
        token_path = os.getenv("MOTOR_NPU_METRICS_TOKEN_FILE", "")
        if token_path:
            from .agent_credentials import read_private_file
            token = read_private_file(Path(token_path), maximum=4096).decode().strip()
            if not token or any(not 33 <= ord(c) <= 126 for c in token):
                raise ValueError("invalid deployment-owned NPU exporter token")
            from .observability import register_secret
            register_secret(token)
            headers["Authorization"] = "Bearer " + token
        return trust, headers

    async def collect():
        trust, headers = await asyncio.to_thread(prepare)
        chunks, size = [], 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(3, connect=2, pool=1), verify=trust,
                                     trust_env=False, follow_redirects=False,
                                     limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200 or response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                    raise ValueError("exporter response is not an uncompressed successful scrape")
                def add(chunk):
                    nonlocal size
                    size += len(chunk)
                    if size > MAX_METRICS_BYTES:
                        raise ValueError("exporter response exceeds its collection budget")
                    chunks.append(chunk)
                if response.is_stream_consumed:
                    add(response.content)
                else:
                    async for chunk in response.aiter_raw():
                        add(chunk)
        return b"".join(chunks).decode("utf-8")

    return run_network(collect, deadline)


def _read_host_snapshot(path: str, identity: str, captured: datetime, *, host_root="/host") -> dict:
    from .agent_credentials import read_private_file
    from .resource_inventory import project_inventory
    if not identity:
        raise ValueError("host identity is required to accept an NPU snapshot")
    source = Path(path)
    if not source.is_absolute() or source.is_symlink():
        raise ValueError("host snapshot must use a deployment-owned absolute regular path")
    value = json.loads(read_private_file(source, maximum=MAX_METRICS_BYTES).decode("utf-8"))
    if (not isinstance(value, dict) or value.get("inventory_schema") != 2
            or value.get("host_identity") != identity):
        raise ValueError("host snapshot identity/schema could not be verified")
    age, started = None, elapsed_ns()
    if "sample_clock" in value:
        age = host_snapshot_age(value["sample_clock"], host_root)
        # Only a matching host boot clock can establish a persisted file's real
        # age across process restarts or wall-clock corrections.
        npu_value = value.get("npu_telemetry")
        if (not isinstance(npu_value, dict) or npu_value.get("collected_at") != value.get("collected_at")
                or npu_value.get("expires_at") != value.get("expires_at")):
            raise ValueError("host snapshot clock coverage is inconsistent")
        rebase_times(value, age_ms=age, captured=captured, basis="host-boot-uptime")
        rebase_times(npu_value, age_ms=age, captured=captured, basis="host-boot-uptime")
    projected = project_inventory(value, received_at=captured)
    observed, expires = projected["collected_at"], projected["expires_at"]
    if not expires or datetime.fromisoformat(expires) <= captured:
        raise ValueError("host snapshot is expired")
    npu = projected.get("npu_telemetry")
    if not npu or npu["source"] != "host-npu-smi":
        raise ValueError("host snapshot source is not supported")
    if npu["status"] in {"ready", "partial"}:
        if npu["collected_at"] != observed or npu["expires_at"] != expires or not npu["devices"]:
            raise ValueError("host snapshot observation coverage is inconsistent")
        for device in npu["devices"]:
            if (device["mapping_status"] != "verified" or device.get("physical_device_id") != device["device_index"]
                    or any(device.get(field) is None for field in ("npu_id", "chip_id", "logical_device_id"))
                    or device["process_pid_scope"] != "host"):
                raise ValueError("host snapshot device identity is not verified")
    return mark_sample(npu, started_ns=started, age_ms=age, basis="host-boot-uptime") if age is not None else npu


class ResourceCollector:
    def __init__(self, host_root: str | Path):
        self.root = Path(host_root)
        self.lock = threading.Lock()
        self.cached: dict | None = None
        self.last_good_npu: dict | None = None
        self.next_collection = 0.0

    def _read(self, relative: str, limit: int, *, first_line=False) -> str:
        path = self.root / relative
        if path.is_symlink():
            raise ValueError("host observation symlink is not accepted")
        with path.open("rb") as source:
            value = source.readline(limit + 1) if first_line else source.read(limit + 1)
        if len(value) > limit:
            raise ValueError("host observation exceeds limit")
        return value.decode("ascii")

    def _host(self, captured: datetime, ttl: int) -> dict:
        # CPU, memory and OS have one collection authority: Node Exporter.
        # Machine ID below is only a hashed scope for existing NPU
        # snapshot identity binding, not an alternative metric or OS source.
        from .node_exporter import collect_node_metrics
        result = collect_node_metrics()
        identity = ""
        for path in ("etc/machine-id", "var/lib/dbus/machine-id"):
            try:
                raw = self._read(path, 256).strip()
                if re.fullmatch(r"[a-fA-F0-9]{32}", raw):
                    identity = "sha256:" + hashlib.sha256(raw.lower().encode()).hexdigest()
                    break
            except (OSError, ValueError, UnicodeError):
                continue
        return {"host_identity": identity, **result}

    def collect(self) -> dict:
        if time.monotonic() < self.next_collection and self.cached is not None:
            return copy.deepcopy(self.cached)
        if not self.lock.acquire(blocking=False):
            if self.cached is not None:
                return copy.deepcopy(self.cached)
            from .node_exporter import unavailable_node_metrics
            return {"host_identity": "", **unavailable_node_metrics("collecting"),
                    "npu_telemetry": {"status": "unavailable", "source": "ascend-npu-exporter", "devices": [],
                                      "errors": [_error("npu_exporter", "collecting", "资源采集进行中")]}}
        try:
            # A prior owner can finish between the optimistic cache check and
            # this non-blocking acquisition; do not repeat its scrape.
            if time.monotonic() < self.next_collection and self.cached is not None:
                return copy.deepcopy(self.cached)
            cache = _setting("MOTOR_RESOURCE_CACHE_SECONDS", 10, 1, 60)
            ttl = _setting("MOTOR_RESOURCE_TTL_SECONDS", 45, 5, 300)
            started = elapsed_ns()
            captured = _utc()
            result = self._host(captured, ttl)
            endpoint = os.getenv("MOTOR_NPU_METRICS_URL", "")
            snapshot = os.getenv("MOTOR_NPU_SNAPSHOT_FILE", "")
            unavailable = mark_sample({"status": "unavailable", "source": "ascend-npu-exporter", "devices": [],
                           "collected_at": captured.isoformat(), "expires_at": (captured + timedelta(seconds=ttl)).isoformat(),
                           "errors": [_error("npu_exporter", "not_configured", "尚未配置 NPU 硬件指标来源")]}, started_ns=started)
            snapshot_error = None
            npu = None
            if snapshot:
                try:
                    npu = _read_host_snapshot(snapshot, result["host_identity"], captured, host_root=self.root)
                    if npu["status"] not in {"ready", "partial"}:
                        snapshot_error, npu = "snapshot_failed", None
                except (OSError, ValueError, UnicodeError):
                    snapshot_error = "invalid_snapshot"
            if npu is not None:
                if npu["status"] == "ready":
                    self.last_good_npu = copy.deepcopy(npu)
            elif not endpoint:
                npu = unavailable
                if snapshot_error:
                    npu = {**unavailable, "status": "error", "source": "host-npu-smi", "errors": [
                        _error("host-npu-smi", snapshot_error, "宿主 NPU 快照缺失、无效或已过期；当前硬件状态未知")]}
                    if self.last_good_npu:
                        npu["last_good"] = copy.deepcopy(self.last_good_npu)
            else:
                try:
                    mapping = json.loads(os.getenv("MOTOR_NPU_DEVICE_MAP_JSON", "{}"))
                    if (not isinstance(mapping, dict) or len(mapping) > MAX_DEVICES
                            or any(not isinstance(k, str) or not k or len(k) > 64 or type(v) is not int or not 0 <= v <= 4095 for k, v in mapping.items())
                            or len(set(mapping.values())) != len(mapping)):
                        raise ValueError("invalid deployment-owned device mapping")
                    scope = os.getenv("MOTOR_NPU_PROCESS_PID_SCOPE", "unknown")
                    if scope not in {"host", "unknown"}:
                        raise ValueError("invalid process PID scope")
                    npu = parse_npu_metrics(_fetch_exporter(endpoint), collected_at=captured,
                                            ttl_seconds=ttl, device_map=mapping, pid_scope=scope, sample_started_ns=started)
                    if snapshot_error:
                        npu["errors"].append(_error("host-npu-smi", snapshot_error, "宿主快照不可用，当前使用只读 Exporter 指标"))
                        if npu["status"] == "ready":
                            npu["status"] = "partial"
                    if npu["status"] == "ready":
                        self.last_good_npu = copy.deepcopy(npu)
                except (ValueError, OSError, UnicodeError, httpx.HTTPError):
                    npu = {**unavailable, "status": "error", "errors": [_error("npu_exporter", "collection_failed", "NPU 指标采集失败；检查受信任地址、证书和采集器状态")]}
                    if self.last_good_npu:
                        npu["last_good"] = copy.deepcopy(self.last_good_npu)
            result["npu_telemetry"] = npu
            self.cached = result
            self.next_collection = time.monotonic() + cache
            return copy.deepcopy(result)
        finally:
            self.lock.release()


@lru_cache(maxsize=8)
def _collector(root: str) -> ResourceCollector:
    return ResourceCollector(root)


def collect_resource_telemetry(host_root: str | Path) -> dict:
    return _collector(str(Path(host_root).absolute())).collect()
