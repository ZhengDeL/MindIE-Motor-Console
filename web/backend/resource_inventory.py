"""Public, bounded inventory snapshots and explicit freshness semantics."""
from __future__ import annotations

import copy
from datetime import datetime, timezone, timedelta
import math
import os
import re

MAX_INVENTORY_BYTES = 2 * 1024 * 1024
_MAX_INTEGER = 2**53 - 1


class InventoryError(ValueError):
    pass


def _time(value, *, optional=False):
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise InventoryError("Inventory observation time is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InventoryError("Inventory observation time is invalid") from exc
    if parsed.tzinfo is None:
        raise InventoryError("Inventory observation time must include its timezone")
    return parsed.astimezone(timezone.utc)


def observation_times(ttl_seconds: int | None = None) -> dict:
    if ttl_seconds is None:
        raw = os.getenv("MOTOR_RESOURCE_TTL_SECONDS", "45")
        if not raw.isascii() or not raw.isdecimal() or not 5 <= int(raw) <= 300:
            raise InventoryError("MOTOR_RESOURCE_TTL_SECONDS must be between 5 and 300")
        ttl_seconds = int(raw)
    if type(ttl_seconds) is not int or not 5 <= ttl_seconds <= 300:
        raise InventoryError("Inventory TTL is invalid")
    from .resource_freshness import elapsed_ns, mark_sample
    started = elapsed_ns()
    captured = datetime.now(timezone.utc)
    return mark_sample({"collected_at": captured.isoformat(),
                        "expires_at": (captured + timedelta(seconds=ttl_seconds)).isoformat()}, started_ns=started)


def _text(value, maximum=256, *, empty=True):
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value)
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise InventoryError("Inventory contains an invalid public string")
    return value


def _integer(value, maximum=_MAX_INTEGER, *, nullable=True):
    if value is None and nullable:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        raise InventoryError("Inventory contains an invalid integer")
    return value


def _number(value, low=0, high=100):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise InventoryError("Inventory contains an invalid observation value")
    return value


