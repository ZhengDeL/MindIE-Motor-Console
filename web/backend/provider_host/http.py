"""Capture public routers before FastAPI creates its version-specific wrappers."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..plugin_host.contracts import PluginConfigurationError

FRONTEND_METHODS = frozenset({"GET", "HEAD"})


def http_route_methods(contract, route):
    """Reject transports the HTTP authorization boundary cannot protect.

    Standard StaticFiles is the explicit read-only ASGI exception, available
    to every frontend provider under the same rule as the reference package.
    Arbitrary ASGI mounts and WebSocket routes require a different contract.
    """
    if isinstance(route, Route):
        methods = getattr(route, "methods", None)
        if (not isinstance(methods, (set, frozenset, tuple, list)) or not methods
                or any(not isinstance(method, str) or not method or method != method.upper() for method in methods)):
            raise PluginConfigurationError("HTTP provider requires an explicit nonempty HTTP method set: " + contract)
        methods = frozenset(methods)
    elif contract == "api.frontend" and type(route) is Mount and type(route.app) is StaticFiles:
        methods = FRONTEND_METHODS
    else:
        raise PluginConfigurationError("HTTP provider does not support WebSocket or arbitrary ASGI routes: " + contract)
    if contract == "api.frontend" and not methods <= FRONTEND_METHODS:
        raise PluginConfigurationError("Frontend provider routes must use only GET/HEAD; writes require a protected API contract")
    return methods


class ProviderApplication(FastAPI):
    """Normal FastAPI behavior after one explicit provider-assembly boundary.

    Router ownership is checked against APIRouter.routes before include_router
    transforms them. No private FastAPI route-wrapper type is inspected.
    """
    def __init__(self, *args, **kwargs):
        self.provider_router_sources = []
        self.provider_routes_mounted = False
        super().__init__(*args, **kwargs)

    def include_router(self, router, **options):
        if self.provider_routes_mounted:
            raise PluginConfigurationError("HTTP graph is sealed; router changes require restart")
        if options.get("prefix"):
            raise PluginConfigurationError("Provider routers must declare complete paths before capture")
        for route in router.routes:
            if not isinstance(getattr(route, "path", None), str):
                raise PluginConfigurationError("HTTP provider v1 requires direct routes; flatten nested routers before returning them")
        self.provider_router_sources.append((router, dict(options)))

    def provider_routes(self):
        sources = {}
        routes = list(self.router.routes)
        for router, options in self.provider_router_sources:
            for route in router.routes:
                routes.append(route)
                sources[id(route)] = (router, options)
        return routes, sources

    def mount_provider_routes(self, routes, sources):
        self.router.routes[:] = []
        for route in routes:
            source = sources.get(id(route))
            if source is None:
                self.router.routes.append(route)
            else:
                _original, options = source
                # A one-route public router preserves the original route's
                # response schema and the include dependencies/tags/options.
                fragment = APIRouter(routes=[route])
                FastAPI.include_router(self, fragment, **options)
        self.provider_router_sources.clear()
        self.provider_routes_mounted = True
