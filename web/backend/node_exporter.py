"""Thin, bounded Node Exporter adapter; never read host procfs or OS files.

The deployment owns the endpoint and verifies the Exporter's host namespaces
and rootfs flags. This adapter consumes the same metrics as Prometheus, keeps
only one CPU baseline, and never substitutes the Agent container's OS.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
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

from .network_budget import run_network
from .resource_freshness import elapsed_ns, mark_sample

MAX_BYTES = 2 * 1024 * 1024
MAX_LINES = 40000
MAX_SERIES = 30000
MAX_CPUS = 2048
_MODES = frozenset({"user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"})
_WANTED = frozenset({"node_cpu_seconds_total", "node_cpu_online", "node_memory_MemTotal_bytes",
                     "node_memory_MemAvailable_bytes", "node_os_info", "node_uname_info",
                     "node_boot_time_seconds", "node_scrape_collector_success"})
_SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+([^\s]+)(?:\s+([^\s]+))?\s*$')
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\[\\"n])*)"(?:,|$)')


def _bound(name, default, low, high):
    raw = os.getenv(name, str(default))
    if not raw.isascii() or not raw.isdecimal() or not low <= int(raw) <= high:
        raise ValueError(name + " is outside its supported bounds")
    return int(raw)


@dataclass(frozen=True)
class NodeExporterSettings:
    url: str = "http://127.0.0.1:9100/metrics"
    allowed_hosts: tuple[str, ...] = ()
    ca_file: str = ""
    token_file: str = ""
    basic_auth_file: str = ""
    interval: int = 10
    ttl: int = 45
    timeout: float = 3

    @classmethod
    def environment(cls):
        settings = cls(url=os.getenv("MOTOR_NODE_EXPORTER_URL", "http://127.0.0.1:9100/metrics"),
                       allowed_hosts=tuple(v.strip().lower() for v in os.getenv("MOTOR_NODE_EXPORTER_ALLOWED_HOSTS", "").split(",") if v.strip()),
                       ca_file=os.getenv("MOTOR_NODE_EXPORTER_CA_FILE", ""),
                       token_file=os.getenv("MOTOR_NODE_EXPORTER_TOKEN_FILE", ""),
                       basic_auth_file=os.getenv("MOTOR_NODE_EXPORTER_BASIC_AUTH_FILE", ""),
                       interval=_bound("MOTOR_NODE_EXPORTER_CACHE_SECONDS", 10, 1, 60),
                       ttl=_bound("MOTOR_NODE_EXPORTER_TTL_SECONDS", 45, 5, 300))
        settings.validate()
        return settings

    def validate(self):
        if (not isinstance(self.allowed_hosts, tuple) or len(self.allowed_hosts) > 32
                or any(not isinstance(host, str) or not 1 <= len(host) <= 253
                       or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) for host in self.allowed_hosts)):
            raise ValueError("Invalid Node Exporter hostname authorization")
        if (not isinstance(self.url, str) or len(self.url) > 2048
                or any(ord(c) <= 32 or ord(c) == 127 for c in self.url)):
            raise ValueError("Invalid deployment-owned Node Exporter endpoint")
        parsed = urlsplit(self.url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path != "/metrics"
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError("Invalid deployment-owned Node Exporter endpoint")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
            if parsed.hostname.lower() not in self.allowed_hosts:
                raise ValueError("Node Exporter hostname is not authorized") from None
        if address is not None and (address.is_unspecified or address.is_multicast or address.is_link_local
                                    or not (address.is_private or address.is_loopback)):
            raise ValueError("Node Exporter requires a private management endpoint")
        if self.token_file and self.basic_auth_file:
            raise ValueError("Node Exporter authentication methods are mutually exclusive")
        if (self.token_file or self.basic_auth_file) and parsed.scheme != "https" and not (address and address.is_loopback):
            raise ValueError("Remote Node Exporter credentials require TLS")
        if (type(self.interval) is not int or not 1 <= self.interval <= 60
                or type(self.ttl) is not int or not max(5, self.interval) <= self.ttl <= 300
                or type(self.timeout) not in {int, float} or not math.isfinite(self.timeout) or not 0 < self.timeout <= 6):
            raise ValueError("Node Exporter collection budgets are invalid")


def fetch_node_metrics(settings: NodeExporterSettings) -> str:
    """One bounded TCP scrape, no proxy environment, redirects or TLS bypass."""
    settings.validate()
    deadline = time.monotonic() + settings.timeout

    def prepare():
        from .agent_credentials import read_private_file
        from .observability import register_secret
        context = (ssl.create_default_context(cafile=settings.ca_file or None)
                   if urlsplit(settings.url).scheme == "https" else ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
        context.keylog_filename = None
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        headers = {"Accept": "text/plain; version=0.0.4", "Accept-Encoding": "identity"}
        auth = None
        if settings.token_file:
            token = read_private_file(Path(settings.token_file), maximum=4096).decode("utf-8").strip()
            if not token or any(not 33 <= ord(c) <= 126 for c in token):
                raise ValueError("Invalid Node Exporter authentication material")
            register_secret(token)
            headers["Authorization"] = "Bearer " + token
        if settings.basic_auth_file:
            value = json.loads(read_private_file(Path(settings.basic_auth_file), maximum=8192))
            if (not isinstance(value, dict) or set(value) != {"username", "password"}
                    or any(not isinstance(v, str) or not 1 <= len(v) <= 4096
                           or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in value.values())
                    or ":" in value["username"]):
                raise ValueError("Invalid Node Exporter authentication material")
            register_secret(value["password"])
            auth = httpx.BasicAuth(value["username"], value["password"])
        return context, headers, auth

    async def scrape():
        context, headers, auth = await asyncio.to_thread(prepare)
        data = bytearray()
        async with httpx.AsyncClient(verify=context, auth=auth, trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(2, connect=1, pool=1),
                                     limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
            async with client.stream("GET", settings.url, headers=headers) as response:
                if (response.status_code != 200
                        or response.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}):
                    raise ValueError("Node Exporter scrape was not successful and uncompressed")
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isascii() or not declared.isdecimal() or int(declared) > MAX_BYTES):
                    raise ValueError("Node Exporter response exceeds its size budget")
                async for chunk in response.aiter_raw():
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise ValueError("Node Exporter response exceeds its size budget")
                    data.extend(chunk)
        return data.decode("utf-8")

    return run_network(scrape, deadline)


def _labels(raw):
    result, offset = {}, 0
    for match in _LABEL.finditer(raw):
        if match.start() != offset or match[1] in result or len(result) >= 32:
            raise ValueError("Invalid metric labels")
        value = re.sub(r'\\([\\"n])', lambda m: "\n" if m[1] == "n" else m[1], match[2])
        if len(value) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Invalid metric label value")
        result[match[1]] = value
        offset = match.end()
    if offset != len(raw):
        raise ValueError("Invalid metric labels")
    return result


def parse_node_metrics(text: str, *, captured: datetime, ttl: int) -> dict:
    """Project only the host fields consumed by Agent; reject ambiguous series."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES or "\x00" in text:
        raise ValueError("Node Exporter exposition exceeds its bounds")
    samples, stamps, count = {}, [], 0
    for index, raw in enumerate(text.splitlines()):
        if index >= MAX_LINES or len(raw) > 8192:
            raise ValueError("Node Exporter exposition exceeds its bounds")
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        count += 1
        if count > MAX_SERIES:
            raise ValueError("Node Exporter series exceed their bounds")
        name = line.split("{", 1)[0].split(None, 1)[0]
        if name not in _WANTED:
            continue
        match = _SAMPLE.fullmatch(line)
        if match is None:
            raise ValueError("Invalid Node Exporter sample")
        labels = _labels(match[2] or "")
        value = float(match[3])
        if not math.isfinite(value) or value < 0 or value > 2**53 - 1:
            raise ValueError("Invalid Node Exporter value")
        key = (name, tuple(sorted(labels.items())))
        if key in samples:
            raise ValueError("Duplicate Node Exporter series")
        samples[key] = value
        if match[4]:
            # Prometheus text 0.0.4 timestamps are milliseconds since epoch.
            stamp = float(match[4]) / 1000
            if not math.isfinite(stamp) or stamp < 0 or stamp > captured.timestamp() + 15:
                raise ValueError("Invalid Node Exporter timestamp")
            observed = datetime.fromtimestamp(stamp, timezone.utc)
            stamps.append(observed)
    if not samples:
        raise ValueError("Node Exporter has no supported host metrics")
    observed = min(stamps) if stamps else captured
    if observed + timedelta(seconds=ttl) <= captured:
        raise ValueError("Node Exporter metrics have expired")
    scalar, cpus, online, collectors, os_info, uname = {}, {}, {}, {}, None, None
    for (name, pairs), value in samples.items():
        labels = dict(pairs)
        if name == "node_cpu_seconds_total":
            cpu, mode = labels.get("cpu", ""), labels.get("mode", "")
            if (set(labels) != {"cpu", "mode"} or not cpu.isascii() or not cpu.isdecimal()
                    or int(cpu) >= MAX_CPUS or str(int(cpu)) != cpu):
                raise ValueError("Invalid Node Exporter CPU identity")
            if mode in {"guest", "guest_nice"}:
                continue  # Already included in user/nice; never count twice.
            if mode not in _MODES:
                raise ValueError("Unsupported Node Exporter CPU mode")
            cpus.setdefault(cpu, {})[mode] = value
        elif name == "node_cpu_online":
            if set(labels) != {"cpu"} or value not in {0, 1}:
                raise ValueError("Invalid Node Exporter online CPU")
            online[labels["cpu"]] = value
        elif name == "node_scrape_collector_success":
            if set(labels) != {"collector"} or value not in {0, 1}:
                raise ValueError("Invalid Node Exporter collector state")
            collectors[labels["collector"]] = value
        elif name in {"node_os_info", "node_uname_info"}:
            if value != 1 or any(len(v) > 128 for v in labels.values()):
                raise ValueError("Invalid Node Exporter host identity")
            if name == "node_os_info":
                if os_info is not None:
                    raise ValueError("Ambiguous Node Exporter OS")
                os_info = {key: labels.get(key, "") for key in ("id", "name", "pretty_name", "version_id")}
            else:
                if uname is not None:
                    raise ValueError("Ambiguous Node Exporter uname")
                uname = {key: labels.get(key, "") for key in ("sysname", "release", "machine")}
        else:
            if labels:
                raise ValueError("Ambiguous Node Exporter scalar")
            scalar[name] = value
    if (not cpus or any(set(modes) != _MODES for modes in cpus.values())
            or online and set(cpus) != {cpu for cpu, state in online.items() if state == 1}
            or collectors.get("cpu", 1) != 1):
        cpus = None
    total, available = scalar.get("node_memory_MemTotal_bytes"), scalar.get("node_memory_MemAvailable_bytes")
    if (total is None or available is None or not total.is_integer() or not available.is_integer()
            or total <= 0 or available > total or collectors.get("meminfo", 1) != 1):
        total, available = None, None
    if collectors.get("os", 1) != 1:
        os_info = None
    if collectors.get("uname", 1) != 1:
        uname = None
    return {"cpus": cpus, "memory_total_bytes": int(total) if total is not None else None,
            "memory_available_bytes": int(available) if available is not None else None,
            "os_info": os_info, "uname_info": uname, "boot_time_seconds": scalar.get("node_boot_time_seconds"),
            "collected_at": observed.isoformat(), "expires_at": (observed + timedelta(seconds=ttl)).isoformat(),
            "timestamp_source": "exporter" if stamps else "scrape"}


