"""Versioned declarations for trusted, deployment-installed console plugins.

These contracts describe composition, not a sandbox. Authentication, resource
ownership, admission and audit enforcement remain host responsibilities.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable


PLUGIN_API_VERSION = 1
CATALOG_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RouteDeclaration:
    """Own a literal route or a subtree, with whole-segment placeholders only."""

    path: str
    subtree: bool = True


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    description: str
    kind: str = "feature"
    version: str = "1.0.0"
    api_version: int = PLUGIN_API_VERSION
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    pages: tuple[str, ...] = ()
    runtimes: tuple[str, ...] = ()
    routes: tuple[RouteDeclaration, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class PluginInstance:
    """A factory result whose cleanup is owned by the application's ExitStack."""

    value: Any = None
    close: Callable[[], Any] | None = None


class PluginConfigurationError(ValueError):
    """Fail application assembly instead of enabling an incomplete plugin graph."""


class PluginDisabledError(RuntimeError):
    def __init__(self, identifier: str):
        self.plugin_id = identifier
        super().__init__("Console plugin is disabled: " + identifier)


def synchronous_cleanup(callback: Callable[..., Any]) -> Callable[..., Any]:
    """Reject async disposal while allowing ExitStack to finish other callbacks."""
    if (not callable(callback) or inspect.iscoroutinefunction(callback)
            or inspect.iscoroutinefunction(getattr(callback, "__call__", None))):
        raise PluginConfigurationError("Plugin cleanup must implement the synchronous lifecycle contract")
    def run(*args: Any, **kwargs: Any) -> Any:
        result = callback(*args, **kwargs)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise PluginConfigurationError("Plugin cleanup returned an awaitable under the synchronous lifecycle contract")
        return result
    return run
