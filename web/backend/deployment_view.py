"""Immutable deployment bindings used by observations, never by draft editing."""
from __future__ import annotations

import copy
import hashlib
import json

from .version import BASELINE_PROFILE_ID


def configuration_digest(service: dict) -> str:
    value = {key: service[key] for key in ("user_config", "env", "placements", "runtime")}
    if service.get("extra_manifests"):
        value["extra_manifests"] = service["extra_manifests"]
    if service.get("motor_profile") and service["motor_profile"] != BASELINE_PROFILE_ID:
        value["motor_profile"] = service["motor_profile"]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def runtime_service_view(service: dict) -> dict:
    """Project the deployed snapshot without changing a user's current draft.

    Legacy records can use their current configuration only when the existing
    deployed digest proves that it is still the executed revision. An uncertain
    draft must never become permission to query another service's reused port.
    """
    from .fleet import FleetError
    snapshot = service.get("deployed_user_config")
    if snapshot is not None:
        environment = service.get("deployed_env")
        placements = service.get("deployed_placements")
        if not isinstance(snapshot, dict) or not isinstance(environment, dict) or not isinstance(placements, list):
            raise FleetError("The immutable deployment snapshot is incomplete; native endpoint access was refused")
        view = {**service, "user_config": copy.deepcopy(snapshot), "env": copy.deepcopy(environment),
                "placements": copy.deepcopy(placements),
                "runtime": service.get("deployed_runtime") or service.get("runtime")}
        if service.get("deployed_motor_profile"):
            view["motor_profile"] = service["deployed_motor_profile"]
        return view
    if service.get("has_unapplied_changes") or service.get("needs_rebuild"):
        raise FleetError("The saved draft has not been applied and no deployment snapshot exists; native access was refused")
    expected = service.get("deployed_digest")
    if expected:
        try:
            matches = configuration_digest(service) == expected
        except (KeyError, ValueError, TypeError):
            matches = False
        if not matches:
            raise FleetError("The deployed configuration binding cannot be verified; native endpoint access was refused")
    elif service.get("id") and service.get("resources_may_exist"):
        raise FleetError("No immutable deployment binding exists for these resources; reconcile before native access")
    return copy.deepcopy(service)