def _error(code):
    messages = {
        "warming": "CPU 使用率需要连续两次有效采样，正在建立基线",
        "counter_reset": "CPU 累计计数已重置或未推进，本次不计算使用率",
        "incomplete_cpu": "CPU 核心或模式集合不完整，本次不计算使用率",
        "incomplete_metrics": "部分宿主指标缺失或对应 collector 失败，缺失值保持未知",
        "stale": "宿主指标已过期，等待新的有效采样",
        "collecting": "宿主指标正在采集，未排队重复请求",
        "collection_failed": "Node Exporter 读取失败；检查采集器、受信任端点与证书",
        "invalid_configuration": "Node Exporter 部署配置无效，未尝试其他数据来源",
    }
    return {"source": "node_exporter", "code": code, "message": messages[code]}


def unavailable_node_metrics(code):
    """Return explicit unknowns without reading files or contacting an endpoint."""
    return {"os": "", "cpu_percent": None, "memory_percent": None,
            "host_metrics": {"source": "node-exporter", "status": "error", "state": code,
                             "collected_at": None, "expires_at": None, "errors": [_error(code)],
                             "memory_total_bytes": None, "memory_available_bytes": None,
                             "os_info": None, "uname_info": None}}


class NodeExporterSampler:
    """One in-flight scrape per Agent, bounded cache and independent source age."""

    def __init__(self, settings: NodeExporterSettings, *, reader=fetch_node_metrics,
                 clock=time.monotonic, wall_clock=lambda: datetime.now(timezone.utc)):
        settings.validate()
        self.settings, self.reader, self.clock, self.wall_clock = settings, reader, clock, wall_clock
        self.lock = threading.Lock()
        self.cache, self.baseline = None, None
        self.next_scrape, self.expires = 0.0, 0.0

    def _cached(self):
        if self.cache is None:
            return unavailable_node_metrics("collecting")
        result = copy.deepcopy(self.cache)
        if self.expires > 0 and self.clock() >= self.expires:
            result.update(os="", cpu_percent=None, memory_percent=None)
            result["host_metrics"].update(status="error", state="stale", memory_total_bytes=None,
                                          memory_available_bytes=None, os_info=None, uname_info=None,
                                          errors=[_error("stale")])
        return result

    def collect(self):
        if self.clock() < self.next_scrape or not self.lock.acquire(blocking=False):
            return self._cached()
        try:
            if self.clock() < self.next_scrape:
                return self._cached()
            started, captured = elapsed_ns(), self.wall_clock()
            stamp = self.clock()
            try:
                sample = parse_node_metrics(self.reader(self.settings), captured=captured, ttl=self.settings.ttl)
                cpu, state = None, "warming"
                previous = self.baseline
                if sample["cpus"] is not None:
                    if (previous is not None and stamp - previous[0] <= self.settings.ttl
                            and sample["cpus"].keys() == previous[1]["cpus"].keys()
                            and sample["boot_time_seconds"] == previous[1]["boot_time_seconds"]
                            and sample["uname_info"] == previous[1]["uname_info"]):
                        deltas = {key: {mode: modes[mode] - previous[1]["cpus"][key][mode] for mode in _MODES}
                                  for key, modes in sample["cpus"].items()}
                        # Any reset, hotplug/missing core or zero counter interval
                        # invalidates the complete host percentage, never just a core.
                        valid = all(all(v >= 0 for v in modes.values()) and sum(modes.values()) > 0 for modes in deltas.values())
                        if valid:
                            total = sum(sum(modes.values()) for modes in deltas.values())
                            idle = sum(modes["idle"] + modes["iowait"] for modes in deltas.values())
                            cpu, state = round((total - idle) / total * 100, 3), "ready"
                        else:
                            state = "counter_reset"
                    self.baseline = (stamp, sample)
                else:
                    state, self.baseline = "incomplete_cpu", None
                total, available = sample["memory_total_bytes"], sample["memory_available_bytes"]
                memory = round((total - available) / total * 100, 3) if total is not None else None
                os_info = sample["os_info"]
                os_name = (os_info.get("pretty_name") or " ".join(v for v in (os_info.get("name"), os_info.get("version_id")) if v)) if os_info else ""
                metrics = {key: value for key, value in sample.items() if key != "cpus"}
                complete = cpu is not None and memory is not None and bool(os_name) and bool(sample["uname_info"])
                if cpu is not None and not complete:
                    state = "incomplete_metrics"
                metrics.update(source="node-exporter", status="ready" if complete else "partial", state=state,
                               cpu_count=len(sample["cpus"]) if sample["cpus"] else None,
                               errors=[] if complete else [_error(state)])
                if sample["timestamp_source"] == "scrape":
                    mark_sample(metrics, started_ns=started)
                result = {"os": os_name, "cpu_percent": cpu, "memory_percent": memory, "host_metrics": metrics}
                self.expires = stamp + max(0, (datetime.fromisoformat(sample["expires_at"]) - captured).total_seconds())
                if self.clock() >= self.expires:
                    raise ValueError("Node Exporter scrape expired during collection")
            except (OSError, ValueError, UnicodeError, httpx.HTTPError, TimeoutError):
                result, self.baseline, self.expires = unavailable_node_metrics("collection_failed"), None, 0
            self.cache = result
            self.next_scrape = self.clock() + self.settings.interval
            return self._cached()
        finally:
            self.lock.release()


@lru_cache(maxsize=4)
def _sampler(settings: NodeExporterSettings):
    return NodeExporterSampler(settings)


_sampler_guard = threading.Lock()


def collect_node_metrics():
    try:
        with _sampler_guard:
            sampler = _sampler(NodeExporterSettings.environment())
        return sampler.collect()
    except (ValueError, OSError):
        # Configuration errors are public fixed codes, never endpoint/secret text.
        return unavailable_node_metrics("invalid_configuration")
