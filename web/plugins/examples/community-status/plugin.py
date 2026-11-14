"""Community plugin example; licensed under Apache-2.0 (see LICENSE).

Only the documented context protocol and the host's FastAPI dependency are
used. Importing this module does not start workers or connect to anything.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from fastapi import APIRouter


class PluginContext(Protocol):
    """The small public context surface consumed by this example."""
    def require_service(self, name: str) -> Any: ...
    def mount_router(self, router: APIRouter, **options: Any) -> None: ...
    def provide(self, name: str, version: int, value: Any) -> Any: ...


def setup(context: PluginContext) -> None:
    """Register a side-effect-free, read-only contribution during bootstrap."""
    public: Mapping[str, Any] = context.require_service("public_info")
    # Keep a snapshot containing only this interface's documented public keys.
    values = {key: public[key] for key in ("product_name", "version", "mode", "api_version")}
    router = APIRouter(prefix="/api/plugins/community-status", tags=["community-status"])

    @router.get("/status")
    def status() -> dict[str, Any]:
        return {
            "title": "插件状态示例",
            "summary": "这些字段来自控制台公共接口，不代表模型、服务器或 NPU 的健康状态。",
            "columns": [{"key": "name", "label": "项目"}, {"key": "value", "label": "值"}],
            "rows": [
                {"name": "平台名称", "value": str(values["product_name"])},
                {"name": "平台版本", "value": str(values["version"])},
                {"name": "控制台运行模式", "value": str(values["mode"])},
                {"name": "插件宿主 API", "value": str(values["api_version"])},
            ],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    context.mount_router(router)
    context.provide("community-status.info", 1, values)
