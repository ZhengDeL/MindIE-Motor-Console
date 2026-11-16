"""Opt-in service sessions for the reference MCP integration.

The assembly must explicitly select this SessionV1 provider. Browser cookies,
CSRF and explicitly enabled legacy credentials keep their existing semantics.
Service credentials have a separate revocable, hash-only, deployment-owned file.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re

from fastapi import HTTPException

from backend.agent_credentials import read_private_file
from backend.auth import AuthSettings, Principal
from backend.plugin_host.packages.files import linked, parents
from backend.provider_host.defaults import LocalSessions

SCOPES = frozenset({"nodes:read", "templates:read", "services:read", "configuration:write",
    "deployment:plan", "deployment:apply", "jobs:read", "logs:read", "metrics:read"})
WRITE_SCOPES = frozenset({"configuration:write", "deployment:plan", "deployment:apply"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_READ = frozenset({"GET", "HEAD"})


class CredentialRegistryError(ValueError):
    """The complete registry failed safe validation; no entry is accepted."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise CredentialRegistryError("Duplicate service credential JSON key")
        result[key] = value
    return result


def expiry(value):
    if not isinstance(value, str) or len(value) > 64:
        raise CredentialRegistryError("Credential timestamps require an ISO8601 timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise CredentialRegistryError("Credential timestamps require an ISO8601 timezone") from None


def validate_registry(document):
    if (not isinstance(document, dict) or set(document) != {"version", "credentials"}
            or type(document["version"]) is not int or document["version"] != 1
            or not isinstance(document["credentials"], list) or len(document["credentials"]) > 1024):
        raise CredentialRegistryError("Service registry requires version=1 and bounded credentials")
    ids, hashes = set(), set()
    required = {"id", "subject", "role", "token_sha256", "expires_at", "scopes"}
    optional = {"service_ids", "node_ids", "revoked_at"}
    for entry in document["credentials"]:
        if not isinstance(entry, dict) or not required <= set(entry) or set(entry) - required - optional:
            raise CredentialRegistryError("Invalid service credential schema")
        if any(not isinstance(entry[key], str) or not _ID.fullmatch(entry[key]) for key in ("id", "subject")):
            raise CredentialRegistryError("Invalid service credential identity")
        if entry["role"] not in {"viewer", "operator"}:
            raise CredentialRegistryError("Service credentials may only be viewer or operator")
        if not isinstance(entry["token_sha256"], str) or not _HASH.fullmatch(entry["token_sha256"]):
            raise CredentialRegistryError("Service credentials require SHA256 token hashes")
        if entry["id"] in ids or entry["token_sha256"] in hashes:
            raise CredentialRegistryError("Duplicate service credential identity or token hash")
        ids.add(entry["id"]); hashes.add(entry["token_sha256"])
        expiry(entry["expires_at"])
        if "revoked_at" in entry:
            expiry(entry["revoked_at"])
        scopes = entry["scopes"]
        if (not isinstance(scopes, list) or any(not isinstance(scope, str) or scope not in SCOPES for scope in scopes)
                or len(set(scopes)) != len(scopes)
                or entry["role"] == "viewer" and WRITE_SCOPES.intersection(scopes)):
            raise CredentialRegistryError("Invalid scopes or scopes exceed the service role")
        for field in ("service_ids", "node_ids"):
            if field not in entry:
                continue
            values = entry[field]
            if (not isinstance(values, list) or len(values) > 1024
                    or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in values)
                    or len(set(values)) != len(values)):
                raise CredentialRegistryError("Target restrictions must be explicit bounded identity lists")
    return document


def read_registry(path):
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise CredentialRegistryError("Service registry requires an absolute literal path")
        parents(path.parent)
        if linked(path.lstat()):
            raise CredentialRegistryError("Service registry must not be a link or reparse point")
        raw = read_private_file(path, maximum=1024 * 1024)
        return validate_registry(json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(CredentialRegistryError("Nonfinite JSON"))))
    except CredentialRegistryError:
        raise
    except Exception:
        raise CredentialRegistryError("Service credential registry could not be read safely") from None


def require_scope(entry, scope):
    if scope not in entry["scopes"] or scope in WRITE_SCOPES and entry["role"] != "operator":
        raise HTTPException(403, {"code": "service_scope_denied", "scope": scope})


def require_service_target(entry, service, *, placements=()):
    if "service_ids" in entry and service["id"] not in entry["service_ids"]:
        raise HTTPException(403, {"code": "service_target_denied"})
    if "node_ids" in entry:
        all_placements = [*service.get("placements", []), *service.get("deployed_placements", []), *placements]
        if any(not isinstance(p, dict) or not p.get("node_id") or p["node_id"] not in entry["node_ids"] for p in all_placements):
            raise HTTPException(403, {"code": "node_target_denied"})


