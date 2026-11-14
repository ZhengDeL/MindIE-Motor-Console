"""Deterministic plugin assembly; no package installation or HTTP code loading."""
from __future__ import annotations

from contextlib import AbstractAsyncContextManager, AbstractContextManager, ExitStack
from dataclasses import asdict
import hashlib
import inspect
import json
import re
from typing import Any, Callable, Iterable

from .contracts import (CATALOG_SCHEMA_VERSION, PLUGIN_API_VERSION,
                        PluginConfigurationError, PluginDisabledError,
                        PluginInstance, PluginManifest, RouteDeclaration, synchronous_cleanup)
from .context import PluginContext


_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_CAPABILITY = re.compile(r"[a-z][a-z0-9.:_-]{0,95}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?")
_PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
_HOST_PATHS = (
    "/api/meta", "/api/plugins", "/api/plugin-packages", "/api/plugin-sdk", "/api/auth/status", "/api/auth/bootstrap",
    "/api/auth/login", "/api/auth/logout", "/api/auth/password", "/api/auth/oidc",
    "/api/system/version", "/api/system/configuration", "/agent/health", "/agent/meta", "/agent/inventory",
)


def _route_parts(route: RouteDeclaration) -> tuple[str, ...]:
    if (not isinstance(route, RouteDeclaration) or not isinstance(route.path, str)
            or not route.path.startswith("/") or route.path == "/"
            or route.path.endswith("/") or type(route.subtree) is not bool):
        raise PluginConfigurationError("Plugin routes must be absolute API paths without a trailing slash")
    parts = tuple(route.path[1:].split("/"))
    if len(parts) < 2 or parts[0] not in {"api", "agent"}:
        raise PluginConfigurationError("Plugin routes must be inside /api or /agent")
    for part in parts:
        if not _PLACEHOLDER.fullmatch(part) and not re.fullmatch(r"[a-zA-Z0-9_.-]+", part):
            raise PluginConfigurationError("Plugin route contains an unsupported segment: " + route.path)
        if part in {".", ".."}:
            raise PluginConfigurationError("Plugin route contains a relative path segment")
    for path in _HOST_PATHS:
        reserved = tuple(path[1:].split("/"))
        if len(parts) <= len(reserved) and (route.subtree or len(parts) == len(reserved)):
            if all(_PLACEHOLDER.fullmatch(part) or part == value for part, value in zip(parts, reserved)):
                raise PluginConfigurationError("Plugin route would replace a required host endpoint: " + route.path)
    return parts


