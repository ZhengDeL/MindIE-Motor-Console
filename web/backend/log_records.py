"""Bounded log views with source identity, original clocks and explicit severity.

These views are snapshots of recent tails, not a complete or immutable archive.
No timestamp is synthesized for a malformed/missing source clock. A source gets
a fair quota before spare capacity is filled by the most recent known records.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import contextvars
from datetime import datetime, timezone
import json
import re
import threading
import time
from typing import Callable

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[Tt](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?([Zz]|[+-]\d{2}:\d{2})$")
_LEVELS = {"TRACE": "TRACE", "DEBUG": "DEBUG", "INFO": "INFO", "WARN": "WARN",
           "WARNING": "WARN", "ERROR": "ERROR", "CRITICAL": "FATAL", "FATAL": "FATAL"}
_TOKEN = r"TRACE|DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL"
_PREFIXES = (
    re.compile(r"^(?:\([^\r\n()]{1,128}\)\s+)?(" + _TOKEN + r")(?=\s|:|\[|$)"),
    re.compile(r"^\[(" + _TOKEN + r")\](?=\s|:|$)"),
    re.compile(r"^\[(?:rank|Rank)\d{1,5}\]\s+(" + _TOKEN + r")(?=\s|:|\[|$)"),
    re.compile(r"^\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:\d{2})?\]?\s*(?:[-|]\s*)?\[?(" + _TOKEN + r")\]?(?=\s|:|\[|$)"),
)
_POD_PREFIX = re.compile(r"^\[pod/([a-z0-9][a-z0-9.-]{0,252})/(?:container/)?([a-z0-9][a-z0-9-]{0,62})\]\s+")
_ROLES = {"prefill", "decode", "encode", "union", "coordinator", "controller", "coordinator_controller",
          "single_container", "kv_store", "kv_conductor", "render", "management"}


def validate_log_role(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 48 or not re.fullmatch(r"[a-z][a-z0-9_-]*", value):
        raise ValueError("role must be a supported literal log role")
    value = value.replace("-", "_")
    if value not in _ROLES:
        raise ValueError("unsupported log role")
    return value


def role_matches(actual: str | None, requested: str | None) -> bool:
    if requested is None:
        return True
    # Combined management containers remain labelled as combined; selecting a
    # component does not pretend to split that container's unstructured output.
    if requested == "management":
        return actual in {"coordinator", "controller", "coordinator_controller", "render"}
    if requested in {"coordinator", "controller"}:
        return actual in {requested, "coordinator_controller"}
    return actual == requested


def timestamp_ns(value) -> int | None:
    if not isinstance(value, str):
        return None
    match = _STAMP.fullmatch(value)
    if not match:
        return None
    date, clock, fraction, offset = match.groups()
    try:
        parsed = datetime.fromisoformat(date + "T" + clock + ("+00:00" if offset.upper() == "Z" else offset))
        delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + int((fraction or "0").ljust(9, "0"))
    except (ValueError, OverflowError):
        return None


def severity(message: str) -> tuple[str, str]:
    clean = _ANSI.sub("", message).lstrip()
    if clean.startswith("{") and len(clean) <= 32768:
        try:
            document = json.loads(clean)
        except (ValueError, TypeError):
            document = None
        if isinstance(document, dict):
            for name in ("level", "levelname"):
                value = document.get(name)
                if isinstance(value, str) and value.upper() in _LEVELS:
                    return _LEVELS[value.upper()], "json_" + name
            return "UNKNOWN", "unclassified"
    for prefix in _PREFIXES:
        match = prefix.match(clean)
        if match:
            return _LEVELS[match[1]], "prefix"
    return "UNKNOWN", "unclassified"


def log_record(line: str, *, source: str, node: str, role: str = "", container_id: str = "",
               container_name: str = "", pod: str = "", namespace: str = "") -> dict:
    original = line
    prefix = _POD_PREFIX.match(line)
    if prefix:
        if pod and prefix[1] != pod or container_name and prefix[2] != container_name:
            raise ValueError("kubectl log prefix does not match the requested Pod/container")
        pod, container_name = prefix[1], prefix[2]
        line = line[prefix.end():]
    token, separated, remainder = line.partition(" ")
    valid = timestamp_ns(token) is not None
    message = remainder if valid and separated else line
    level, provenance = severity(message)
    return {"time": token if valid else "", "raw_timestamp": token if separated else "",
            "timestamp_valid": valid, "level": level, "level_source": provenance,
            "role": role, "node": node, "container_id": container_id, "container_name": container_name,
            "pod": pod, "namespace": namespace, "message": _ANSI.sub("", message),
            "raw_message": message, "raw_line": original, "source": source}


def normalize_log_record(value: dict) -> dict:
    allowed = {"time", "raw_timestamp", "timestamp_valid", "level", "level_source", "role", "node", "node_name",
               "agent_node_id", "container_id", "container_name", "pod", "namespace", "message", "raw_message",
               "raw_line", "source", "message_truncated", "observed_at"}
    item = {key: entry for key, entry in value.items() if key in allowed}
    message = item.get("raw_message", item.get("message", ""))
    message = message if isinstance(message, str) else str(message)
    if item.get("role") == "kubernetes" and not item.get("source"):
        # Old Agents wrapped the entire timestamped kubectl line and assigned
        # collection time to `time`. Recover its actual outer source clock; if
        # absent, leave it absent instead of preserving that invented clock.
        parsed = log_record(message, source="kubernetes_stdout", node="",
                            namespace=str(item.get("node") or ""))
        item.update(parsed)
        message = parsed["raw_message"]
    if item.get("level_source") != "platform":
        item["level"], item["level_source"] = severity(message)
    item["raw_message"] = message
    item["message"] = _ANSI.sub("", message)
    item["timestamp_valid"] = timestamp_ns(item.get("time")) is not None
    # Preserve an unparseable supplied timestamp; never substitute collection time.
    return item


def source_key(item: dict) -> tuple:
    return tuple(str(item.get(key) or "") for key in
                 ("agent_node_id", "node", "namespace", "pod", "role", "container_id", "container_name", "source"))


def fair_tail(values: list[dict], limit: int) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 2000:
        raise ValueError("log tail must be in 1..2000")
    indexed = [(index, normalize_log_record(value)) for index, value in enumerate(values) if isinstance(value, dict)]
    def order(entry):
        index, item = entry
        stamp = timestamp_ns(item.get("time"))
        return (stamp is None, stamp or 0, source_key(item), index)
    groups = {}
    for entry in indexed:
        groups.setdefault(source_key(entry[1]), []).append(entry)
    if not groups:
        return []
    quota = limit // len(groups)
    selected, remaining = [], []
    for key in sorted(groups):
        group = sorted(groups[key], key=order)
        if quota:
            selected.extend(group[-quota:]); remaining.extend(group[:-quota])
        else:
            remaining.extend(group)
    if len(selected) < limit:
        selected.extend(sorted(remaining, key=order)[-(limit - len(selected)):])
    return [value for _index, value in sorted(selected, key=order)]


def bounded_stream_bytes(stream, *, deadline: float, maximum: int) -> tuple[bytes, bool]:
    data = bytearray(); truncated = False
    try:
        for chunk in stream:
            if time.monotonic() >= deadline:
                truncated = True; break
            if not isinstance(chunk, bytes):
                raise ValueError("Docker log stream returned non-byte data")
            available = maximum - len(data)
            data.extend(chunk[:available])
            if len(chunk) > available or len(data) >= maximum:
                truncated = True; break
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    return bytes(data), truncated


class ServiceLogReader:
    """A shared bounded worker pool; each request also has one total deadline."""
    def __init__(self, workers=4, pending=8):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="motor-log-read")
        self._slots = threading.BoundedSemaphore(pending)
        self._closed = False
        self._guard = threading.Lock()
        self._workers = workers

    def close(self):
        with self._guard:
            if self._closed:
                return
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def read(self, targets: list[dict], fetch: Callable, *, seconds=20) -> tuple[list[dict], list[dict]]:
        deadline = time.monotonic() + seconds
        pending, results, errors = {}, [], []
        cursor = 0
        def failure(target, code):
            errors.append({"node_id": target["id"], "node_name": target.get("name", ""), "code": code})
        def submit(target):
            if self._closed or not self._slots.acquire(blocking=False):
                failure(target, "log_reader_busy"); return
            def work():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("log aggregate total deadline")
                return fetch(target, min(12, remaining))
            context = contextvars.copy_context()
            try:
                future = self._pool.submit(context.run, work)
            except RuntimeError:
                self._slots.release(); failure(target, "log_reader_closed"); return
            future.add_done_callback(lambda _future: self._slots.release())
            pending[future] = target
        while cursor < len(targets) or pending:
            while cursor < len(targets) and len(pending) < self._workers and time.monotonic() < deadline:
                submit(targets[cursor]); cursor += 1
            remaining = deadline - time.monotonic()
            if not pending or remaining <= 0:
                break
            finished, _ = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not finished:
                break
            for future in finished:
                target = pending.pop(future)
                try:
                    response = future.result()
                    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
                        raise ValueError("invalid log response")
                    for item in response["items"][:2000]:
                        if isinstance(item, dict):
                            results.append({**item, "agent_node_id": target["id"], "node_name": target.get("name", "")})
                    if response.get("partial") or response.get("truncated"):
                        failure(target, "source_log_window_partial")
                    if len(response["items"]) > 2000:
                        failure(target, "source_log_window_partial")
                    errors.extend({"node_id": target["id"], "node_name": target.get("name", ""), "code": "source_log_error"}
                                  for _ in response.get("errors", [])[:32])
                except Exception:
                    # Exceptions may contain transport internals/secrets. The
                    # bounded source identity and code suffice for this view.
                    failure(target, "log_source_unavailable")
        for future, target in pending.items():
            future.cancel(); failure(target, "log_total_deadline")
        for target in targets[cursor:]:
            failure(target, "log_total_deadline")
        return results, errors
