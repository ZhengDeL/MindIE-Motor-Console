"""Compose direct public routers before the host creates FastAPI wrappers."""
from fastapi import APIRouter
from fastapi.routing import APIRoute

from ..plugin_host.contracts import PluginConfigurationError


def append_router(target: APIRouter, source: APIRouter, *, dependencies=()):
    """Preserve public route definitions and router-owned cleanup callbacks.

    The host captures direct routes before FastAPI's include_router conversion.
    Authentication remains enforced by its mandatory HTTP middleware too.
    """
    for route in source.routes:
        if not isinstance(getattr(route, "path", None), str):
            raise PluginConfigurationError("Reference modules require direct public router definitions")
        if dependencies:
            if not isinstance(route, APIRoute):
                raise PluginConfigurationError("Protected reference routes require APIRoute dependencies")
            route.dependencies.extend(dependencies)
        target.routes.append(route)
    target.on_shutdown.extend(source.on_shutdown)
