"""Operator-pinned Motor source profiles behind a stable Agent protocol.

A custom source manifest is accepted only when its complete bytes are pinned by
an out-of-band SHA256 setting. It cannot select arbitrary driver code. Installing
a profile is a deployment operation, never a user-config field or auto-upgrade.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

from .version import ADAPTER_DRIVER, AGENT_PROTOCOL_VERSION, BASELINE_MOTOR_COMMIT, BASELINE_PROFILE_ID, CONSOLE_VERSION, PRODUCT_ID

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class CompatibilityError(ValueError):
    pass


def source_profile() -> dict:
    configured = os.environ.get("MOTOR_SOURCE_MANIFEST", "")
    path = Path(configured) if configured else Path(__file__).with_name("motor_source_manifest.json")
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Motor profile exceeds the supported size")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        manifest = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise CompatibilityError("Motor source profile is unavailable or invalid") from exc
    if not isinstance(manifest, dict):
        raise CompatibilityError("Motor source profile must be an object")
    if configured:
        expected = os.environ.get("MOTOR_SOURCE_MANIFEST_SHA256", "")
        if not _SHA.fullmatch(expected) or expected != digest:
            raise CompatibilityError("Custom Motor profile requires its exact operator-pinned SHA256")
        if (type(manifest.get("profile_schema")) is not int or manifest["profile_schema"] != 1
                or type(manifest.get("agent_protocol")) is not int or manifest["agent_protocol"] != AGENT_PROTOCOL_VERSION):
            raise CompatibilityError("Unsupported Motor profile schema or Agent protocol")
        if manifest.get("driver") != ADAPTER_DRIVER or manifest.get("contract_status") != "contract-tested":
            raise CompatibilityError("Motor profile needs a reviewed and tested supported driver contract")
        profile_id = manifest.get("profile_id", "")
    else:
        profile_id = BASELINE_PROFILE_ID
        if manifest.get("commit") != BASELINE_MOTOR_COMMIT:
            raise CompatibilityError("Bundled Motor source manifest does not match its baseline")
    commit, files = manifest.get("commit"), manifest.get("files")
    if not isinstance(profile_id, str) or not _ID.fullmatch(profile_id) or not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise CompatibilityError("Invalid Motor profile identity or native commit")
    if configured and not profile_id.endswith("-" + commit):
        raise CompatibilityError("Custom Motor profile identity must end with its full native commit; existing service bindings must remain immutable")
    if not isinstance(files, dict) or not files:
        raise CompatibilityError("Motor source profile has no integrity inventory")
    required = {"examples/deployer/docker_deploy.py", "examples/deployer/deploy.py"}
    if not required.issubset(files):
        raise CompatibilityError("Motor source profile omits native entry points")
    for name, sha in files.items():
        if not isinstance(name, str) or not name or any(ord(c) < 32 for c in name) or ":" in name:
            raise CompatibilityError("Invalid source profile path or digest")
        fragment = PurePosixPath(name)
        if fragment.is_absolute() or ".." in fragment.parts or "\\" in name or not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise CompatibilityError("Invalid source profile path or digest")
    return {"profile_id": profile_id, "commit": commit, "driver": ADAPTER_DRIVER,
            "agent_protocol": AGENT_PROTOCOL_VERSION, "manifest_path": str(path.resolve()),
            "manifest_sha256": digest, "files": files, "bundled": not bool(configured)}


def public_profile() -> dict:
    return {key: value for key, value in source_profile().items() if key not in {"files", "manifest_path"}}


def check_binding(profile_id: str | None, protocol: str | int | None = None) -> dict:
    profile = public_profile()
    if protocol is not None and str(protocol) != str(AGENT_PROTOCOL_VERSION):
        raise CompatibilityError("Agent protocol differs; update the control plane/Agent using a compatible release")
    if profile_id is None or profile_id == "":
        if not profile["bundled"]:
            raise CompatibilityError("An explicit Motor profile binding is required for a custom native version")
    elif profile_id != profile["profile_id"]:
        raise CompatibilityError("The service is bound to a different Motor profile; existing deployments are not migrated automatically")
    return profile


def agent_metadata() -> dict:
    from .execution_policy import execution_metadata
    from .native_contracts import native_contract_status
    from .agent_server import transport_security
    mode = execution_metadata()
    profile = public_profile()
    return {"product_id": PRODUCT_ID, "agent_version": CONSOLE_VERSION, "agent_protocol": AGENT_PROTOCOL_VERSION,
            **mode,
            "transport_security": transport_security(),
            "supported_control_protocols": [AGENT_PROTOCOL_VERSION], "motor_profile": profile,
            "native_contract": native_contract_status(profile["commit"]),
            "supported_runtimes": ["docker", "kubernetes"], "operation_protocol": 1,
            "execution_capabilities": {"operation_receipts": True, "resource_ledger": True,
                                       "resource_admission": True, "mutations": not mode["read_only"],
                                       "image_operations": True}}
