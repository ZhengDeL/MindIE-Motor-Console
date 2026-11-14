"""Data-only extension checks. These checks never import provider code.

Structural checks describe an interface, not behavior certification or a sandbox.
The host alone supplies immutable core definitions and selected bindings.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from .contracts import Contract

_NAMESPACE = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
_CONTRACT = re.compile(r"ext\.([a-z][a-z0-9-]{0,47})\.[a-z][a-z0-9_.-]{0,95}\Z")
_MEMBER = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,95}\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+|\{[a-zA-Z][a-zA-Z0-9_]*\}\Z")
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
ROLES = frozenset({"admin", "operator", "viewer"})
RESERVED_NAMESPACES = frozenset({"core", "internal", "host", "reference", "assembly", "console",
    "auth", "health", "ready", "meta", "packages", "activation", "sdk", "state",
    "configuration", "catalog", "import", "install"})


def extension_namespace(identifier: str) -> str:
    match = _CONTRACT.fullmatch(identifier) if isinstance(identifier, str) else None
    if match is None or match[1] in RESERVED_NAMESPACES:
        raise ValueError("Extension contract must use an independent ext.<namespace>.<name> identifier")
    return match[1]


def extension_contracts(definitions, core: Mapping[str, Contract]) -> dict[str, Contract]:
    """Return new definitions only; core contracts cannot be shadowed or internalized."""
    if not isinstance(definitions, list) or len(definitions) > 128:
        raise ValueError("contract_definitions must be a bounded list")
    result = {}
    required = {"id", "version", "methods", "attributes", "optional", "critical"}
    for item in definitions:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("Extension contract definition has an invalid schema")
        identifier = item["id"]
        extension_namespace(identifier)
        if identifier in core or identifier in result:
            raise ValueError("Duplicate or reserved extension contract: " + identifier)
        if type(item["version"]) is not int or not 1 <= item["version"] <= 65535:
            raise ValueError("Extension contract version must be an exact positive integer")
        if type(item["optional"]) is not bool or type(item["critical"]) is not bool:
            raise ValueError("Extension optional/critical flags must be explicit booleans")
        for field in ("methods", "attributes"):
            members = item[field]
            if (not isinstance(members, list) or len(members) > 128
                    or any(not isinstance(name, str) or not (_MEMBER.fullmatch(name)
                        or field == "methods" and name == "__call__") for name in members)
                    or len(set(members)) != len(members)):
                raise ValueError("Extension contract has invalid public " + field)
        if set(item["methods"]) & set(item["attributes"]):
            raise ValueError("Extension method and attribute names overlap")
        result[identifier] = Contract(identifier, item["version"], tuple(item["methods"]),
            item["optional"], tuple(item["attributes"]), critical=item["critical"])
    return result


def http_modules(declarations, contracts: Mapping[str, Contract]) -> tuple[dict, ...]:
    """Validate exact route ownership and explicit permissions before any factory."""
    if not isinstance(declarations, list) or len(declarations) > 64:
        raise ValueError("http_modules must be a bounded list")
    result, namespaces, owners = [], set(), set()
    for item in declarations:
        if not isinstance(item, dict) or set(item) != {"namespace", "contract", "routes"}:
            raise ValueError("HTTP module requires namespace, contract and explicit routes")
        namespace, contract, routes = item["namespace"], item["contract"], item["routes"]
        if (not isinstance(namespace, str) or not _NAMESPACE.fullmatch(namespace)
                or namespace in RESERVED_NAMESPACES or extension_namespace(contract) != namespace
                or contract not in contracts or contracts[contract].optional):
            raise ValueError("HTTP module must own an independently defined nonoptional contract")
        if namespace in namespaces or contract in owners:
            raise ValueError("Duplicate HTTP namespace or contract")
        if not isinstance(routes, list) or not 1 <= len(routes) <= 128:
            raise ValueError("HTTP module requires a bounded nonempty route declaration")
        base, normalized, seen = "/api/plugins/" + namespace, [], set()
        for route in routes:
            if not isinstance(route, dict) or set(route) != {"path", "methods", "roles", "account_only"}:
                raise ValueError("HTTP route requires explicit path, methods, roles and account_only")
            path, methods, roles = route["path"], route["methods"], route["roles"]
            if (not isinstance(path, str) or len(path) > 512 or not (path == base or path.startswith(base + "/"))
                    or path.endswith("/") or any(part in {"", ".", ".."} or not _SEGMENT.fullmatch(part)
                        for part in path[1:].split("/"))):
                raise ValueError("HTTP module paths must remain in their literal namespace; catchalls are unsupported")
            if (not isinstance(methods, list) or not methods or any(not isinstance(m, str) or m not in HTTP_METHODS for m in methods)
                    or len(set(methods)) != len(methods)):
                raise ValueError("HTTP module requires exact supported methods")
            if (not isinstance(roles, list) or not roles or any(not isinstance(r, str) or r not in ROLES for r in roles)
                    or len(set(roles)) != len(roles) or type(route["account_only"]) is not bool):
                raise ValueError("HTTP module requires explicit authenticated roles; public access is unsupported")
            if "viewer" in roles and not set(methods) <= {"GET", "HEAD", "OPTIONS"}:
                raise ValueError("Viewer is permanently read-only: unsafe HTTP module methods require admin/operator roles; use GET/HEAD for viewer queries")
            # Renaming a placeholder cannot distinguish a route in Starlette.
            shape = re.sub(r"\{[^}]+\}", "{}", path)
            for method in methods:
                if (shape, method) in seen:
                    raise ValueError("Conflicting HTTP module routes")
                seen.add((shape, method))
            for previous in normalized:
                a, b = previous["path"].split("/"), path.split("/")
                overlaps = len(a) == len(b) and all(x == y or x.startswith("{") or y.startswith("{") for x, y in zip(a, b))
                if overlaps and set(previous["methods"]) & set(methods):
                    raise ValueError("Ambiguous overlapping HTTP module routes")
            normalized.append({"path": path, "methods": list(methods), "roles": list(roles),
                "account_only": route["account_only"]})
        namespaces.add(namespace)
        owners.add(contract)
        result.append({"namespace": namespace, "contract": contract, "routes": normalized})
    return tuple(result)


def validate_product(contract: Contract, product) -> None:
    """Check required public members. This cannot certify operational semantics."""
    if product is None and contract.optional:
        return
    if (product is None or any(not callable(getattr(product, name, None)) for name in contract.methods)
            or any(not hasattr(product, name) for name in contract.attributes)):
        raise ValueError("Provider does not implement selected contract shape: " + contract.id)
