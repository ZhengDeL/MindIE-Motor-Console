"""Offline MCP credential and backend-assembly preparation; never prints tokens.

Mount the private directory so registry replacement is visible on each request.
Issue uses distinct gateway and backend tokens. Revocation commits the backend
first, so failure updating the gateway cannot restore backend access.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import sys

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
from backend.agent_credentials import read_private_file
from backend.plugin_host.packages.files import atomic_write, ensure_directory, parents, store_lock, write_new
from backend.provider_host import ProviderHost
from backend.reference_modules.mcp_access import read_registry, validate_registry
from backend.reference_modules.mcp_operations import CONTRACT, VERSION, http_module


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def _directory(directory):
    directory = Path(os.path.abspath(directory))
    parents(directory)
    existed = directory.exists()
    ensure_directory(directory)
    if os.name == "posix":
        info = directory.stat()
        if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o077:
            raise ValueError("Private MCP directory must be owned by current UID/root and have mode 0700")
    elif os.name == "nt":
        # chmod on Windows does not protect secrets. A newly created dedicated
        # directory receives an explicit DACL; existing DACLs are only checked.
        script = r"""
$ErrorActionPreference = 'Stop'
$p = $env:MOTOR_MCP_PRIVATE_DIRECTORY
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$allowed = @($sid.Value, 'S-1-5-18', 'S-1-5-32-544')
if ($env:MOTOR_MCP_PRIVATE_NEW -eq 'true') {
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
    [System.IO.Directory]::SetAccessControl($p, $acl)
}
$acl = [System.IO.Directory]::GetAccessControl($p)
foreach ($rule in $acl.Access) {
    if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -notin $allowed) {
        throw 'Private MCP directory grants another account access'
    }
}
"""
        env = dict(os.environ, MOTOR_MCP_PRIVATE_DIRECTORY=str(directory), MOTOR_MCP_PRIVATE_NEW=str(not existed).lower())
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, capture_output=True, text=True)
        if result.returncode:
            raise ValueError("Private MCP directory Windows DACL could not be secured or verified")
    return directory


def _gateway(path):
    # Validate the same identity/scope policy without exposing backend token paths
    # to the backend registry schema.
    from backend.reference_modules.mcp_access import _pairs
    raw = read_private_file(path, maximum=1024 * 1024)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    if not isinstance(document, dict) or not isinstance(document.get("credentials"), list):
        raise ValueError("Invalid gateway registry")
    stripped = copy.deepcopy(document)
    for entry in stripped["credentials"]:
        filename = entry.pop("backend_token_file", None)
        if not isinstance(filename, str) or not filename or any(ord(c) < 32 for c in filename):
            raise ValueError("Gateway credentials require a literal backend token file")
    validate_registry(stripped)
    return document


def issue(directory, *, identifier, subject, role, scopes, expires_in=86400, service_ids=None, node_ids=None,
          runtime_directory=None):
    if not 60 <= expires_in <= 365 * 86400:
        raise ValueError("Credential lifetime must be 60..31536000 seconds")
    if ":" in identifier:
        raise ValueError("Credential export ids must not contain colons")
    directory = _directory(directory)
    backend_file, gateway_file = directory / "backend-credentials.json", directory / "mcp-credentials.json"
    runtime = PurePosixPath(runtime_directory) if runtime_directory else None
    if runtime is not None and (not runtime.is_absolute() or ".." in runtime.parts
            or str(runtime).startswith("//") or any(ord(c) < 32 for c in str(runtime))):
        raise ValueError("Runtime directory must be an absolute literal POSIX path")
    backend_token, gateway_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    entry = {"id": identifier, "subject": subject, "role": role,
        "token_sha256": hashlib.sha256(backend_token.encode()).hexdigest(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(), "scopes": scopes}
    if service_ids is not None:
        entry["service_ids"] = service_ids
    if node_ids is not None:
        entry["node_ids"] = node_ids
    validate_registry({"version": 1, "credentials": [entry]})
    # Identity validation above precedes deriving filenames from identifier.
    backend_token_file, gateway_token_file = directory / ("backend-" + identifier + ".token"), directory / ("mcp-" + identifier + ".token")
    gateway_entry = {**entry, "token_sha256": hashlib.sha256(gateway_token.encode()).hexdigest(),
        "backend_token_file": str(runtime / backend_token_file.name) if runtime else str(backend_token_file)}
    with store_lock(directory):
        backend = read_registry(backend_file) if backend_file.exists() or backend_file.is_symlink() else {"version": 1, "credentials": []}
        gateway = _gateway(gateway_file) if gateway_file.exists() or gateway_file.is_symlink() else {"version": 1, "credentials": []}
        if any(item["id"] == identifier for document in (backend, gateway) for item in document["credentials"]):
            raise ValueError("Credential id already exists; revoke it and use a new id")
        backend["credentials"].append(entry); gateway["credentials"].append(gateway_entry)
        validate_registry(backend)
        # Never overwrite existing credential exports, even when not registered.
        write_new(backend_token_file, backend_token.encode() + b"\n")
        write_new(gateway_token_file, gateway_token.encode() + b"\n")
        atomic_write(backend_file, _json(backend))
        atomic_write(gateway_file, _json(gateway))
    return {"id": identifier, "subject": subject, "backend_registry": str(backend_file),
        "gateway_registry": str(gateway_file), "backend_token_file": str(backend_token_file),
        "gateway_token_file": str(gateway_token_file)}


def revoke(directory, identifier):
    directory = _directory(directory)
    backend_file, gateway_file = directory / "backend-credentials.json", directory / "mcp-credentials.json"
    with store_lock(directory):
        backend, gateway = read_registry(backend_file), _gateway(gateway_file)
        matches = [[entry for entry in document["credentials"] if entry["id"] == identifier] for document in (backend, gateway)]
        if any(len(entries) != 1 for entries in matches):
            raise ValueError("Identity must exist in both registries before revocation")
        stamp = datetime.now(timezone.utc).isoformat()
        for entries in matches:
            entries[0]["revoked_at"] = stamp
        atomic_write(backend_file, _json(backend))
        atomic_write(gateway_file, _json(gateway))
    return {"id": identifier, "revoked": True, "backend_registry": str(backend_file), "gateway_registry": str(gateway_file)}


def assembly(output, *, module_root=None):
    sources = WEB / "backend/reference_modules"
    providers = []
    definitions = [
        ("motor.mcp-session", "mcp_access.py", {"security.session": 1}, {"storage.store": 1}),
        ("motor.mcp-operations", "mcp_operations.py", {CONTRACT: 1},
            {"storage.store": 1, "audit.sink": 1, "services.workflow": 1, "tasks.coordinator": 1}),
    ]
    for identifier, filename, contracts, dependencies in definitions:
        source = sources / filename
        providers.append({"id": identifier, "version": VERSION, "contracts": contracts,
            "dependencies": dependencies, "module_file": str(source.absolute()),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "factory": "factory", "trust_code": True})
    document = {"schemaVersion": 2, "providers": providers,
        "bindings": {"security.session": {"provider": "motor.mcp-session", "version": VERSION, "contract_version": 1},
            CONTRACT: {"provider": "motor.mcp-operations", "version": VERSION, "contract_version": 1}},
        "contract_definitions": [{"id": CONTRACT, "version": 1, "methods": [], "attributes": [], "optional": False, "critical": True}],
        "http_modules": [http_module()]}
    # Source-side validation checks schema, digests and Python syntax without
    # executing factories. Container path rewriting follows validation.
    with ExitStack() as cleanup:
        ProviderHost(settings={}, cleanup=cleanup, document=document)
    if module_root:
        runtime = PurePosixPath(module_root)
        if (not runtime.is_absolute() or ".." in runtime.parts or str(runtime).startswith("//")
                or any(ord(c) < 32 for c in str(runtime))):
            raise ValueError("Module root must be an absolute literal container backend path")
        for provider in providers:
            provider["module_file"] = str(runtime / "reference_modules" / Path(provider["module_file"]).name)
    output = Path(os.path.abspath(output))
    ensure_directory(output.parent)
    write_new(output, _json(document))
    return {"assembly_file": str(output), "activation": "explicit-next-start",
        "credential_environment": "MOTOR_SERVICE_CREDENTIALS_FILE", "assembly_environment": "MOTOR_PROVIDER_ASSEMBLY_FILE"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("assembly"); item.add_argument("--output", required=True, type=Path)
    item.add_argument("--module-root", help="Absolute container backend directory, e.g. /app/backend")
    item = sub.add_parser("issue"); item.add_argument("--directory", required=True, type=Path)
    item.add_argument("--id", required=True); item.add_argument("--subject", required=True)
    item.add_argument("--role", choices=("viewer", "operator"), required=True)
    item.add_argument("--scopes", nargs="+", required=True); item.add_argument("--expires-in", type=int, default=86400)
    item.add_argument("--service-ids", nargs="*"); item.add_argument("--node-ids", nargs="*")
    item.add_argument("--runtime-directory", help="Absolute gateway container directory for backend token references")
    item = sub.add_parser("revoke"); item.add_argument("--directory", required=True, type=Path); item.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "assembly":
            result = assembly(args.output, module_root=args.module_root)
        elif args.command == "issue":
            result = issue(args.directory, identifier=args.id, subject=args.subject, role=args.role, scopes=args.scopes,
                expires_in=args.expires_in, service_ids=args.service_ids, node_ids=args.node_ids, runtime_directory=args.runtime_directory)
        else:
            result = revoke(args.directory, args.id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        # Do not stringify unexpected OS/library exceptions: they may contain
        # deployment-owned paths or material supplied in a malformed registry.
        print(json.dumps({"error": "MCP preparation failed", "error_type": type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
