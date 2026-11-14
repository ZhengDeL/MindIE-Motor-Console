"""Explicit host services and versioned interfaces for trusted plugin factories."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, TYPE_CHECKING

from .contracts import PLUGIN_API_VERSION, PluginConfigurationError, synchronous_cleanup

if TYPE_CHECKING:
    from .registry import PluginRegistry


_NAME = re.compile(r"[a-z][a-z0-9_.:-]{0,95}")


@dataclass(frozen=True)
class InterfaceBinding:
    provider: str
    name: str
    version: int
    value: Any


class PluginContext:
    """Bootstrap SDK with a host-owned dependency graph and cleanup lifetime.

    The context is not a permission sandbox. Installed code is trusted, while
    route ownership and declared interface dependencies catch integration
    mistakes before the application accepts requests. The host constructs one
    root context; ``PluginRegistry.mount`` supplies a plugin-scoped view.
    """

    api_version = PLUGIN_API_VERSION

    def __init__(self, *, application: Any, services: Mapping[str, Any],
                 registry: PluginRegistry, cleanup: ExitStack):
        if not isinstance(services, Mapping) or not isinstance(cleanup, ExitStack):
            raise PluginConfigurationError("PluginContext requires explicit host services and an ExitStack")
        if any(not isinstance(key, str) or not _NAME.fullmatch(key) for key in services):
            raise PluginConfigurationError("Host service names must be bounded literal identifiers")
        self.application = application
        self.registry = registry
        self.cleanup = cleanup
        self.services = MappingProxyType(dict(services))
        self._interfaces: dict[tuple[str, int], InterfaceBinding] = {}
        self._plugin_id: str | None = None

    @property
    def plugin_id(self) -> str | None:
        return self._plugin_id

    def for_plugin(self, identifier: str) -> PluginContext:
        self.registry.require(identifier)
        scoped = object.__new__(PluginContext)
        scoped.application, scoped.registry, scoped.cleanup = self.application, self.registry, self.cleanup
        scoped.services, scoped._interfaces = self.services, self._interfaces
        scoped._plugin_id = identifier
        return scoped

    def _bootstrap_owner(self) -> str:
        if self._plugin_id is None or self.registry.sealed:
            raise PluginConfigurationError("Plugin contributions require an active bootstrap scope")
        self.registry.require(self._plugin_id)
        return self._plugin_id

    @staticmethod
    def _interface_key(name: str, version: int) -> tuple[str, int]:
        if not isinstance(name, str) or not _NAME.fullmatch(name) or type(version) is not int or not 1 <= version <= 65535:
            raise PluginConfigurationError("Plugin interfaces require a literal name and a positive integer version")
        return name, version

    def require_service(self, name: str, expected_type: type | tuple[type, ...] | None = None) -> Any:
        if name not in self.services:
            raise PluginConfigurationError("Required host service is unavailable: " + str(name))
        value = self.services[name]
        if expected_type is not None and not isinstance(value, expected_type):
            raise PluginConfigurationError("Host service has an incompatible type: " + name)
        return value

    def provide(self, name: str, version: int, value: Any) -> Any:
        """Publish one exact interface version during the provider's factory."""
        owner = self._bootstrap_owner()
        key = self._interface_key(name, version)
        if key in self._interfaces:
            raise PluginConfigurationError("Duplicate plugin interface: " + name + " v" + str(version))
        if any(binding.name == name and binding.provider != owner for binding in self._interfaces.values()):
            raise PluginConfigurationError("Plugin interface name has another provider: " + name)
        self._interfaces[key] = InterfaceBinding(owner, name, version, value)
        return value

    def consume(self, name: str, version: int) -> Any:
        """Resolve an exact version, allowing only direct declared dependencies."""
        key = self._interface_key(name, version)
        binding = self._interfaces.get(key)
        if binding is None:
            raise PluginConfigurationError("Required plugin interface is unavailable: " + name + " v" + str(version))
        if self._plugin_id is not None:
            self.registry.require(self._plugin_id)
            permitted = {self._plugin_id, *self.registry.dependencies(self._plugin_id)}
            if binding.provider not in permitted:
                raise PluginConfigurationError("Plugin " + self._plugin_id + " has no declared dependency on " + binding.provider)
        self.registry.require(binding.provider)
        return binding.value

    def on_shutdown(self, callback: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
        """Dispose owned workers/clients on assembly failure or host shutdown."""
        self._bootstrap_owner()
        self.cleanup.callback(synchronous_cleanup(callback), *args, **kwargs)

    def mount_router(self, router: Any, **options: Any) -> None:
        """Mount an API router only inside this plugin's declared ownership."""
        owner = self._bootstrap_owner()
        include = getattr(self.application, "include_router", None)
        routes = getattr(router, "routes", None)
        if not callable(include) or not isinstance(routes, list) or not routes:
            raise PluginConfigurationError("Plugin router requires a host application and declared API routes")
        # Prefixing after validation could move a route into another plugin's
        # subtree. Declare the complete prefix on the router instead.
        if options.get("prefix"):
            raise PluginConfigurationError("Plugin routers must declare their full prefix before mounting")
        for route in routes:
            path = getattr(route, "path", None)
            if not isinstance(path, str) or self.registry.owner_for_path(path) != owner:
                raise PluginConfigurationError("Plugin router is outside declared ownership: " + str(path))
        include(router, **options)