class ServiceSessions(LocalSessions):
    def __init__(self, store, settings, legacy_token, registry_path):
        super().__init__(store, settings, legacy_token)
        self.registry_path = Path(registry_path)
        read_registry(self.registry_path)

    def authenticate(self, request, *, required=True):
        # Cookies win. A supplied bearer can never bypass browser CSRF or turn
        # an expired browser cookie into a service session.
        if request.cookies.get(self.cookie_name):
            return super().authenticate(request, required=required)
        supplied = request.headers.get("authorization", "")
        if not supplied:
            return super().authenticate(request, required=required)
        try:
            document = read_registry(self.registry_path)
        except CredentialRegistryError as error:
            raise HTTPException(503, {"code": "service_registry_unavailable"}) from error
        raw = supplied[7:] if supplied.startswith("Bearer ") else ""
        if not 24 <= len(raw) <= 2000 or any(ord(c) <= 32 or ord(c) >= 127 for c in raw):
            raise HTTPException(401, {"code": "invalid_service_credential"})
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        matches = [entry for entry in document["credentials"] if hmac.compare_digest(entry["token_sha256"], token_hash)]
        if not matches:
            return super().authenticate(request, required=required)
        entry = matches[0]
        if "revoked_at" in entry or datetime.now(timezone.utc) >= expiry(entry["expires_at"]):
            raise HTTPException(401, {"code": "expired_or_revoked_service_credential"})
        from backend.observability import register_secret
        register_secret(raw)
        self._authorize_route(entry, request)
        principal = Principal("service:" + entry["subject"], entry["subject"], entry["subject"], entry["role"],
            kind="machine", auth_source="service", expires_at=expiry(entry["expires_at"]).timestamp())
        request.state.mcp_credential = dict(entry)
        request.state.principal = principal
        return principal

    def _authorize_route(self, entry, request):
        method, path = request.method, request.url.path.rstrip("/")
        read = method in _READ
        if read and path in {"/health", "/ready", "/api/meta"}:
            return
        if read and path == "/api/plugins/mcp/identity":
            return
        if path == "/api/templates" and read:
            return require_scope(entry, "templates:read")
        if (re.fullmatch(r"/api/templates/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", path) and read
                and not any(part in {".", ".."} for part in path.split("/"))):
            return require_scope(entry, "templates:read")
        if path == "/api/nodes" and read:
            require_scope(entry, "nodes:read")
            if "node_ids" in entry:
                raise HTTPException(403, {"code": "restricted_collection_denied"})
            return
        match = re.fullmatch(r"/api/nodes/([^/]+)(?:/kubernetes/nodes)?", path)
        if match and read:
            require_scope(entry, "nodes:read")
            if "node_ids" in entry and match[1] not in entry["node_ids"]:
                raise HTTPException(403, {"code": "node_target_denied"})
            return
        if path == "/api/services":
            require_scope(entry, "services:read" if read else "configuration:write")
            if read and ("service_ids" in entry or "node_ids" in entry):
                raise HTTPException(403, {"code": "restricted_collection_denied"})
            if method == "POST" and ("service_ids" in entry or "node_ids" in entry):
                raise HTTPException(403, {"code": "restricted_configuration_denied"})
            if read or method == "POST":
                return
        match = re.fullmatch(r"/api/services/([^/]+)(?:/(export|revisions|logs|metrics|observations))?", path)
        if match and (read or method == "PUT" and match[2] is None):
            field = match[2]
            scope = "configuration:write" if method == "PUT" else "logs:read" if field == "logs" else "metrics:read" if field in {"metrics", "observations"} else "services:read"
            require_scope(entry, scope)
            if method == "PUT" and "node_ids" in entry:
                raise HTTPException(403, {"code": "restricted_configuration_denied"})
            service = self.store.get("services", match[1])
            if service is None:
                raise HTTPException(404, "服务不存在")
            require_service_target(entry, service)
            return
        if path == "/api/jobs" and read:
            require_scope(entry, "jobs:read")
            if "service_ids" in entry or "node_ids" in entry:
                identifier = request.query_params.get("service_id", "")
                service = self.store.get("services", identifier) if identifier else None
                if service is None:
                    raise HTTPException(403, {"code": "restricted_collection_denied"})
                require_service_target(entry, service)
                for job in self.store.list("jobs"):
                    if job.get("service_id") == identifier:
                        require_service_target(entry, service, placements=job.get("execution_placements", []))
            return
        match = re.fullmatch(r"/api/jobs/([^/]+)", path)
        if match and read:
            require_scope(entry, "jobs:read")
            job = self.store.get("jobs", match[1])
            service = self.store.get("services", job.get("service_id", "")) if job else None
            if service is None:
                raise HTTPException(404, "任务或服务不存在")
            require_service_target(entry, service, placements=job.get("execution_placements", []))
            return
        if method == "POST" and path == "/api/plugins/mcp/operations/plan":
            return require_scope(entry, "deployment:plan")
        if method == "POST" and path == "/api/plugins/mcp/operations/apply":
            return require_scope(entry, "deployment:apply")
        if read and re.fullmatch(r"/api/plugins/mcp/operations/plans/[^/]+", path):
            return require_scope(entry, "deployment:plan")
        # Every undeclared path/method, including direct deploy/stop/scale and
        # account/system/Agent APIs, is denied even for an operator credential.
        raise HTTPException(403, {"code": "service_route_denied"})


def factory(ctx):
    filename = os.getenv("MOTOR_SERVICE_CREDENTIALS_FILE", "")
    if not filename:
        raise CredentialRegistryError("Selected service sessions require MOTOR_SERVICE_CREDENTIALS_FILE")
    return ServiceSessions(ctx.consume("storage.store"), AuthSettings.from_env(),
        ctx.settings["console_token"], filename)