def _canonical_parts(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple("{}" if _PLACEHOLDER.fullmatch(part) else part for part in parts)


def _intersects(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return all(a == b or a == "{}" or b == "{}" for a, b in zip(left, right))


def _selection(disabled: str | Iterable[str]) -> frozenset[str]:
    if isinstance(disabled, str):
        if len(disabled) > 8192:
            raise PluginConfigurationError("Disabled plugin selection is too long")
        values = [value.strip() for value in disabled.split(",") if value.strip()]
    else:
        try:
            values = list(disabled)
        except TypeError as exc:
            raise PluginConfigurationError("Disabled plugins must be a string or ID collection") from exc
    if len(values) > 256 or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in values):
        raise PluginConfigurationError("Disabled plugins must be literal registered plugin IDs")
    if len(set(values)) != len(values):
        raise PluginConfigurationError("Disabled plugin selection contains a duplicate ID")
    return frozenset(values)


class PluginRegistry:
    """Freeze a validated manifest graph before opening business resources.

    Disabled dependencies are a configuration error rather than an implicit
    cascade. Operators see exactly which capabilities a deployment will expose.
    Factories are provided by reviewed host code and can run only once per ID.
    """

    def __init__(self, manifests: Iterable[PluginManifest], *, disabled: str | Iterable[str] = "",
                 active_storage_plugin: str | None = None):
        self._items: dict[str, PluginManifest] = {}
        self._routes: list[tuple[tuple[int, int, int], tuple[str, ...], bool, str]] = []
        self._mounted: set[str] = set()
        self._sealed = False
        features: dict[str, str] = {}
        pages: dict[str, str] = {}
        declarations: dict[tuple[str, ...], str] = {}
        try:
            definitions = iter(manifests)
        except TypeError as exc:
            raise PluginConfigurationError("Plugin manifests must be an iterable of immutable declarations") from exc
        for manifest in definitions:
            if len(self._items) >= 256:
                raise PluginConfigurationError("Plugin registry exceeds its 256-plugin bound")
            self._validate_manifest(manifest)
            if manifest.id in self._items:
                raise PluginConfigurationError("Duplicate plugin ID: " + manifest.id)
            self._items[manifest.id] = manifest
            for key in manifest.capabilities:
                if key in features:
                    raise PluginConfigurationError("Duplicate plugin capability: " + key)
                features[key] = manifest.id
            for page in manifest.pages:
                if page in pages:
                    raise PluginConfigurationError("Duplicate plugin page: " + page)
                pages[page] = manifest.id
            for route in manifest.routes:
                parts = _canonical_parts(_route_parts(route))
                if parts in declarations:
                    raise PluginConfigurationError("Duplicate plugin route: " + route.path)
                declarations[parts] = manifest.id
                score = (sum(part != "{}" for part in parts), len(parts), int(not route.subtree))
                for previous_score, previous, previous_subtree, owner in self._routes:
                    if owner != manifest.id and score == previous_score and _intersects(parts, previous):
                        raise PluginConfigurationError("Ambiguous plugin routes: " + owner + " / " + manifest.id)
                self._routes.append((score, parts, route.subtree, manifest.id))
        if not self._items:
            raise PluginConfigurationError("At least one trusted plugin declaration is required")
        if (active_storage_plugin is not None and
                (not isinstance(active_storage_plugin, str) or active_storage_plugin not in self._items or self._items[active_storage_plugin].kind != "storage"
                 or not self._items[active_storage_plugin].required)):
            raise PluginConfigurationError("Active storage must name a required storage plugin")
        self._active_storage_plugin = active_storage_plugin
        self._disabled = _selection(disabled)
        unknown = self._disabled - self._items.keys()
        if unknown:
            raise PluginConfigurationError("Unknown disabled plugins: " + ", ".join(sorted(unknown)))
        required = sorted(identifier for identifier in self._disabled if self._items[identifier].required)
        if required:
            raise PluginConfigurationError("Required plugins cannot be disabled: " + ", ".join(required))
        self._order = self._validate_dependencies()
        for identifier in self._order:
            if self.enabled(identifier):
                missing = set(self._items[identifier].dependencies) & self._disabled
                if missing:
                    raise PluginConfigurationError("Plugin " + identifier + " requires disabled plugins: " + ", ".join(sorted(missing)))
        self._routes.sort(key=lambda value: value[0], reverse=True)
        self._page_owners = pages

    @staticmethod
    def _validate_manifest(manifest: PluginManifest) -> None:
        if not isinstance(manifest, PluginManifest) or not isinstance(manifest.id, str) or not _ID.fullmatch(manifest.id):
            raise PluginConfigurationError("Plugin ID must contain lowercase letters, digits and hyphens")
        if type(manifest.api_version) is not int or manifest.api_version != PLUGIN_API_VERSION:
            raise PluginConfigurationError("Unsupported plugin API version: " + manifest.id)
        if (not isinstance(manifest.version, str) or not _VERSION.fullmatch(manifest.version)
                or type(manifest.required) is not bool):
            raise PluginConfigurationError("Plugin version or required flag is invalid: " + manifest.id)
        for value, maximum in ((manifest.name, 100), (manifest.description, 2000), (manifest.kind, 64)):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise PluginConfigurationError("Plugin display metadata is invalid: " + manifest.id)
        for values, pattern in ((manifest.dependencies, _ID), (manifest.capabilities, _CAPABILITY),
                                (manifest.pages, _ID), (manifest.runtimes, _ID)):
            if (not isinstance(values, tuple) or len(values) > 128
                    or any(not isinstance(value, str) or not pattern.fullmatch(value) for value in values)
                    or len(set(values)) != len(values)):
                raise PluginConfigurationError("Plugin declarations must be unique immutable ID tuples: " + manifest.id)
        if not isinstance(manifest.routes, tuple) or len(manifest.routes) > 128:
            raise PluginConfigurationError("Plugin route declarations must be immutable and bounded: " + manifest.id)

    def _validate_dependencies(self) -> tuple[str, ...]:
        done: set[str] = set()
        active: list[str] = []
        order: list[str] = []

        def visit(identifier: str) -> None:
            if identifier in done:
                return
            if identifier in active:
                raise PluginConfigurationError("Plugin dependency cycle: " + " -> ".join([*active, identifier]))
            if identifier not in self._items:
                raise PluginConfigurationError("Missing plugin dependency: " + identifier)
            active.append(identifier)
            for dependency in self._items[identifier].dependencies:
                visit(dependency)
            active.pop()
            done.add(identifier)
            order.append(identifier)

        for identifier in sorted(self._items):
            visit(identifier)
        return tuple(order)

    def enabled(self, identifier: str) -> bool:
        if not isinstance(identifier, str) or identifier not in self._items:
            raise PluginConfigurationError("Unknown plugin: " + str(identifier))
        return identifier not in self._disabled

    def require(self, identifier: str) -> None:
        if not self.enabled(identifier):
            raise PluginDisabledError(identifier)

    def dependencies(self, identifier: str) -> tuple[str, ...]:
        if not isinstance(identifier, str) or identifier not in self._items:
            raise PluginConfigurationError("Unknown plugin: " + str(identifier))
        return self._items[identifier].dependencies

    def owner_for_path(self, path: str, method: str | None = None) -> str | None:
        """Resolve trusted route ownership; this never substitutes for RBAC.

        ``method`` is accepted for middleware callers; route ownership is
        deliberately consistent across methods so a write cannot bypass a
        feature disabled only for its read route.
        """
        if not isinstance(path, str) or not path.startswith("/"):
            return None
        values = tuple(path.rstrip("/")[1:].split("/"))
        for _, parts, subtree, owner in self._routes:
            if (len(values) < len(parts) or not subtree and len(values) != len(parts)):
                continue
            if all(part == "{}" or part == value for part, value in zip(parts, values)):
                return owner
        return None

    def owner_for_page(self, page: str) -> str | None:
        return self._page_owners.get(page)

    def enabled_runtimes(self) -> list[str]:
        return sorted({runtime for item in self._items.values() if self.enabled(item.id) for runtime in item.runtimes})

    @property
    def order(self) -> tuple[str, ...]:
        return self._order

    @property
    def sealed(self) -> bool:
        return self._sealed

    def describe(self) -> dict:
        items = []
        for identifier in sorted(self._items):
            manifest = self._items[identifier]
            value = asdict(manifest)
            value.pop("routes")
            value.update(enabled=self.enabled(identifier), restart_required=True)
            items.append(value)
        catalog = {"schemaVersion": CATALOG_SCHEMA_VERSION, "api_version": PLUGIN_API_VERSION,
                   "trusted_code_only": True, "online_installation": False,
                   "runtime_toggle": False, "active_storage_plugin": self._active_storage_plugin, "items": items}
        raw = json.dumps(catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        return {**catalog, "digest": hashlib.sha256(raw).hexdigest()}

    def mount(self, identifier: str, factory: Callable[[Any], Any], context: Any, cleanup: ExitStack) -> Any:
        """Run one reviewed factory and register its cleanup with the host."""
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
            raise PluginConfigurationError("Plugin factory requires a literal registered plugin ID")
        if self._sealed or identifier in self._mounted:
            raise PluginConfigurationError("Plugin factory is duplicate or assembly is sealed: " + identifier)
        if not callable(factory) or not isinstance(cleanup, ExitStack):
            raise PluginConfigurationError("Plugin assembly requires a trusted callable and an ExitStack")
        if not self.enabled(identifier):
            return None
        if isinstance(context, PluginContext):
            if context.registry is not self or context.cleanup is not cleanup:
                raise PluginConfigurationError("Plugin context must belong to this registry and cleanup lifetime")
            context = context.for_plugin(identifier)
        # Reserve the identity before executing a factory to reject recursive
        # mounts and retries after a factory whose side effects are unknown.
        self._mounted.add(identifier)
        result = factory(context)
        if inspect.isawaitable(result) or isinstance(result, AbstractAsyncContextManager):
            if inspect.iscoroutine(result):
                result.close()
            raise PluginConfigurationError("Plugin factory must use the synchronous bootstrap contract: " + identifier)
        if isinstance(result, AbstractContextManager):
            return cleanup.enter_context(result)
        if isinstance(result, PluginInstance):
            if result.close is not None:
                cleanup.callback(synchronous_cleanup(result.close))
            return result.value
        return result

    def seal(self) -> None:
        """Disallow later factory execution, including calls from HTTP handlers."""
        self._sealed = True
