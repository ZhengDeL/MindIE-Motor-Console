"""Bound resource ages to the authenticated response, independent of wall clocks.

Sample anchors are private to one Agent process and survive cache reads without
renewal. Only elapsed ages cross the wire. The control plane consumes them once;
stored observations subsequently expire against its own clock as before.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re
import time
import uuid

_CLOCK_ID = uuid.uuid4().hex
_MARK = "_sample_clock"
_NONCE = re.compile(r"[0-9a-f]{32}")
_MAX_AGE_MS = 366 * 24 * 60 * 60 * 1000
_PATHS = {"inventory": (), "host_metrics": ("host_metrics",),
          "npu_telemetry": ("npu_telemetry",),
          "npu_last_good": ("npu_telemetry", "last_good"),
          "resource_ledger": ("resource_ledger",)}


def elapsed_ns() -> int:
    # Linux CLOCK_MONOTONIC excludes suspend. BOOTTIME includes it, so resuming
    # a sleeping Agent cannot rejuvenate a pre-suspend cached observation.
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    return time.clock_gettime_ns(clock) if clock is not None else time.monotonic_ns()


def mark_sample(value: dict, *, started_ns: int | None = None, age_ms: int = 0,
                basis: str = "agent-elapsed") -> dict:
    if type(age_ms) is not int or not 0 <= age_ms <= _MAX_AGE_MS:
        raise ValueError("Observation age is outside its supported bounds")
    tick = elapsed_ns() if started_ns is None else started_ns
    value[_MARK] = {"clock_id": _CLOCK_ID, "started_ns": tick - age_ms * 1_000_000, "basis": basis}
    return value


def host_snapshot_age(clock: dict, host_root) -> int:
    """Check a project's persisted sampler against the same host's boot clock."""
    if (not isinstance(clock, dict) or set(clock) != {"schema", "boot_id", "uptime_ms"}
            or type(clock.get("schema")) is not int or clock["schema"] != 1
            or not isinstance(clock.get("boot_id"), str)
            or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", clock["boot_id"])
            or type(clock.get("uptime_ms")) is not int or not 0 <= clock["uptime_ms"] <= 10**15):
        raise ValueError("Host snapshot boot clock is invalid")
    def read(relative):
        path = Path(host_root) / relative
        if path.is_symlink():
            raise ValueError("Host clock symlinks are not accepted")
        with path.open("rb") as source:
            raw = source.read(513)
        if len(raw) > 512:
            raise ValueError("Host boot clock exceeds its read limit")
        return raw.decode("ascii").strip()
    boot = read("proc/sys/kernel/random/boot_id")
    fields = read("proc/uptime").split()
    uptime = fields[0] if fields else ""
    if (boot != clock["boot_id"] or read("proc/sys/kernel/random/boot_id") != boot
            or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,3})?", uptime)):
        raise ValueError("Host snapshot belongs to a different or unverifiable boot")
    current = int(float(uptime) * 1000)
    if current < clock["uptime_ms"]:
        raise ValueError("Host snapshot uptime is in the future")
    # /proc/uptime commonly exposes centiseconds; account for both rounded reads.
    age = current - clock["uptime_ms"] + 20
    if age > _MAX_AGE_MS:
        raise ValueError("Host snapshot age exceeds its supported bounds")
    return age


def _component(value: dict, path: tuple[str, ...]):
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, dict) else None


def _elapsed_authority(name: str, value: dict, basis: str) -> bool:
    if name in {"npu_telemetry", "npu_last_good"}:
        if value.get("timestamp_source") == "exporter":
            return False
        source = value.get("source")
        if not isinstance(source, str):
            return False
        if basis == "host-boot-uptime":
            return source == "host-npu-smi"
        failed = value.get("status") in {"error", "unavailable"} and not value.get("devices")
        return basis == "agent-elapsed" and (
            (source == "ascend-npu-exporter" and value.get("timestamp_source") == "scrape")
            or (failed and source in {"ascend-npu-exporter", "host-npu-smi"}))
    return basis == "agent-elapsed" and (name != "host_metrics"
        or value.get("source") == "host-proc"
        or value.get("source") == "node-exporter" and value.get("timestamp_source") == "scrape")


def _parsed(value) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Observation source time is invalid")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Observation source time must have a timezone")
    return result.astimezone(timezone.utc)


def _ttl_ms(value: dict, *, ledger=False) -> int | None:
    if value.get("collected_at") in (None, ""):
        if value.get("expires_at") not in (None, ""):
            raise ValueError("Observation source interval is incomplete")
        return None
    observed = _parsed(value["collected_at"])
    if ledger and value.get("expires_at") in (None, ""):
        return None
    interval = (_parsed(value.get("expires_at")) - observed).total_seconds() * 1000
    # Do not expand a producer's existing TTL, including sub-millisecond bounds.
    if not math.isfinite(interval) or not 0 < interval <= 600_000 or not interval.is_integer():
        raise ValueError("Observation source TTL is unsupported")
    return int(interval)


def rebase_times(value: dict, *, age_ms: int, captured: datetime, basis: str, ledger=False) -> None:
    ttl = _ttl_ms(value, ledger=ledger)
    if value.get("collected_at") in (None, ""):
        return
    if "source_observation" not in value:
        value["source_observation"] = {"basis": basis, "collected_at": value["collected_at"],
                                       **({"expires_at": value["expires_at"]} if ttl is not None else {})}
    observed = captured - timedelta(milliseconds=age_ms)
    value["collected_at"] = observed.isoformat()
    if ttl is not None:
        value["expires_at"] = (observed + timedelta(milliseconds=ttl)).isoformat()


def inventory_response(payload: dict, nonce: str | None) -> dict:
    """Generate a new response envelope without changing any cached sample age."""
    result = copy.deepcopy(payload)
    if nonce is not None and (not isinstance(nonce, str) or not _NONCE.fullmatch(nonce)):
        raise ValueError("Inventory request nonce is invalid")
    current, proofs = elapsed_ns(), {}
    for name, path in _PATHS.items():
        value = _component(result, path)
        if value is None:
            continue
        marker = value.pop(_MARK, None)
        if value.get("collected_at") in (None, ""):
            continue
        ttl = _ttl_ms(value, ledger=name == "resource_ledger")
        if marker is None:
            # Explicit exporter timestamps and legacy snapshots have their own
            # UTC authority. They must never inherit the Agent's clock offset.
            proofs[name] = {"basis": "source-utc"}
            continue
        if (not isinstance(marker, dict) or marker.get("clock_id") != _CLOCK_ID
                or type(marker.get("started_ns")) is not int or marker["started_ns"] > current
                or not _elapsed_authority(name, value, marker.get("basis"))):
            raise ValueError("Observation elapsed clock is no longer verifiable")
        if ttl is None:
            raise ValueError("Elapsed observation proof requires an explicit TTL")
        age = (current - marker["started_ns"] + 999_999) // 1_000_000
        if age > _MAX_AGE_MS:
            raise ValueError("Observation elapsed age exceeds its supported bounds")
        proofs[name] = {"basis": marker["basis"], "age_ms": age, "ttl_ms": ttl}
    if nonce is not None:
        result["inventory_freshness"] = {"schema": 1, "nonce": nonce, "components": proofs}
    return result


def receive_inventory(payload: dict, *, nonce: str, received_at: datetime, round_trip_ns: int) -> dict:
    """Consume optional age proofs once at the network boundary, before projection."""
    if not isinstance(payload, dict):
        raise ValueError("Agent inventory must be an object")
    wrapper = payload.get("inventory", payload)
    if not isinstance(wrapper, dict):
        raise ValueError("Agent inventory wrapper is invalid")
    envelope = wrapper.get("inventory_freshness")
    if envelope is None:
        return payload  # Legacy Agent: retain the existing strict UTC behavior.
    if (not isinstance(envelope, dict) or set(envelope) != {"schema", "nonce", "components"}
            or type(envelope.get("schema")) is not int or envelope["schema"] != 1
            or not isinstance(nonce, str) or not _NONCE.fullmatch(nonce) or envelope.get("nonce") != nonce
            or type(round_trip_ns) is not int or not 0 <= round_trip_ns <= _MAX_AGE_MS * 1_000_000):
        raise ValueError("Inventory age envelope is untrusted")
    proofs = envelope.get("components")
    if not isinstance(proofs, dict) or set(proofs) - _PATHS.keys():
        raise ValueError("Inventory age components are invalid")
    result = copy.deepcopy(wrapper)
    result.pop("inventory_freshness", None)
    transport = (round_trip_ns + 999_999) // 1_000_000
    expected = set()
    for name, path in _PATHS.items():
        value = _component(result, path)
        if value is None or value.get("collected_at") in (None, ""):
            continue
        expected.add(name)
        proof = proofs.get(name)
        if not isinstance(proof, dict):
            raise ValueError("Inventory age proof is missing for a timed component")
        ttl = _ttl_ms(value, ledger=name == "resource_ledger")
        if proof == {"basis": "source-utc"}:
            if (name in {"npu_telemetry", "npu_last_good"}
                    and value.get("source") not in ("ascend-npu-exporter", "host-npu-smi")):
                raise ValueError("NPU observation source is unsupported")
            if name == "host_metrics" and value.get("source") not in {"host-proc", "node-exporter"}:
                raise ValueError("Host observation source is unsupported")
            continue
        age = proof.get("age_ms")
        if (not _elapsed_authority(name, value, proof.get("basis"))
                or ttl is None
                or type(age) is not int or not 0 <= age <= _MAX_AGE_MS
                or set(proof) != {"basis", "age_ms", "ttl_ms"}
                or type(proof.get("ttl_ms")) is not int or proof["ttl_ms"] != ttl):
            raise ValueError("Inventory age proof is invalid")
        rebase_times(value, age_ms=age + transport, captured=received_at, basis=proof["basis"],
                     ledger=name == "resource_ledger")
    if set(proofs) != expected:
        raise ValueError("Inventory age proof coverage is inconsistent")
    return result
