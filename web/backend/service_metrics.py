"""Bounded, service-scoped read-only metric snapshots.

This cache never derives rates, changes Motor state, or polls in the background.
Browser sessions can calculate clearly scoped rates from successive counters;
Motor's reported rates remain available in the unmodified raw snapshot.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import time
from typing import Callable

from .fleet import FleetError, motor_request


# Keep endpoint identity so a reset/disappearing endpoint cannot be hidden by
# another endpoint's increasing counter. This is an existing native API view.
METRICS_PATH = "/metrics?type=dp&format=prometheus"
MAX_METRICS_BYTES = 2 * 1024 * 1024
ACTIVE_STATES = frozenset({"running", "starting", "partial"})


def metrics_revision(service: dict, nodes: dict, configuration_digest: str) -> str:
    """Fingerprint intent and execution identity, excluding derived observations."""
    placements = service.get("placements") or []
    identifiers = sorted({placement.get("node_id") for placement in placements
                          if isinstance(placement, dict) and isinstance(placement.get("node_id"), str)})
    node_fields = ("address", "agent_url", "credential_revision", "_token", "_agent_ca_pem")
    value = {key: service.get(key) for key in (
        "id", "updated_at", "runtime", "deployed_runtime", "deployed_digest", "last_job_id",
        "deployed_placements", "motor_profile", "deployed_motor_profile", "resources_may_exist",
        "has_unapplied_changes")}
    value.update(configuration_digest=configuration_digest,
                 lifecycle="active" if service.get("status") in ACTIVE_STATES else service.get("status"),
                 nodes={identifier: {key: nodes.get(identifier, {}).get(key) for key in node_fields}
                        for identifier in identifiers})
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class _Entry:
    revision: str
    attempted_at: float
    response: dict | None = None
    expires_monotonic: float = 0


class ServiceMetricsSampler:
    """Single-flight per service, a shared nonqueueing budget, and a small LRU."""

    def __init__(self, *, interval_seconds: int = 10, ttl_seconds: int = 20,
                 max_services: int = 64, max_concurrent: int = 8,
                 reader: Callable | None = None, clock: Callable = time.monotonic,
                 wall_clock: Callable | None = None):
        if not (type(interval_seconds) is int and interval_seconds >= 1
                and type(ttl_seconds) is int and ttl_seconds >= interval_seconds
                and type(max_services) is int and 1 <= max_services <= 64
                and type(max_concurrent) is int and 1 <= max_concurrent <= 8):
            raise ValueError("Invalid service metric sampling bounds")
        self.interval = interval_seconds
        self.ttl = ttl_seconds
        self.max_services = max_services
        self.reader = reader or motor_request
        self.clock = clock
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(max_concurrent)
        self.entries: OrderedDict[str, _Entry] = OrderedDict()
        self.active: set[str] = set()
        self.closed = False

    def close(self):
        with self.lock:
            self.closed = True
            self.entries.clear()

    def _empty(self, identifier, revision, state, error, *, cache_hit=False):
        return {"service_id": identifier, "revision": revision, "state": state,
                "metrics_text": "", "sampled_at": None, "expires_at": None,
                "cache_hit": cache_hit, "interval_seconds": self.interval, "error": error}

    def _cached(self, entry: _Entry, identifier: str, revision: str, stamp: float):
        if entry.response is None:
            return self._empty(identifier, revision, "warming", "正在读取服务指标，请稍后刷新。", cache_hit=True)
        result = {**entry.response, "cache_hit": True}
        if result["state"] == "ready" and stamp >= entry.expires_monotonic:
            result.update(state="stale", metrics_text="", error="上一次指标采样已过期，等待新的有效采样。")
        return result

    @staticmethod
    def _read_kubernetes(fleet, service: dict, nodes: dict) -> str:
        placements = service.get("placements") or []
        if len(placements) != 1 or placements[0].get("node_id") not in nodes:
            raise FleetError("Kubernetes metric execution node is unavailable")
        node = nodes[placements[0]["node_id"]]
        payload = fleet.call(node, "GET", "/agent/kubernetes/observations?service_id=" + service["id"],
                             timeout=5, max_response_bytes=MAX_METRICS_BYTES)
        if not isinstance(payload, dict):
            raise FleetError("Kubernetes metric response is invalid")
        # An independent workload/readiness warning does not invalidate a valid
        # metric read, but a metric-plane failure must never reuse old text.
        if any(isinstance(error, dict) and "metrics" in str(error.get("source", ""))
               for error in payload.get("errors", [])):
            raise FleetError("Kubernetes metric plane read failed")
        return payload.get("metrics_text", "")

    def sample(self, service: dict, nodes: dict, *, revision: str,
               is_current: Callable[[], bool], fleet=None, demo=False) -> dict:
        result = self._sample(service, nodes, revision=revision, is_current=is_current,
                              fleet=fleet, demo=demo)
        # Kubernetes retains the established Agent full-view contract. It must
        # not be represented as endpoint-level reset detection in the client.
        return {**result, "metrics_view": "full" if service.get("runtime") == "kubernetes" else "dp"}

    def _sample(self, service: dict, nodes: dict, *, revision: str,
                is_current: Callable[[], bool], fleet=None, demo=False) -> dict:
        identifier = service["id"]
        if demo:
            return {**self._empty(identifier, revision, "demo", "演示环境不采集真实服务指标。"), "demo": True}
        if service.get("status") not in ACTIVE_STATES or not service.get("resources_may_exist"):
            with self.lock:
                previous = self.entries.get(identifier)
                if previous is not None:
                    self.entries[identifier] = _Entry(revision, previous.attempted_at)
            return self._empty(identifier, revision, "unavailable", "服务尚未运行或正在执行生命周期操作，未连接指标端点。")
        if service.get("has_unapplied_changes"):
            return self._empty(identifier, revision, "unavailable", "服务存在尚未应用的配置，请先确认已部署修订的指标地址。")

        with self.lock:
            stamp = self.clock()
            if self.closed:
                return self._empty(identifier, revision, "unavailable", "指标采样器正在关闭。")
            entry = self.entries.get(identifier)
            if entry is not None and entry.revision != revision:
                # Revision changes invalidate values, but cannot bypass the
                # per-service minimum request interval or an in-flight read.
                entry = _Entry(revision, entry.attempted_at)
                self.entries[identifier] = entry
            if entry is not None:
                self.entries.move_to_end(identifier)
            if identifier in self.active or entry is not None and stamp - entry.attempted_at < self.interval:
                return self._cached(entry, identifier, revision, stamp) if entry else self._empty(
                    identifier, revision, "warming", "上一轮服务指标仍在读取，请稍后刷新。")
            if not self.slots.acquire(blocking=False):
                return self._cached(entry, identifier, revision, stamp) if entry else self._empty(
                    identifier, revision, "warming", "当前指标采样繁忙，请稍后刷新。")
            if entry is None and len(self.entries) >= self.max_services:
                removable = next((key for key, value in self.entries.items()
                                  if key not in self.active and stamp - value.attempted_at >= self.interval), None)
                if removable is None:
                    self.slots.release()
                    return self._empty(identifier, revision, "warming", "当前指标采样繁忙，请稍后刷新。")
                self.entries.pop(removable)
            # Other browsers can keep the same fresh sample while this read
            # is in flight. Carry its original expiry; a failed refresh below
            # replaces it with an explicit unavailable result.
            entry = _Entry(revision, stamp, response=entry.response if entry else None,
                           expires_monotonic=entry.expires_monotonic if entry else 0)
            self.entries[identifier] = entry
            self.entries.move_to_end(identifier)
            self.active.add(identifier)

        try:
            try:
                if service.get("runtime") == "kubernetes":
                    text = self._read_kubernetes(fleet, service, nodes)
                elif service.get("runtime") == "docker":
                    text = self.reader(service, nodes, "obs", METRICS_PATH,
                                       timeout=3, max_response_bytes=MAX_METRICS_BYTES)
                else:
                    raise FleetError("Unsupported metric runtime")
                if not isinstance(text, str) or not text.strip() or "\x00" in text \
                        or len(text.encode("utf-8")) > MAX_METRICS_BYTES:
                    raise FleetError("Metric snapshot is empty or invalid")
                completed_at = self.clock()
                sampled_at = self.wall_clock()
                result = {"service_id": identifier, "revision": revision, "state": "ready", "metrics_text": text,
                          "sampled_at": sampled_at.isoformat(),
                          "expires_at": (sampled_at + timedelta(seconds=self.ttl)).isoformat(),
                          "cache_hit": False, "interval_seconds": self.interval}
                expires = completed_at + self.ttl
            except Exception:
                # Upstream exceptions can contain endpoint URLs, keys or file
                # paths. The public diagnostic is deliberately fixed text.
                scope = "Kubernetes Agent 指标" if service.get("runtime") == "kubernetes" else "Coordinator 指标"
                result = self._empty(identifier, revision, "unavailable",
                                     scope + "读取失败，请核对服务、访问权限和管理连接后重试。")
                expires = 0
            if not is_current():
                result = self._empty(identifier, revision, "stale", "服务修订、执行节点或运行状态已变化，本次旧采样已丢弃。")
                expires = 0
            with self.lock:
                if self.closed:
                    return self._empty(identifier, revision, "unavailable", "指标采样器正在关闭。")
                current = self.entries.get(identifier)
                if current is not entry:
                    return self._empty(identifier, revision, "stale", "服务修订已变化，本次旧采样已丢弃。")
                entry.response = result.copy()
                entry.expires_monotonic = expires
            return result
        finally:
            with self.lock:
                self.active.discard(identifier)
            self.slots.release()
