"""Stable console plugin contracts; business plugins are trusted installed code."""
from .builtin import build_registry, builtin_manifests
from .contracts import (CATALOG_SCHEMA_VERSION, PLUGIN_API_VERSION, PluginConfigurationError,
                        PluginDisabledError, PluginInstance, PluginManifest, RouteDeclaration)
from .registry import PluginRegistry
from .context import PluginContext

__all__ = ["CATALOG_SCHEMA_VERSION", "PLUGIN_API_VERSION", "PluginConfigurationError",
           "PluginDisabledError", "PluginInstance", "PluginManifest", "PluginRegistry", "PluginContext",
           "RouteDeclaration", "build_registry", "builtin_manifests"]
