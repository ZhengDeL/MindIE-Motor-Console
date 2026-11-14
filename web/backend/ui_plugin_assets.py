"""Public immutable UI boot graph/assets and protected artifact management."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import copy
import hashlib
import re

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .plugin_host.packages.errors import PluginPackageError
from .ui_plugin_packages import (MAX_ARCHIVE_BYTES, UiPackageStore, UiPluginError, default_manifest, member_name)


def public_ui_plugin_read(path: str, method: str) -> bool:
    return method in {"GET", "HEAD"} and (path == "/api/ui-plugins/bootstrap" or path.startswith("/api/ui-plugins/assets/"))


def current_ui_generation(request: Request, local_generation: str | None = None):
    local = local_generation or getattr(request.app.state, "ui_generation", "")
    if not isinstance(local, str) or not re.fullmatch(r"[0-9a-f]{64}", local):
        return "", {}
    generations, metadata = [local], {}
    provider = getattr(request.app.state, "provider_generation", "")
    if isinstance(provider, str) and re.fullmatch(r"[0-9a-f]{64}", provider):
        metadata["providerGeneration"] = provider
        generations.append(provider)
    outer = request.headers.get("x-console-generation", "")
    if re.fullmatch(r"[0-9a-f]{64}", outer):
        metadata["hostGeneration"] = outer
        generations.append(outer)
    return (local if len(generations) == 1 else hashlib.sha256(":".join(generations).encode("ascii")).hexdigest()), metadata


def guard_ui_generation(request: Request):
    """Reject known-old UI writes before route effects; never replay them."""
    if request.method in {"GET", "HEAD", "OPTIONS"} or not request.url.path.startswith("/api/"):
        return
    for header, current in (("x-motor-ui-generation", current_ui_generation(request)[0]),
                            ("x-motor-expected-generation", getattr(request.app.state, "provider_generation", ""))):
        expected = request.headers.get(header, "")
        if not expected:
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise HTTPException(400, "无效的插件版本身份")
        if not current:
            raise HTTPException(503, "无法核对插件版本，暂停此操作；请检查当前装配状态")
        if expected != current:
            raise HTTPException(409, "插件版本已更新，请保留草稿并刷新页面后重新确认操作", headers={"X-Motor-Generation-Mismatch": "true"})


class UiSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0, lt=9007199254740990)
    trust_code: bool = False
    packages: dict[str, str] = Field(default_factory=dict, max_length=16)
    bindings: dict[str, str] = Field(default_factory=dict, max_length=512)


def create_ui_plugin_router(*, directory: Path, mode: str, store, role_for_request):
    packages = UiPackageStore(Path(directory), default_manifest())
    graph = packages.graph()
    router = APIRouter(prefix="/api/ui-plugins", tags=["ui-plugins"])
    router.ui_generation = graph.bootstrap["generation"]

    def admin(request: Request, *, mutate=False):
        role = role_for_request(request)
        if role not in {"admin", "demo"} or mutate and (mode != "control" or role != "admin"):
            raise HTTPException(403, "仅已登录的控制面管理员可导入或选择受信 UI 插件；演示环境只读")

    def checked(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except UiPluginError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except PluginPackageError as exc:
            raise HTTPException(503, "UI 插件存储不可用或配置无效，请使用离线恢复工具检查") from exc
        except OSError as exc:
            raise HTTPException(503, "UI 插件文件不可用，请检查安装与存储") from exc

    @router.api_route("/bootstrap", methods=["GET", "HEAD"])
    def bootstrap(request: Request):
        # Only public package contracts and selected content hashes, no config,
        # filesystem paths, private credentials or inactive artifact inventory.
        value = copy.deepcopy(graph.bootstrap)
        value["generation"], metadata = current_ui_generation(request, graph.bootstrap["generation"])
        value.update(metadata)
        return value

    @router.api_route("/assets/{digest}/{asset:path}", methods=["GET", "HEAD"])
    def asset(digest: str, asset: str, request: Request):
        checked(member_name, asset)
        data = graph.assets.get((digest, asset))
        if data is None:
            raise HTTPException(404, "当前 UI 启动图中没有此资源")
        types = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".woff2": "font/woff2"}
        suffix = PurePosixPath(asset).suffix
        etag = '"'+hashlib.sha256(data).hexdigest()+'"'
        headers = {"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        headers["Content-Length"] = str(len(data))
        return Response(content=b"" if request.method == "HEAD" else data, media_type=types[suffix], headers=headers)

    @router.get("/packages")
    def inventory(request: Request):
        admin(request)
        return checked(packages.inventory, graph.bootstrap["generation"])

    @router.post("/packages/import", status_code=201)
    async def import_package(request: Request):
        admin(request, mutate=True)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
            raise HTTPException(415, "请上传 application/octet-stream 的离线 .mcui 插件包")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_ARCHIVE_BYTES:
                raise HTTPException(413, "UI 插件包不能超过 8 MiB")
        def stage():
            store.audit("导入 UI 插件请求", "ui-plugins", "accepted")
            result = checked(packages.import_package, bytes(content))
            store.audit("导入 UI 插件", result["manifest"]["id"], "success", metadata={"digest": result["digest"]})
            return result
        return await run_in_threadpool(stage)

    @router.post("/selection")
    def select(data: UiSelection, request: Request):
        admin(request, mutate=True)
        store.audit("选择 UI 插件请求", "ui-plugins", "accepted", metadata={"expected_revision": data.expected_revision})
        result = checked(packages.select, packages=data.packages, bindings=data.bindings,
                         expected_revision=data.expected_revision, trust_code=data.trust_code)
        store.audit("选择 UI 插件", "ui-plugins", "success", "下次启动生效，当前 UI 与模型未重启", metadata={"revision": result["revision"], "desired_generation": result["desired_generation"]})
        return checked(packages.inventory, graph.bootstrap["generation"])

    return router
