"""Host enforcement for optional console plugins, independent of native Motor."""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException

from .fleet import Fleet, FleetError
from .plugin_host import PluginRegistry


def require_plugin(registry: PluginRegistry, identifier: str) -> None:
    if not registry.enabled(identifier):
        raise HTTPException(503, {"code": "plugin_disabled", "plugin_id": identifier,
                                  "message": "此功能插件未启用，请检查部署侧插件配置"})


def require_runtime(registry: PluginRegistry, runtime: str) -> None:
    identifier = {"docker": "motor-docker", "kubernetes": "motor-kubernetes"}.get(runtime)
    if identifier is None:
        raise HTTPException(422, "不支持的服务运行时")
    require_plugin(registry, identifier)


class PluginFleet(Fleet):
    """Check runtime composition before any Agent connection or receipt retry.

    Neutral identity and exporter inventory stay available even when a runtime
    is disabled. The Agent independently enforces its own runtime composition.
    """
    def __init__(self, secrets, registry: PluginRegistry):
        super().__init__(secrets)
        self.registry = registry

    def call(self, node, method, path, body=None, **options):
        # Query strings belong to the request, not the route identity. Never
        # let a caller supply another origin or a dot/encoded path variant.
        if not isinstance(path, str) or not path.startswith("/agent/") or any(
                ord(character) < 32 for character in path) or "\\" in path:
            raise FleetError("Agent 请求路径无效", status_code=422)
        parsed = urlsplit(path)
        if (parsed.scheme or parsed.netloc or parsed.fragment or "%" in parsed.path
                or any(part in {".", ".."} for part in parsed.path.split("/"))):
            raise FleetError("Agent 请求路径无效", status_code=422)
        owner = self.registry.owner_for_path(parsed.path, method)
        if owner and not self.registry.enabled(owner):
            raise FleetError("执行插件未启用：" + owner, status_code=503)
        return super().call(node, method, path, body, **options)


def include_available_router(application, registry: PluginRegistry, router, cleanup, **options) -> None:
    """Compose existing mixed host/feature routers without removing host guards.

    This bridge is for reviewed legacy routers. New providers use the stricter
    PluginContext.mount_router contract. Router-owned synchronous disposers
    enter the same lifetime as the application, including assembly failures.
    """
    router.routes[:] = [route for route in router.routes
                        if not (owner := registry.owner_for_path(getattr(route, "path", "")))
                        or registry.enabled(owner)]
    for callback in router.on_shutdown:
        cleanup.callback(callback)
    router.on_shutdown.clear()
    application.include_router(router, **options)
