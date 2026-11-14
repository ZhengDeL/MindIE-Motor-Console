"""Capture public routers before FastAPI creates its version-specific wrappers."""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..plugin_host.contracts import PluginConfigurationError

FRONTEND_METHODS = frozenset({"GET", "HEAD"})


def protect_extension_route(route, host, session, policy, audit, declarations):
    """Attach host enforcement to the ASGI route, independent of endpoint code.

    Direct APIRoutes retain their original validation/response models. They are
    mounted directly, so FastAPI cannot rebuild and discard this boundary.
    """
    from copy import copy
    from fastapi import HTTPException
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from .authorization import authenticate_session

    secured = copy(route)
    endpoint_app = route.app

    class AuditResultUnavailable(RuntimeError):
        pass

    async def boundary(scope, receive, send):
        request = Request(scope, receive)
        path, method = scope["path"], scope["method"]
        declaration = declarations.get((route.path, method))
        try:
            if declaration is None:
                raise HTTPException(405, "HTTP method is outside the selected module declaration")
            roles = frozenset(declaration["roles"])
            authenticated = authenticate_session(session, request, required=True)
            request.state.principal = authenticated
            principal = policy.authorize(request, roles, csrf=True, account_only=declaration["account_only"])
            if principal is None:
                raise HTTPException(503, "Selected authorization provider returned no identity")
            if (authenticated.role not in roles or principal.role != authenticated.role
                    or principal.kind != authenticated.kind or principal.user_id != authenticated.user_id
                    or declaration["account_only"] and principal.kind != "session"):
                raise HTTPException(403, "Identity is outside the selected module permission")
            unsafe = method not in {"GET", "HEAD", "OPTIONS"}
            if unsafe:
                # Even a permissive third-party Policy cannot disable CSRF.
                if authenticated.kind == "session":
                    session.check_csrf(request)
                generation = request.headers.get("X-Motor-Expected-Generation", "")
                if generation and generation != host.generation:
                    raise HTTPException(409, {"code": "provider_generation_mismatch"})
                try:
                    audit.audit("module.write.admitted", path, "accepted",
                        metadata={"method": method, "provider_generation": host.generation})
                except Exception as error:
                    raise HTTPException(503, {"code": "operation_audit_unavailable",
                        "outcome_unknown": False}) from error

            async def audited_send(message):
                if unsafe and message["type"] == "http.response.start":
                    try:
                        audit.audit("module.write.result", path,
                            "success" if message["status"] < 400 else "rejected",
                            metadata={"method": method, "status_code": message["status"],
                                "provider_generation": host.generation})
                    except Exception as error:
                        # FastAPI has already marked its own response started
                        # before calling send(). A new HTTPException would be
                        # converted into a generic 500 by its inner handler.
                        # Raise a private transport signal and emit the safe
                        # response at this outer boundary, before forwarding
                        # any original response bytes.
                        raise AuditResultUnavailable() from error
                await send(message)

            await endpoint_app(scope, receive, audited_send)
        except AuditResultUnavailable:
            await JSONResponse({"detail": {"code": "operation_audit_unavailable",
                "outcome_unknown": True}}, status_code=503)(scope, receive, send)
        except HTTPException as error:
            await JSONResponse({"detail": error.detail}, status_code=error.status_code,
                headers=error.headers)(scope, receive, send)

    secured.app = boundary
    return secured


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
