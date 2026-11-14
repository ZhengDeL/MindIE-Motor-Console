"""Actual bundled plugin factories; only host bootstrap selects these callables."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .context import PluginContext

if TYPE_CHECKING:
    from ..branding import BrandingManager


def default_branding() -> dict:
    """Return native product defaults without reading editable stored branding."""
    from ..branding import BrandingManager
    return BrandingManager.project(None)


def mount_branding(context: PluginContext) -> BrandingManager:
    """Provide a versioned manager and mount the actual editable branding API."""
    from ..branding import BrandingManager, create_branding_router
    if context.plugin_id != "branding":
        from .contracts import PluginConfigurationError
        raise PluginConfigurationError("Branding provider requires the branding plugin scope")
    manager = BrandingManager(context.require_service("store"), context.require_service("mutation_lock"))
    context.mount_router(create_branding_router(manager))
    context.provide("branding.manager", 1, manager)
    return manager
