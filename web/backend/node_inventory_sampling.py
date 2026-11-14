"""Bounded background observations; no remote mutation and no per-browser polling."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import threading
import time

from .agent_metadata_projection import AGENT_META_BYTES, AgentMetadataError, project_agent_metadata
from .fleet import FleetError
from .observability import redact
from .node_configuration import nic_configuration
from .resource_inventory import InventoryError, MAX_INVENTORY_BYTES, inventory_snapshot, inventory_view, project_inventory
from .store import now
from .version import AGENT_PROTOCOL_VERSION


def _setting(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, str(default))
    if not raw.isascii() or not raw.isdecimal() or not low <= int(raw) <= high:
        raise ValueError(name + " is outside its supported bounds")
    return int(raw)


class ObservationBusy(InventoryError):
    """An existing read owns the finite collection budget; do not queue it."""


def _same_connection(left: dict, right: dict) -> bool:
    # Display-name/label revisions do not change the observation source. The
    # result merges onto the latest record, preserving those concurrent edits.
    return (all(left.get(key) == right.get(key) for key in
                ("agent_url", "_token", "_agent_ca_pem", "credential_revision", "address"))
            and nic_configuration(left)[0] == nic_configuration(right)[0])


def _duplicate_identity(value: dict, others: list[dict]) -> list[str]:
    if value.get("docker_available") is False:
        return []
    return sorted(node["id"] for node in others if node.get("id") != value.get("id")
                  and node.get("docker_available") is not False
                  and ((value.get("daemon_id") and node.get("daemon_id") == value["daemon_id"])
                       or (value.get("host_identity") and node.get("host_identity") == value["host_identity"])))[:32]


def _cas_observation(store, identifier: str, expected: dict, value: dict, *, manual=False) -> bool:
    """Snapshots are not user mutations; avoid thousands of routine audit records."""
    with store.unit_of_work(write=True) as unit:
        if not unit.resources.compare_and_replace("nodes", identifier, expected, value):
            return False
        if manual:
            store.audit("探测节点", value["name"], "failed" if value.get("last_sample_error") else "success",
                        value.get("error", ""), metadata={"inventory_status": value.get("inventory_status", "partial")},
                        connection=unit)
    return True


def collect_node_observation(store, fleet, mutation_lock, identifier: str, *, manual: bool = False,
                             mode: str = "control", stopped: threading.Event | None = None) -> dict:
    """Network reads run outside SQL/locks; only a still-current identity may be updated."""
    with mutation_lock:
        original = store.get("nodes", identifier)
        if original is None:
            raise KeyError("Node no longer exists")
    started = now()
    updates: dict = {}
    if mode == "demo":
        return original
    try:
        projected = project_inventory(fleet.call(original, "GET", "/agent/inventory", timeout=10,
                                                 max_response_bytes=MAX_INVENTORY_BYTES))
        updates.update(projected)
        reported = updates.pop("nic_name", "")
        updates["reported_nic_name"] = reported
        updates.update(status="online", last_seen=now(), error="", last_sample_error="")
        metadata_due = True
        try:
            checked = datetime.fromisoformat(original.get("agent_metadata_checked_at", ""))
            metadata_due = checked.tzinfo is None or checked < datetime.now(timezone.utc) - timedelta(minutes=5)
        except ValueError:
            pass
        if manual or metadata_due:
            try:
                metadata = project_agent_metadata(fleet.call(original, "GET", "/agent/meta", timeout=8,
                                                             max_response_bytes=AGENT_META_BYTES))
                updates["agent_metadata_checked_at"] = now()
                if metadata["agent_protocol"] == AGENT_PROTOCOL_VERSION:
                    updates.update(agent_metadata=metadata, compatibility_error="")
                else:
                    updates["compatibility_error"] = "Agent protocol is unsupported; writes will be refused"
            except (FleetError, AgentMetadataError) as error:
                updates["compatibility_error"] = str(redact(str(error)))[:512]
    except (FleetError, InventoryError) as error:
        if isinstance(error, FleetError) and error.status_code == 429:
            raise ObservationBusy("Agent 正在采集或等待退避，请稍后读取结果") from error
        detail = str(redact(str(error)))[:512]
        updates.update(status="offline" if isinstance(error, FleetError) else "online", error=detail,
                       inventory_status="error", last_sample_error=detail,
                       collection_errors=[{"source": "agent_inventory", "code": "transport_failed" if isinstance(error, FleetError) else "invalid_response",
                                           "message": detail}])
    if stopped is not None and stopped.is_set():
        return original
    with mutation_lock:
        if stopped is not None and stopped.is_set():
            return original
        latest = store.get("nodes", identifier)
        if latest is None:
            raise KeyError("Node no longer exists")
        if not _same_connection(original, latest) or latest.get("last_probe_started", "") > started:
            raise InventoryError("节点已更新，本次旧观测结果未应用")
        value = {**latest, **updates, "last_probe_started": started, "last_sample_attempt_at": now()}
        configured, previous_reported, _ = nic_configuration(latest)
        value["configured_nic_name"] = configured
        value["reported_nic_name"] = updates.get("reported_nic_name", previous_reported)
        value["nic_name"] = configured or value["reported_nic_name"]
        previous = inventory_view(latest)
        if previous.get("resource_freshness") == "fresh":
            value["last_good_inventory"] = inventory_snapshot(latest)
        duplicates = _duplicate_identity(value, store.list("nodes"))
        if duplicates:
            value["identity_conflict"] = {"code": "duplicate_execution_host", "other_node_ids": duplicates,
                                           "message": "同一宿主或 Docker daemon 已由其他执行节点接入；资源未重复计入"}
        else:
            value.pop("identity_conflict", None)
        persisted = _cas_observation(store, identifier, latest, value, manual=manual)
        if not persisted:
            raise InventoryError("节点记录已更新，本次旧观测结果未应用")
    return value


class NodeInventorySampler:
    def __init__(self, store, fleet, mutation_lock, *, mode: str = "control"):
        self.store, self.fleet, self.mutation_lock, self.mode = store, fleet, mutation_lock, mode
        self.interval = _setting("MOTOR_NODE_SAMPLE_SECONDS", 15, 5, 300)
        self.workers = _setting("MOTOR_NODE_SAMPLE_CONCURRENCY", 3, 1, 3)
        self.stopped = threading.Event()
        self.guard = threading.Lock()
        self.pending: set[str] = set()
        self.active: set[str] = set()
        self.manual_started: dict[str, float] = {}
        self.next_due: dict[str, float] = {}
        self.failures: dict[str, int] = {}
        self.executor: ThreadPoolExecutor | None = None
        self.thread: threading.Thread | None = None

    def collect(self, identifier: str, *, manual: bool = False) -> dict:
        # The background executor alone cannot bound manually requested reads.
        # All entry points share one non-blocking global/per-node admission.
        with self.guard:
            if self.stopped.is_set():
                raise ObservationBusy("节点采集正在关闭，请稍后重试")
            reserved = self.pending | self.active
            if identifier in self.active or manual and identifier in self.pending:
                raise ObservationBusy("该节点已有采集进行中，请稍后读取结果")
            if len(self.active) >= self.workers or manual and len(reserved) >= self.workers:
                raise ObservationBusy("节点采集并发已达上限，请稍后重试")
            if manual:
                tick = time.monotonic()
                if tick - self.manual_started.get(identifier, float("-inf")) < 2:
                    raise ObservationBusy("该节点刚请求过采集，请稍后读取结果")
                self.manual_started[identifier] = tick
            self.active.add(identifier)
        try:
            result = collect_node_observation(self.store, self.fleet, self.mutation_lock, identifier,
                                              manual=manual, mode=self.mode, stopped=self.stopped)
            if manual:
                with self.guard:
                    success = result.get("status") == "online" and not result.get("last_sample_error")
                    failures = 0 if success else min(5, self.failures.get(identifier, 0) + 1)
                    self.failures[identifier] = failures
                    self.next_due[identifier] = time.monotonic() + min(300, self.interval * 2**failures)
            return result
        finally:
            with self.guard:
                self.active.discard(identifier)

    def start(self) -> None:
        if self.mode != "control" or self.thread is not None:
            return
        self.executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="node-inventory")
        self.thread = threading.Thread(target=self._run, name="node-inventory-sampler", daemon=True)
        self.thread.start()

    def _sample(self, identifier: str) -> None:
        succeeded = False
        busy = False
        try:
            if self.stopped.is_set():
                return
            result = self.collect(identifier)
            succeeded = result.get("status") == "online" and not result.get("last_sample_error")
        except ObservationBusy:
            busy = True
        except Exception as error:
            # The sampler has no permission to turn unexpected failures into synthetic success.
            logging.getLogger(__name__).warning("Node inventory sampling failed (%s)", type(error).__name__)
            succeeded = False
        finally:
            with self.guard:
                self.pending.discard(identifier)
                if busy:
                    self.next_due[identifier] = time.monotonic() + 2
                else:
                    failure = 0 if succeeded else min(5, self.failures.get(identifier, 0) + 1)
                    self.failures[identifier] = failure
                    self.next_due[identifier] = time.monotonic() + min(300, self.interval * 2**failure)

    def _run(self) -> None:
        while not self.stopped.is_set():
            try:
                nodes = self.store.list("nodes")
                identifiers = {node["id"] for node in nodes}
                with self.guard:
                    for mapping in (self.next_due, self.failures, self.manual_started):
                        for identifier in set(mapping) - identifiers:
                            mapping.pop(identifier, None)
                    available = self.workers - len(self.pending | self.active)
                    # Only active workers are submitted: executor has no unbounded waiting queue.
                    candidates = sorted((node for node in nodes if node.get("agent_url") and node["id"] not in (self.pending | self.active)
                                         and self.next_due.get(node["id"], 0) <= time.monotonic()),
                                        key=lambda node: self.next_due.get(node["id"], 0))[:max(0, available)]
                    for node in candidates:
                        self.pending.add(node["id"])
                        self.executor.submit(self._sample, node["id"])
            except Exception as error:
                # Database/logging shutdown or temporary corruption must not spin or submit mutations.
                logging.getLogger(__name__).warning("Node inventory scheduling failed (%s)", type(error).__name__)
            self.stopped.wait(1)

    def close(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
