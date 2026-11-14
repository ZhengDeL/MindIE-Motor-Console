"""Data-only package contract, checked before any third-party import."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from ...version import CONSOLE_VERSION
from ..builtin import builtin_manifests
from ..contracts import PluginManifest, RouteDeclaration
from .errors import PluginPackageError
from .limits import MAX_PACKAGE_BYTES, MAX_UNCOMPRESSED_BYTES, MAX_FILE_BYTES, MAX_FILES, MAX_ACTIVE_PLUGINS

PACKAGE_SCHEMA_VERSION = 1
_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_MODULE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[A-Za-z0-9.-]+)?")
_HOST_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_ACCESS = {"viewer": 0, "operator": 1, "admin": 2}
TEXT_LIMITS = {"name": 100, "description": 2000, "author": 200, "license": 200}
MAX_ROUTES = 32
MAX_DEPENDENCIES = 32
MAX_NAVIGATION = 8
RESERVED_IDS = frozenset({item.id for item in builtin_manifests()} | {
    "packages", "activation", "sdk", "state", "configuration", "health", "catalog", "import", "install"})


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise PluginPackageError("JSON contains duplicate property names")
        result[key] = value
    return result


def json_document(raw: bytes, *, maximum: int = 131072) -> Any:
    if not isinstance(raw, bytes) or len(raw) > maximum:
        raise PluginPackageError("JSON document exceeds its size limit")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PluginPackageError("JSON document is not bounded, unambiguous UTF-8 JSON") from exc


def canonical_json(value: Any, *, maximum: int = 1048576) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PluginPackageError("Plugin configuration must contain finite JSON values") from exc
    if len(raw) > maximum:
        raise PluginPackageError("Plugin configuration exceeds its size limit")
    return raw


def _text(value, name: str, maximum=200, *, empty=False) -> str:
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip())
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value)):
        raise PluginPackageError("Invalid plugin " + name)
    return value


def _id(value, name="ID") -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise PluginPackageError("Invalid plugin " + name)
    return value


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or len(value) > 32 or not _HOST_VERSION.fullmatch(value):
        raise PluginPackageError("Compatibility versions must be three numeric components")
    return tuple(int(part) for part in value.split("."))


def check_version_range(version, minimum, maximum=None):
    selected, low = version_tuple(version), version_tuple(minimum)
    high = version_tuple(maximum) if maximum is not None else None
    if high is not None and high <= low:
        raise PluginPackageError("Compatibility maximum must be greater than minimum")
    if selected < low or high is not None and selected >= high:
        raise PluginPackageError("Plugin is incompatible with the selected host or dependency version")


def _api_path(path: Any, identifier: str, *, literal=False) -> str:
    value = _text(path, "route", 512)
    base = "/api/plugins/" + identifier
    if not (value == base or value.startswith(base + "/")) or value.endswith("/"):
        raise PluginPackageError("Community plugin routes must stay within their own /api/plugins/<id> namespace")
    pattern = r"[A-Za-z0-9_.-]+" if literal else r"(?:[A-Za-z0-9_.-]+|\{[A-Za-z_][A-Za-z0-9_]*\})"
    if any(part in {".", ".."} or not re.fullmatch(pattern, part) for part in value[1:].split("/")):
        raise PluginPackageError("Plugin API route contains an unsupported path segment")
    return value


def _route_matches(route: dict, path: str) -> bool:
    parts, values = route["path"].strip("/").split("/"), path.strip("/").split("/")
    return (len(values) >= len(parts) and (route["subtree"] or len(values) == len(parts))
            and all(a == b or a.startswith("{") for a, b in zip(parts, values)))


def validate_manifest(value: Any, *, host_version: str = CONSOLE_VERSION,
                      reserved_ids: frozenset[str] | set[str] = RESERVED_IDS, check_compatibility: bool = True) -> dict:
    if not isinstance(value, dict):
        raise PluginPackageError("Plugin manifest must be a JSON object")
    value = json_document(canonical_json(value, maximum=131072))
    allowed = {"schemaVersion", "id", "name", "version", "description", "api_version", "entrypoint",
               "license", "author", "minimum_host_version", "maximum_host_version_exclusive", "dependencies",
               "dependency_versions", "routes", "access", "navigation", "capabilities"}
    if set(value) - allowed:
        raise PluginPackageError("Manifest has unsupported properties: " + ", ".join(sorted(set(value) - allowed)))
    identifier = _id(value.get("id"))
    if identifier in reserved_ids:
        raise PluginPackageError("Community plugin ID is reserved by the host")
    if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != PACKAGE_SCHEMA_VERSION:
        raise PluginPackageError("Unsupported plugin package schema version")
    if type(value.get("api_version")) is not int or value["api_version"] != 1:
        raise PluginPackageError("Unsupported plugin host API version")
    for field, limit in TEXT_LIMITS.items():
        _text(value.get(field), field, limit)
    if not re.fullmatch(r"[A-Za-z0-9.+() -]+", value["license"]):
        raise PluginPackageError("License must be an explicit SPDX-style expression")
    if not isinstance(value.get("version"), str) or len(value["version"]) > 96 or not _VERSION.fullmatch(value["version"]):
        raise PluginPackageError("Plugin version must be a semantic three-component version")
    entry = value.get("entrypoint")
    if (not isinstance(entry, str) or entry.count(":") != 1 or len(entry) > 200
            or not _MODULE.fullmatch(entry.split(":")[0]) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry.split(":")[1])):
        raise PluginPackageError("Entrypoint must name a package-local module and one callable")
    access = value.get("access")
    if not isinstance(access, str) or access not in _ACCESS:
        raise PluginPackageError("Plugin access must explicitly be viewer, operator or admin")
    minimum = value.get("minimum_host_version")
    maximum = value.get("maximum_host_version_exclusive")
    # Retained older artifacts must remain inspectable for recovery after an
    # upgrade. Execution/import selection still use the strict default.
    check_version_range(host_version if check_compatibility else minimum, minimum, maximum)
    dependencies = value.get("dependencies", [])
    if (not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES
            or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in dependencies)
            or len(set(dependencies)) != len(dependencies) or identifier in dependencies):
        raise PluginPackageError("Plugin dependencies must be unique IDs without self-dependency")
    bounds = value.get("dependency_versions", {})
    if not isinstance(bounds, dict) or set(bounds) - set(dependencies):
        raise PluginPackageError("Dependency version bounds must name declared dependencies")
    for bound in bounds.values():
        if not isinstance(bound, dict) or set(bound) - {"minimum", "maximum_exclusive"} or "minimum" not in bound:
            raise PluginPackageError("Dependency version range is invalid")
        low = version_tuple(bound["minimum"])
        high = version_tuple(bound["maximum_exclusive"]) if "maximum_exclusive" in bound else None
        if high is not None and high <= low:
            raise PluginPackageError("Dependency maximum must be greater than minimum")
    routes = value.get("routes", [])
    if not isinstance(routes, list) or len(routes) > MAX_ROUTES:
        raise PluginPackageError("A plugin may declare at most 32 bounded API routes")
    route_keys = set()
    for route in routes:
        if not isinstance(route, dict) or set(route) - {"path", "subtree"} or "path" not in route:
            raise PluginPackageError("Plugin route declaration is invalid")
        _api_path(route["path"], identifier)
        if type(route.get("subtree", True)) is not bool:
            raise PluginPackageError("Route subtree must be boolean")
        route.setdefault("subtree", True)
        if route["path"] in route_keys:
            raise PluginPackageError("Plugin route declarations are duplicated")
        route_keys.add(route["path"])
    navigation = value.get("navigation", [])
    if not isinstance(navigation, list) or len(navigation) > MAX_NAVIGATION:
        raise PluginPackageError("Plugin navigation exceeds its limit")
    nav_ids = set()
    for nav in navigation:
        if not isinstance(nav, dict) or set(nav) - {"id", "title", "description", "kind", "endpoint", "group", "access"}:
            raise PluginPackageError("Plugin navigation declaration is invalid")
        nav_id = _id(nav.get("id"), "navigation ID")
        if nav_id in nav_ids:
            raise PluginPackageError("Plugin navigation IDs are duplicated")
        nav_ids.add(nav_id)
        _text(nav.get("title"), "navigation title", 100)
        _text(nav.get("description", ""), "navigation description", 500, empty=True)
        if nav.get("kind") != "status-table" or nav.get("group", "插件") != "插件":
            raise PluginPackageError("Only the bounded status-table navigation contribution is supported")
        endpoint = _api_path(nav.get("endpoint"), identifier, literal=True)
        if not any(_route_matches(route, endpoint) for route in routes):
            raise PluginPackageError("Navigation endpoint has no declared route")
        if not isinstance(nav.get("access"), str) or nav["access"] not in _ACCESS or _ACCESS[nav["access"]] < _ACCESS[access]:
            raise PluginPackageError("Navigation access cannot be weaker than the plugin API")
    capabilities = value.get("capabilities", [])
    if (not isinstance(capabilities, list) or len(capabilities) > 64
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9.:_-]{0,95}", item) for item in capabilities)
            or len(set(capabilities)) != len(capabilities)):
        raise PluginPackageError("Plugin capabilities are invalid")
    # A canonical round trip detaches callers and rejects non-JSON values.
    result = json_document(canonical_json(value))
    result.setdefault("dependencies", [])
    result.setdefault("dependency_versions", {})
    result.setdefault("navigation", [])
    result.setdefault("capabilities", [])
    return result


def declaration(manifest: dict) -> PluginManifest:
    return PluginManifest(manifest["id"], manifest["name"], manifest["description"], kind="community",
                          version=manifest["version"], dependencies=tuple(manifest["dependencies"]),
                          capabilities=tuple(manifest["capabilities"]),
                          routes=tuple(RouteDeclaration(item["path"], item["subtree"]) for item in manifest["routes"]))


def check_dependency_versions(manifests: list[dict], versions: dict[str, str]) -> None:
    for manifest in manifests:
        for identifier, bounds in manifest.get("dependency_versions", {}).items():
            if identifier not in versions:
                raise PluginPackageError("Versioned plugin dependency is unavailable: " + identifier)
            # v1 bounds are stable numeric releases. Never silently turn a
            # prerelease dependency into its final release for compatibility.
            check_version_range(versions[identifier], bounds["minimum"], bounds.get("maximum_exclusive"))


def manifest_schema() -> dict:
    """Publish shape validation plus the additional runtime cross-field rules.

    Regexes and numeric limits are the same objects used by the executable
    validator. JSON Schema alone cannot prove sibling IDs own an API route or
    resolve host/dependency versions, so those checks remain explicit.
    """
    identifier = {"type": "string", "pattern": "^" + _ID.pattern + "$", "maxLength": 64}
    plain_version = {"type": "string", "pattern": "^" + _HOST_VERSION.pattern + "$", "maxLength": 32}
    text = lambda maximum: {"type": "string", "minLength": 1, "maxLength": maximum,
                            "pattern": r"^[^\u0000-\u001f\u007f]+$"}
    access = {"type": "string", "enum": list(_ACCESS)}
    route = {"type": "object", "additionalProperties": False, "required": ["path"],
             "properties": {"path": {"type": "string", "maxLength": 512,
                 "pattern": r"^/api/plugins/[a-z][a-z0-9-]{0,63}(?:/(?:[A-Za-z0-9_.-]+|\{[A-Za-z_][A-Za-z0-9_]*\}))*$"},
                 "subtree": {"type": "boolean", "default": True}}}
    navigation = {"type": "object", "additionalProperties": False,
        "required": ["id", "title", "kind", "endpoint", "access"], "properties": {
            "id": identifier, "title": text(100), "description": {"type": "string", "maxLength": 500},
            "kind": {"const": "status-table"}, "group": {"const": "插件", "default": "插件"},
            "endpoint": {"type": "string", "maxLength": 512,
                         "pattern": r"^/api/plugins/[a-z][a-z0-9-]{0,63}(?:/[A-Za-z0-9_.-]+)*$"}, "access": access}}
    properties = {"schemaVersion": {"const": PACKAGE_SCHEMA_VERSION, "type": "integer"}, "id": identifier,
        **{name: text(maximum) for name, maximum in TEXT_LIMITS.items()},
        "version": {"type": "string", "pattern": "^" + _VERSION.pattern + "$", "maxLength": 96},
        "api_version": {"const": 1, "type": "integer"},
        "entrypoint": {"type": "string", "maxLength": 200,
                       "pattern": "^" + _MODULE.pattern + r":[A-Za-z_][A-Za-z0-9_]*$"},
        "minimum_host_version": plain_version, "maximum_host_version_exclusive": plain_version,
        "dependencies": {"type": "array", "items": identifier, "uniqueItems": True,
                         "maxItems": MAX_DEPENDENCIES, "default": []},
        "dependency_versions": {"type": "object", "propertyNames": identifier,
            "additionalProperties": {"type": "object", "additionalProperties": False, "required": ["minimum"],
                "properties": {"minimum": plain_version, "maximum_exclusive": plain_version}}},
        "routes": {"type": "array", "items": route, "maxItems": MAX_ROUTES, "default": []},
        "access": access, "navigation": {"type": "array", "items": navigation, "maxItems": MAX_NAVIGATION, "default": []},
        "capabilities": {"type": "array", "maxItems": 64, "uniqueItems": True,
                         "items": {"type": "string", "pattern": r"^[a-z][a-z0-9.:_-]{0,95}$"}}}
    properties["license"]["pattern"] = r"^[A-Za-z0-9.+() -]+$"
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:qinghuan:plugin-manifest:1",
        "title": "MindIE-Motor Console source plugin manifest", "type": "object", "additionalProperties": False,
        "required": ["schemaVersion", "id", "name", "version", "description", "api_version", "entrypoint",
                     "license", "author", "minimum_host_version", "access"], "properties": properties,
        "x-runtime-rules": ["IDs must not replace host or bundled plugins", "Every route stays in /api/plugins/<manifest.id>",
            "Navigation IDs are unique; endpoints have declared routes and cannot grant weaker access",
            "Version bounds are checked against the host and complete selected dependency graph",
            "Display text must be nonblank and exclude Unicode control, format and line-separator characters"],
        "x-package-limits": {"compressed_bytes": MAX_PACKAGE_BYTES, "expanded_bytes": MAX_UNCOMPRESSED_BYTES,
                             "member_bytes": MAX_FILE_BYTES, "members": MAX_FILES, "selected_plugins": MAX_ACTIVE_PLUGINS}}
