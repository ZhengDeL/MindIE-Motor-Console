"""A complete, independent overview replacement with explicit unavailability.

This reference intentionally prevents overview data from being reported during
a maintenance window. It does not manufacture empty or zero hardware metrics.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse


def create_provider(context):
    router = APIRouter()

    @router.get("/api/overview")
    def overview():
        return JSONResponse(
            {"state": "unavailable", "detail": "概览处于维护状态；模型服务与其他模块保持独立管理。",
             "provider": context.provider.id},
            status_code=503,
        )

    return router