def _enum(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        raise InventoryError("Inventory contains an unsupported observation state")
    return value


def _list(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise InventoryError("Inventory collection exceeds its bounds")
    return value


def _errors(values) -> list[dict]:
    result = []
    for item in _list(values, 16):
        if not isinstance(item, dict):
            raise InventoryError("Inventory error entry is invalid")
        result.append({"source": _text(item.get("source", "unknown"), 64),
                       "code": _text(item.get("code", "unknown"), 64),
                       "message": _text(item.get("message", ""), 512)})
    return result


def _source_observation(value: dict) -> dict:
    source = value.get("source_observation")
    if source is None:
        return {}
    if not isinstance(source, dict) or set(source) - {"basis", "collected_at", "expires_at"}:
        raise InventoryError("Observation source provenance is invalid")
    result = {"basis": _enum(source.get("basis"), {"agent-elapsed", "host-boot-uptime"}),
              "collected_at": _time(source.get("collected_at")).isoformat()}
    if "expires_at" in source:
        expires = _time(source["expires_at"])
        observed = _time(source["collected_at"])
        if not 0 < (expires - observed).total_seconds() <= 600:
            raise InventoryError("Observation source provenance interval is invalid")
        result["expires_at"] = expires.isoformat()
    return {"source_observation": result}


def _times(value: dict, captured: datetime, *, optional=False) -> dict:
    observed = _time(value.get("collected_at"), optional=optional)
    expires = _time(value.get("expires_at"), optional=optional)
    if (observed is None) != (expires is None):
        raise InventoryError("Inventory freshness times must be supplied together")
    if observed is not None:
        if observed > captured + timedelta(seconds=15) or expires < observed or expires - observed > timedelta(seconds=600):
            raise InventoryError("Inventory freshness interval is invalid")
    return {"collected_at": observed.isoformat() if observed else None,
            "expires_at": expires.isoformat() if expires else None, **_source_observation(value)}


def _telemetry(value: dict, captured: datetime, *, historical=False) -> dict:
    if not isinstance(value, dict):
        raise InventoryError("NPU telemetry is invalid")
    result = {"status": _enum(value.get("status"), {"ready", "partial", "unavailable", "error"}),
              "source": _text(value.get("source", ""), 64),
              **_times(value, captured, optional=value.get("status") in {"unavailable", "error"}),
              "errors": _errors(value.get("errors", [])), "devices": []}
    for field in ("timestamp_source", "memory_unit_source"):
        if field in value:
            result[field] = _text(value[field], 64)
    seen, physical_ids, logical_ids, chip_pairs = set(), set(), set(), set()
    for device in _list(value.get("devices", []), 256):
        if not isinstance(device, dict):
            raise InventoryError("NPU telemetry device is invalid")
        identity = _text(device.get("device_id"), 64, empty=False)
        if identity in seen:
            raise InventoryError("NPU telemetry device identity is duplicated")
        seen.add(identity)
        output = {"device_id": identity,
                  "device_index": _integer(device.get("device_index"), 4095),
                  "logical_device_id": _integer(device.get("logical_device_id"), 4095),
                  "mapping_status": _enum(device.get("mapping_status", "unknown"), {"unknown", "configured", "verified"}),
                  "health": _enum(device.get("health", "unknown"), {"healthy", "unhealthy", "unknown"}),
                  "utilization_percent": _number(device.get("utilization_percent")),
                  "memory_total_bytes": _integer(device.get("memory_total_bytes")),
                  "memory_used_bytes": _integer(device.get("memory_used_bytes")),
                  "memory_usage_percent": _number(device.get("memory_usage_percent")),
                  "temperature_celsius": _number(device.get("temperature_celsius"), -40, 200),
                  "power_watts": _number(device.get("power_watts"), 0, 10000),
                  "processes_status": _enum(device.get("processes_status", "unavailable"), {"ready", "unavailable"}),
                  "process_pid_scope": _enum(device.get("process_pid_scope", "unknown"), {"host", "unknown"}),
                  "processes": []}
        for field in ("physical_device_id", "npu_id", "chip_id"):
            if field in device:
                output[field] = _integer(device[field], 4095)
        if (output.get("physical_device_id") is not None
                and output["device_index"] != output["physical_device_id"]):
            raise InventoryError("NPU physical device mappings disagree")
        if output["mapping_status"] == "verified":
            if any(output.get(field) is None for field in ("physical_device_id", "logical_device_id", "npu_id", "chip_id")):
                raise InventoryError("Verified NPU mapping is incomplete")
            physical, logical = output["physical_device_id"], output["logical_device_id"]
            pair = (output["npu_id"], output["chip_id"])
            if physical in physical_ids or logical in logical_ids or pair in chip_pairs:
                raise InventoryError("Verified NPU mapping is ambiguous")
            physical_ids.add(physical)
            logical_ids.add(logical)
            chip_pairs.add(pair)
        for name in ("pci_bus_id", "uuid", "model_name"):
            output[name] = _text(device.get(name, ""), 128)
        owners = set()
        for process in _list(device.get("processes", []), 128):
            if not isinstance(process, dict):
                raise InventoryError("NPU process observation is invalid")
            pid = _integer(process.get("pid"), 2**31 - 1, nullable=False)
            if pid == 0:
                raise InventoryError("NPU process ID is invalid")
            entry = {"pid": pid, "memory_used_bytes": _integer(process.get("memory_used_bytes"))}
            if process.get("container_id"):
                entry["container_id"] = _text(process["container_id"], 128)
            owner = (pid, entry.get("container_id", ""))
            if owner in owners:
                raise InventoryError("NPU process observation is duplicated")
            owners.add(owner)
            output["processes"].append(entry)
        total, used = output["memory_total_bytes"], output["memory_used_bytes"]
        if total is not None and used is not None and used > total:
            raise InventoryError("NPU memory observation exceeds its total")
        result["devices"].append(output)
    if not historical and isinstance(value.get("last_good"), dict):
        result["last_good"] = _telemetry(value["last_good"], captured, historical=True)
    return result


def _ledger(value: dict, captured: datetime) -> dict:
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1 or type(value.get("complete")) is not bool):
        raise InventoryError("Resource allocation ledger is invalid")
    observed = _time(value.get("collected_at"))
    if observed > captured + timedelta(seconds=15):
        raise InventoryError("Resource allocation ledger time is in the future")
    result = {"schema_version": 1, "scope": _enum(value.get("scope"), {"single-agent-docker-daemon"}),
              "daemon_id": _text(value.get("daemon_id", ""), 128), "collected_at": observed.isoformat(),
              "complete": value["complete"], "items": [], **_source_observation(value)}
    if "expires_at" in value:
        result.update(_times(value, captured))
    for item in _list(value.get("items", []), 256):
        if not isinstance(item, dict):
            raise InventoryError("Resource allocation entry is invalid")
        entry = {field: _text(item.get(field, ""), 128) for field in ("allocation_id", "service_id", "node_id", "operation_id")}
        entry["state"] = _enum(item.get("state"), {"pending", "active", "uncertain", "released"})
        entry["devices"] = [_integer(v, 4095, nullable=False) for v in _list(item.get("devices", []), 128)]
        entry["ports"] = [_integer(v, 65535, nullable=False) for v in _list(item.get("ports", []), 256)]
        for field in ("container_ids", "container_names"):
            entry[field] = [_text(v, 256) for v in _list(item.get(field, []), 32)]
        entry["updated_at"] = _time(item.get("updated_at")).isoformat()
        observed_at = _time(item.get("observed_at"), optional=True)
        entry["observed_at"] = observed_at.isoformat() if observed_at else None
        result["items"].append(entry)
    return result


def project_inventory(payload, *, received_at: datetime | None = None) -> dict:
    """Discard arbitrary Agent output and reject malformed resource values before Store."""
    if not isinstance(payload, dict):
        raise InventoryError("Agent inventory must be a JSON object")
    if "inventory" in payload:
        payload = payload["inventory"]
        if not isinstance(payload, dict):
            raise InventoryError("Agent inventory wrapper is invalid")
    captured = received_at or datetime.now(timezone.utc)
    schema = payload.get("inventory_schema", 1)
    if type(schema) is not int or schema not in {1, 2}:
        raise InventoryError("Agent inventory schema is unsupported")
    result = {"inventory_schema": schema, "last_received_at": captured.isoformat(),
              "inventory_status": _enum(payload.get("inventory_status", "partial"), {"ready", "partial", "error"}),
              **_times(payload, captured, optional=schema == 1),
              "collection_errors": _errors(payload.get("collection_errors", []))}
    for field in ("architecture", "os", "docker_version", "kubernetes_version", "nic_name", "resource_scope"):
        if field in payload:
            result[field] = _text(payload[field], 512 if field == "resource_scope" else 128)
    for field in ("docker_available", "kubernetes_available"):
        if field in payload:
            if type(payload[field]) is not bool:
                raise InventoryError("Agent inventory runtime flag is invalid")
            result[field] = payload[field]
    for field in ("npu_total", "npu_used"):
        result[field] = _integer(payload.get(field), 4096)
    for field in ("cpu_percent", "memory_percent"):
        result[field] = _number(payload.get(field))
    for field in ("devices", "devices_in_use"):
        values = [_integer(v, 4095, nullable=False) for v in _list(payload.get(field, []), 256)]
        if len(values) != len(set(values)):
            raise InventoryError("Agent device inventory contains duplicate IDs")
        result[field] = sorted(values)
    if result["npu_total"] is not None and "devices" in payload and result["npu_total"] != len(result["devices"]):
        raise InventoryError("Agent device count and identities disagree")
    if (result["npu_total"] is not None and result["npu_used"] is not None
            and result["npu_used"] > result["npu_total"]):
        raise InventoryError("Agent declared device allocation exceeds its inventory")
    if schema == 2 and result["npu_used"] is not None:
        if result["npu_used"] != len(result["devices_in_use"]) or not set(result["devices_in_use"]).issubset(result["devices"]):
            raise InventoryError("Agent declared device count and allocation identities disagree")
    for field in ("daemon_id", "host_identity"):
        value = _text(payload.get(field, ""), 128)
        if value and not re.fullmatch(r"[A-Za-z0-9:._-]+", value):
            raise InventoryError("Agent host identity is invalid")
        result[field] = value
    if "npu_telemetry" in payload:
        result["npu_telemetry"] = _telemetry(payload["npu_telemetry"], captured)
    if "host_metrics" in payload:
        value = payload["host_metrics"]
        if not isinstance(value, dict):
            raise InventoryError("Host metrics are invalid")
        result["host_metrics"] = {"status": _enum(value.get("status"), {"ready", "partial", "error"}),
                                  "source": _text(value.get("source", "host-proc"), 64),
                                  **_times(value, captured, optional=True),
                                  "errors": _errors(value.get("errors", [])),
                                  "memory_total_bytes": _integer(value.get("memory_total_bytes")),
                                  "memory_available_bytes": _integer(value.get("memory_available_bytes"))}
        host = result["host_metrics"]
        total, available = host["memory_total_bytes"], host["memory_available_bytes"]
        if total is not None and available is not None and (total == 0 or available > total):
            raise InventoryError("Host memory observation exceeds its total")
        if host["source"] == "node-exporter":
            host["state"] = _enum(value.get("state"), {"ready", "warming", "counter_reset", "incomplete_cpu", "incomplete_metrics",
                                  "stale", "collecting", "collection_failed", "invalid_configuration"})
            if "timestamp_source" in value:
                host["timestamp_source"] = _enum(value["timestamp_source"], {"scrape", "exporter"})
            host["cpu_count"] = _integer(value.get("cpu_count"), 2048)
            host["boot_time_seconds"] = _number(value.get("boot_time_seconds"), 0, _MAX_INTEGER)
            for field, keys in (("os_info", {"id", "name", "pretty_name", "version_id"}),
                                ("uname_info", {"sysname", "release", "machine"})):
                info = value.get(field)
                if info is not None and (not isinstance(info, dict) or set(info) != keys):
                    raise InventoryError("Node Exporter host identity is invalid")
                host[field] = {key: _text(info[key], 128) for key in keys} if info is not None else None
            info = host.get("os_info") or {}
            result["os"] = info.get("pretty_name") or " ".join(v for v in (info.get("name"), info.get("version_id")) if v)
            if len(result["os"]) > 128:
                raise InventoryError("Node Exporter OS name exceeds its bounds")
    if "resource_ledger" in payload:
        result["resource_ledger"] = _ledger(payload["resource_ledger"], captured)
    return result


def inventory_snapshot(node: dict) -> dict:
    fields = {"inventory_schema", "inventory_status", "collected_at", "expires_at", "source_observation", "collection_errors",
              "daemon_id", "host_identity", "npu_total", "npu_used", "devices", "devices_in_use",
              "cpu_percent", "memory_percent", "host_metrics", "npu_telemetry", "resource_ledger"}
    try:
        safe = project_inventory(node)
    except InventoryError:
        return {}
    return copy.deepcopy({field: safe[field] for field in fields if field in safe})


def _current_telemetry(value: dict, captured: datetime, *, historical_only=False) -> dict:
    expires = _time(value.get("expires_at"), optional=True)
    if not historical_only and value.get("status") in {"ready", "partial"} and expires and expires > captured:
        return value
    history = value.get("last_good") or (value if value.get("devices") else None)
    result = {"status": "unavailable", "source": value.get("source", ""), "devices": [],
              "collected_at": value.get("collected_at"), "expires_at": value.get("expires_at"),
              "errors": value.get("errors", []) or [{"source": "inventory", "code": "stale",
                  "message": "NPU 观测已过期或不可验证，历史值不计入当前资源"}]}
    if history:
        result["last_good"] = {key: copy.deepcopy(item) for key, item in history.items() if key != "last_good"}
    return result


def inventory_view(node: dict, *, at: datetime | None = None) -> dict:
    """Historical values remain available separately and never masquerade as live totals."""
    captured = at or datetime.now(timezone.utc)
    resource_fields = {"inventory_schema", "inventory_status", "collected_at", "expires_at", "source_observation", "collection_errors",
                       "daemon_id", "host_identity", "npu_total", "npu_used", "devices", "devices_in_use",
                       "cpu_percent", "memory_percent", "host_metrics", "npu_telemetry", "resource_ledger",
                       "last_good_inventory", "last_received_at"}
    result = {key: value for key, value in node.items() if key not in resource_fields}
    if isinstance(node.get("host_metrics"), dict) and node["host_metrics"].get("source") == "node-exporter":
        # A malformed saved observation must not leave its old OS string in
        # metadata after strict projection has discarded its provenance.
        result["os"] = ""
    try:
        safe = project_inventory(node, received_at=captured)
        result.update(safe)
        observed = _time(safe.get("collected_at"), optional=True)
        expires = _time(safe.get("expires_at"), optional=True)
        valid_time = bool(observed and expires and observed <= captured + timedelta(seconds=15) and expires > captured)
        freshness = "demo" if node.get("demo") is True else "fresh" if (safe.get("inventory_schema") == 2 and node.get("status") == "online"
                                and safe.get("inventory_status") in {"ready", "partial"} and valid_time
                                and not node.get("identity_conflict")) else "stale" if observed else "unknown"
    except InventoryError:
        observed, freshness, safe = None, "unknown", {}
        result.update(inventory_schema=1, inventory_status="error", collected_at=None, expires_at=None,
                      collection_errors=[{"source": "inventory", "code": "invalid_saved_observation",
                                          "message": "已保存的资源观测无效，需重新采集"}])
    if node.get("last_received_at"):
        try:
            result["last_received_at"] = _time(node["last_received_at"]).isoformat()
        except InventoryError:
            result.pop("last_received_at", None)
    else:
        result.pop("last_received_at", None)
    result["resource_freshness"] = freshness
    result["resource_age_seconds"] = max(0, round((captured - observed).total_seconds())) if observed else None
    history = node.get("last_good_inventory")
    if isinstance(history, dict):
        history = inventory_snapshot(history)
    else:
        history = None
    if freshness not in {"fresh", "demo"}:
        result["last_good_inventory"] = history or inventory_snapshot(safe)
        for field in ("npu_total", "npu_used", "cpu_percent", "memory_percent"):
            result[field] = None
        result["devices"] = []
        result["devices_in_use"] = []
        result.pop("host_metrics", None)
        result.pop("resource_ledger", None)
        if (safe.get("host_metrics") or {}).get("source") == "node-exporter":
            result["os"] = ""
    elif history:
        result["last_good_inventory"] = history
    if safe.get("npu_telemetry"):
        result["npu_telemetry"] = _current_telemetry(safe["npu_telemetry"], captured,
                                                   historical_only=freshness not in {"fresh", "demo"})
    if freshness == "fresh" and isinstance(safe.get("host_metrics"), dict):
        host_expiry = _time(safe["host_metrics"].get("expires_at"), optional=True)
        if not host_expiry or host_expiry <= captured or safe["host_metrics"].get("status") == "error":
            result["cpu_percent"] = None
            result["memory_percent"] = None
            if safe["host_metrics"].get("source") == "node-exporter":
                result["os"] = ""
                result["host_metrics"] = {**safe["host_metrics"], "status": "error",
                    "state": "stale" if host_expiry and host_expiry <= captured else safe["host_metrics"]["state"],
                    "memory_total_bytes": None, "memory_available_bytes": None, "os_info": None, "uname_info": None}
    if freshness == "fresh" and isinstance(safe.get("resource_ledger"), dict):
        ledger_expiry = _time(safe["resource_ledger"].get("expires_at"), optional=True)
        if ledger_expiry is not None and ledger_expiry <= captured:
            result.pop("resource_ledger", None)
    return result
