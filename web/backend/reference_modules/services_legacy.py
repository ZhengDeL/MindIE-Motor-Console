"""Preserve the published PluginContext v1 interfaces during provider migration.

Selected API factories explicitly contribute their actual objects. This bridge
never constructs reference implementations or silently substitutes a provider.
An alternative API can opt in with
``ctx.require("application_state").legacy_publications.publish(ctx, name, value)``.
"""
from __future__ import annotations

from ..plugin_host.context import PluginContext
from ..plugin_host.contracts import PluginConfigurationError


EXPORTS = {
    "branding.manager": ("branding", ("branding.manager",), "manager"),
    "nodes.manager": ("nodes", ("api.nodes",), "manager"),
    "services.routes": ("services", ("api.services", "api.tasks"), "routes"),
    "gateways.routes": ("gateways", ("api.gateways",), "routes"),
    "images.routes": ("images", ("api.images",), "routes"),
    "templates.routes": ("templates", ("api.templates",), "routes"),
}

LEGACY_API_CONTRACTS = ("api.nodes", "api.services", "api.tasks", "api.gateways", "api.images", "api.templates")


class LegacyPluginContext(PluginContext):
    """Same v1 context with an explicit compatibility diagnostic on consume."""
    legacy_publications = None

    def for_plugin(self, identifier):
        # Delegate scope and shared interface-table creation to the v1 context.
        # The subclass changes only the diagnostic; publication/RBAC stays there.
        scoped = super().for_plugin(identifier)
        result = object.__new__(type(self))
        vars(result).update(vars(scoped))
        result.legacy_publications = self.legacy_publications
        return result

    def consume(self, name, version):
        if self.legacy_publications is not None:
            self.legacy_publications.require_available(name, version)
        return super().consume(name, version)


class LegacyPublications:
    def __init__(self, context, bindings):
        self.context, self.bindings = context, {item["contract"]: item for item in bindings}
        self.parts, self.available, self.finalized = {}, set(), False

    def publish(self, ctx, name, value):
        """Explicitly promise a v1 export from the selected API factory scope."""
        self.publish_selected(ctx.contract, name, value)

    def publish_selected(self, contract, name, value):
        definition = EXPORTS.get(name)
        if self.finalized or definition is None or contract not in definition[1]:
            raise PluginConfigurationError("Invalid or late legacy v1 publication: " + str(name))
        owner, _contracts, kind = definition
        if not self.context.registry.enabled(owner):
            raise PluginConfigurationError("Disabled feature cannot publish a legacy interface: " + owner)
        key = name, contract
        if key in self.parts:
            raise PluginConfigurationError("Duplicate legacy v1 publication: " + name + " from " + contract)
        if kind == "routes":
            if (not isinstance(value, tuple) or not value
                    or any(not isinstance(getattr(router, "routes", None), list) for router in value)):
                raise PluginConfigurationError("Legacy v1 routes require the actual router tuple: " + name)
        elif value is None:
            raise PluginConfigurationError("Legacy v1 manager cannot be empty: " + name)
        if name == "branding.manager" and any(not callable(getattr(value, method, None)) for method in ("public", "save", "icon")):
            raise PluginConfigurationError("Legacy branding.manager v1 requires public/save/icon")
        if name == "nodes.manager" and any(not callable(getattr(value, method, None)) for method in
                ("create_plan", "execute", "get_job", "list_jobs", "connect", "cancel", "recover", "close")):
            raise PluginConfigurationError("Legacy nodes.manager v1 requires the complete rollout manager operations")
        self.parts[key] = value

    def finalize(self):
        if self.finalized:
            raise PluginConfigurationError("Legacy publications have already been finalized")
        for name, (owner, contracts, kind) in EXPORTS.items():
            if not self.context.registry.enabled(owner):
                continue
            if all((name, contract) in self.parts for contract in contracts):
                value = (tuple(router for contract in contracts for router in self.parts[name, contract])
                         if kind == "routes" else self.parts[name, contracts[0]])
                self.context.for_plugin(owner).provide(name, 1, value)
                self.available.add(name)
        self.finalized = True

    def require_available(self, name, version):
        if name not in EXPORTS:
            return
        if version != 1:
            raise PluginConfigurationError("Legacy interface version is unsupported: " + name + " v" + str(version))
        if name in self.available:
            return
        owner, contracts, _kind = EXPORTS[name]
        selected = ", ".join(contract + "=" + str(self.bindings.get(contract, {}).get("provider")) for contract in contracts)
        reason = ("feature " + owner + " is disabled" if not self.context.registry.enabled(owner)
                  else "the selected provider did not explicitly publish the compatible v1 interface")
        raise PluginConfigurationError("Legacy interface unavailable: " + name + " v1; " + reason +
            "; " + selected + ". Select a compatible provider or migrate this plugin's consume() dependency; no reference fallback was constructed.")
