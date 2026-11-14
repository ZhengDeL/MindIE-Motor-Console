"""Independent selection receipt; critical changes fail before storage opens."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
from uuid import uuid4

from ..plugin_host.contracts import PluginConfigurationError

CRITICAL = frozenset({"storage.store", "runtime.docker", "runtime.kubernetes", "motor.native", "resources.ledger"})
WORKFLOW_CRITICAL = frozenset({"services.workflow", "tasks.coordinator"})
_LOCK = threading.RLock()


class ProviderGenerationMigrationRequired(PluginConfigurationError):
    """An old receipt cannot prove the newly tracked workflow identities.

    Reference names alone are not compatibility evidence: the old format did
    not record these contracts, and deliberately omitted reference versions.
    Keep the original receipt intact and reconcile on its recorded release.
    """
    code = "provider_receipt_migration_required"

    def __init__(self, missing_contracts):
        self.missing_contracts = tuple(sorted(missing_contracts))
        super().__init__(self.code + ": recorded receipt predates critical service/task workflow identities ("
            + ", ".join(self.missing_contracts) + "); reference names do not prove compatible code or versions. "
            "Keep this receipt and restore the exact recorded release/assembly to reconcile pending operations, "
            "owned resources and Agent effects. Upgrade only after that release checkpoints a confirmed idle state, "
            "or use a separately reviewed migration with complete original identity evidence. "
            "Do not delete/edit the receipt or clear ownership flags to bypass this guard.")


def _selected(describe):
    # A Console/UI release number is not an execution/storage compatibility
    # change. Reference critical products are identified by their own relevant
    # source digest and exact contract version, independent of bundle version.
    return {item["contract"]: {key: value for key, value in item.items()
                if key != "version" or not isinstance(item["provider"], str) or not item["provider"].startswith("reference.")}
            for item in describe["bindings"] if item["contract"] in CRITICAL or item.get("contract_definition", {}).get("critical") is True}


def guard(directory: Path, describe: dict):
    path = directory / "provider-generation.json"
    if not path.exists():
        # Upgrading an existing installation has no independent pending-operation
        # receipt. Do not switch its persistence/execution provider blindly.
        changed = any(item["provider"] != "reference." + item["contract"] for item in describe["bindings"] if item["contract"] in CRITICAL or item.get("contract_definition", {}).get("critical") is True)
        if changed and directory.exists() and any(directory.iterdir()):
            raise PluginConfigurationError("Existing state has no provider generation receipt; first start the recorded providers and reconcile operations")
        return
    try:
        from ..plugin_host.packages.files import read_regular
        previous = json.loads(read_regular(path, 1024 * 1024))
        if (not isinstance(previous, dict) or set(previous) != {"schemaVersion", "generation", "critical_bindings", "pending", "resources"}
                or previous["schemaVersion"] != 1 or not isinstance(previous["generation"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", previous["generation"])
                or not isinstance(previous["critical_bindings"], dict) or not CRITICAL <= set(previous["critical_bindings"])
                or any(not isinstance(previous[key], list) or any(not isinstance(value, str) for value in previous[key]) for key in ("pending", "resources"))):
            raise PluginConfigurationError("Provider generation receipt has an invalid schema")
        effects = _agent_effects(directory)
        selected = _selected(describe)
        if previous["critical_bindings"] != selected and (previous["pending"] or previous.get("resources") or effects):
            missing_workflows = WORKFLOW_CRITICAL & (set(selected) - set(previous["critical_bindings"]))
            if set(previous["critical_bindings"]) == CRITICAL and missing_workflows:
                raise ProviderGenerationMigrationRequired(missing_workflows)
            raise PluginConfigurationError("Cannot change critical provider implementations with pending operations or owned resources; reconcile using the recorded generation")
    except PluginConfigurationError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise PluginConfigurationError("Provider generation receipt is invalid; offline recovery is required") from exc


def checkpoint(directory: Path, describe: dict, store, *, extra_pending=()):
    """A stale receipt is conservative: interruption never clears ownership."""
    with _LOCK:
        pending = [job["id"] for job in store.list("jobs") if job.get("status") in {"queued", "running", "interrupted"} or job.get("outcome_unknown")]
        pending.extend(extra_pending)
        resources = [item["id"] for item in store.list("services") if item.get("resources_may_exist")]
        value = {"schemaVersion": 1, "generation": describe["generation"],
                 "critical_bindings": _selected(describe), "pending": sorted(pending), "resources": sorted(resources)}
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "provider-generation.json"
        temporary = path.with_name(path.name + "." + uuid4().hex + ".part")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                temporary.chmod(0o600)
                json.dump(value, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _agent_effects(directory):
    path = directory / "provider-agent-effects.json"
    if not path.exists(): return {}
    from ..plugin_host.packages.files import read_regular
    value = json.loads(read_regular(path, 1024 * 1024))
    if not isinstance(value, dict) or value.get("schemaVersion") != 1 or not isinstance(value.get("effects"), dict):
        raise PluginConfigurationError("Agent provider effect receipt has an invalid schema")
    return value["effects"]


def record_agent_operation(directory, describe, phase, runtime, action, payload, result=None):
    """Public operation tracker: unknown effects survive provider restarts.

    The middleware persists begin before execution, including alternative HTTP
    modules. A selected runtime/API can report an exact receipt through this
    callback. Only confirmed removal (or completed image receipt) releases the
    independent ownership marker; stopping does not remove owned resources.
    """
    if action in {"plan", "preflight", "inference"} or payload.get("dry_run") is True:
        return result
    key = payload.get("service_id") or payload.get("operation_id")
    if not isinstance(key, str) or not key or len(key) > 256:
        return result
    with _LOCK:
        effects = _agent_effects(directory)
        if phase == "begin":
            if len(effects) >= 4096 and key not in effects:
                raise PluginConfigurationError("Agent unresolved-effect receipt capacity reached; reconcile existing operations")
            effects[key] = {"generation": describe["generation"], "binding": next(
                item for item in describe["bindings"] if item["contract"] == "runtime." + runtime),
                "operation_id": payload.get("operation_id"), "service_id": payload.get("service_id"),
                "action": action, "state": "outcome_unknown", "resources_may_exist": True}
        elif phase == "result" and isinstance(result, dict):
            removed = action == "remove" and result.get("status") == "removed" and (
                result.get("resources_may_exist") is False or result.get("removal_converged") is True)
            image_complete = action.startswith("image") and result.get("operation_status") == "completed" and result.get("resources_may_exist") is False
            if removed or image_complete:
                effects.pop(key, None)
            elif key in effects:
                effects[key]["state"] = "completed" if result.get("operation_status") == "completed" else "outcome_unknown"
            result = dict(result)
            result.setdefault("provider_generation", describe["generation"])
            result.setdefault("provider_binding", next(item for item in describe["bindings"] if item["contract"] == "runtime." + runtime))
        else:
            raise PluginConfigurationError("Invalid Agent operation tracker phase or receipt")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "provider-agent-effects.json"
        temporary = path.with_name(path.name + "." + uuid4().hex + ".part")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                temporary.chmod(0o600)
                json.dump({"schemaVersion": 1, "effects": effects}, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return result
