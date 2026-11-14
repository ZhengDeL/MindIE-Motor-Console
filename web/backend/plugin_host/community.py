"""Authenticated community-package management and startup-only composition."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from ..version import CONSOLE_VERSION, PRODUCT_NAME
from .builtin import build_registry
from .contracts import PLUGIN_API_VERSION, PluginConfigurationError
from .packages import MAX_PACKAGE_BYTES, PackageConflict, PackageStore, PluginPackageError, manifest_schema
from .packages.manifest import declaration
from .packages.limits import MAX_CONFIGURATION_BYTES
from .sdk import status_table_schema

FEATURES = ("community-import-v1", "http-routes-v1", "status-table-v1")
_ROLES = {"viewer": {"viewer", "operator", "admin"}, "operator": {"operator", "admin"}, "admin": {"admin"}}


class CommunityContext:
    """Documented context facade; trusted Python code is not a process sandbox."""
    api_version = PLUGIN_API_VERSION

    def __init__(self, context, profile, mode):
        self._context, self._profile = context, profile
        self.plugin_id = context.plugin_id
        self._services = MappingProxyType({
            "public_info": MappingProxyType({"product_name": PRODUCT_NAME, "version": CONSOLE_VERSION,
                                            "mode": mode, "api_version": PLUGIN_API_VERSION}),
            "configuration": MappingProxyType(copy.deepcopy(profile.configuration)),
            "resources": profile.read_resource,
        })

    def require_service(self, name, expected_type=None):
        if name not in self._services:
            raise PluginConfigurationError("Host service is not part of the community SDK: " + str(name))
        result = self._services[name]
        if expected_type is not None and not isinstance(result, expected_type):
            raise PluginConfigurationError("Host service has an incompatible type: " + name)
        return result

    def mount_router(self, router, **options):
        # HTTP authorization does not apply to WebSockets. They require a
        # separate future contract, rather than an unguarded implicit fallback.
        if not isinstance(router, APIRouter) or any(not isinstance(route, APIRoute) for route in router.routes):
            raise PluginConfigurationError("Community plugins may contribute HTTP API routes only")
        if any(not route.methods or not route.methods <= {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
               for route in router.routes):
            raise PluginConfigurationError("Community plugin uses an unsupported HTTP method")
        self._context.mount_router(router, **options)

    def provide(self, name, version, value):
        return self._context.provide(name, version, value)

    def consume(self, name, version):
        return self._context.consume(name, version)

    def on_shutdown(self, callback, /, *args, **kwargs):
        self._context.on_shutdown(callback, *args, **kwargs)


class SelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=64)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_revision: int = Field(ge=0)
    trust_code: bool
    configuration: dict = Field(default_factory=dict, repr=False)


class DeselectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=0)


class CommunityRuntime:
    def __init__(self, data_directory, *, mode, storage_backend):
        self.mode, self.storage_backend = mode, storage_backend
        configured = os.getenv("MOTOR_PLUGIN_DIR", "")
        self.directory = Path(configured) if configured else Path(data_directory).resolve() / "plugin-packages"
        if not self.directory.is_absolute():
            raise PluginConfigurationError("MOTOR_PLUGIN_DIR must be absolute")
        self.store = PackageStore(self.directory, recover=mode == "control") if mode != "agent" else None
        # The unauthenticated demo and privileged Agent are never execution
        # owners for uploaded control-plane code.
        self.profiles = self.store.active_profiles() if mode == "control" else ()
        self.by_id = {profile.manifest["id"]: profile for profile in self.profiles}
        self.loaded = {}

    def declarations(self):
        return tuple(profile.declaration() for profile in self.profiles)

    def roles_for(self, owner, *, write=False):
        profile = self.by_id.get(owner)
        if profile is None:
            return None
        roles = _ROLES[profile.manifest["access"]]
        return roles & {"admin", "operator"} if write else roles

    def mount(self, registry, context, cleanup):
        for identifier in registry.order:
            profile = self.by_id.get(identifier)
            if profile is None or not registry.enabled(identifier):
                continue
            factory = profile.load_factory()
            registry.mount(identifier, lambda scoped, p=profile, f=factory: f(CommunityContext(scoped, p, self.mode)),
                           context, cleanup)
            self.loaded[identifier] = {"digest": profile.digest, "configuration_digest": profile.configuration_digest}

    def catalog(self, registry):
        result = registry.describe()
        result.pop("digest", None)
        result["features"] = list(FEATURES)
        result["navigation"] = [{"plugin_id": identifier, **item,
                                 "description": item.get("description", ""), "group": item.get("group", "插件")}
                                for identifier, profile in self.by_id.items() if identifier in self.loaded
                                for item in profile.manifest.get("navigation", [])]
        raw = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        return {**result, "digest": hashlib.sha256(raw).hexdigest()}

    def inventory(self, registry):
        if self.store is None:
            raise HTTPException(404, "执行 Agent 不提供控制台插件包管理")
        state = self.store.list()
        selected = {item["id"]: item for item in state["selected"]}
        declared = {item["id"] for item in registry.describe()["items"]}
        items = []
        for package in state["packages"]:
            manifest, digest = package["manifest"], package["digest"]
            identifier = manifest["id"]
            chosen = selected.get(identifier, {}).get("digest") == digest
            blocked = chosen and identifier in declared and not registry.enabled(identifier)
            compatible = package.get("compatible_with_host", True)
            reason = "宿主启动配置停用了此插件" if blocked else "" if compatible else "与当前宿主版本不兼容；保留用于版本回退"
            items.append({**manifest, "digest": digest, "selected": chosen, "compatible_with_host": compatible,
                          "loaded": self.loaded.get(identifier, {}).get("digest") == digest,
                          "blocked_reason": reason})
        desired = {identifier: {"digest": item["digest"], "configuration_digest": item["configuration_digest"]}
                   for identifier, item in selected.items()
                   if identifier not in declared or registry.enabled(identifier)}
        return {"schemaVersion": 1, "revision": state["revision"], "items": items,
                "pending_restart": desired != self.loaded, "management_enabled": self.mode == "control",
                "import_executes_code": False, "activation": "next-start", "maximum_package_bytes": MAX_PACKAGE_BYTES,
                "maximum_configuration_bytes": MAX_CONFIGURATION_BYTES}

    def _validate_selection(self, identifier, digest, expected_revision):
        state = self.store.list()
        if state["revision"] != expected_revision:
            raise PackageConflict("Plugin selection changed; reload before saving")
        candidates = {p.manifest["id"]: p.manifest for p in self.store.active_profiles()}
        if digest is None:
            candidates.pop(identifier, None)
        else:
            manifest = self.store.inspect(digest)["manifest"]
            if manifest["id"] != identifier:
                raise PluginPackageError("Selected plugin ID does not match the artifact")
            candidates[identifier] = manifest
        # CAS in PackageStore detects concurrent changes after this review.
        # Check host disable selections as well as the package dependency graph.
        build_registry(storage_backend=self.storage_backend,
                       extra_manifests=[declaration(item) for item in candidates.values()])


def create_package_router(runtime, registry, store, mutation_lock, role_for_request):
    router = APIRouter(prefix="/api/plugin-packages", tags=["community-plugin-management"])

    def admin(request, *, mutate=False):
        role = role_for_request(request)
        if role not in {"admin", "demo"}:
            raise HTTPException(403, "仅系统管理员可管理插件包")
        if mutate and (runtime.mode != "control" or role != "admin"):
            raise HTTPException(403, "演示环境只读；请在带登录保护的控制面导入并选择插件")

    @router.get("")
    def packages(request: Request):
        admin(request)
        return runtime.inventory(registry)

    @router.post("/import", status_code=201)
    async def import_package(request: Request):
        admin(request, mutate=True)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
            raise HTTPException(415, "请上传 application/octet-stream 离线插件包")
        raw = await request.body()
        if len(raw) > MAX_PACKAGE_BYTES:
            raise HTTPException(413, "插件包不能超过 8 MiB")
        # Filesystem validation and hashing must not block the event loop.
        from starlette.concurrency import run_in_threadpool
        def stage():
            with mutation_lock:
                digest = hashlib.sha256(raw).hexdigest()
                store.audit("导入插件包请求", digest, "accepted", "仅校验与暂存；不执行第三方代码")
                try:
                    result = runtime.store.import_bytes(raw)
                except PluginPackageError as exc:
                    store.audit("导入插件包", digest, "failed", metadata={"error_code": exc.code})
                    raise
                store.audit("导入插件包", result["manifest"]["id"], "success", metadata={"digest": result["digest"]})
                return {"digest": result["digest"], "manifest": result["manifest"], "import_executes_code": False}
        return await run_in_threadpool(stage)

    @router.post("/select")
    def select_package(data: SelectionInput, request: Request):
        admin(request, mutate=True)
        with mutation_lock:
            runtime._validate_selection(data.id, data.digest, data.expected_revision)
            store.audit("选择插件包请求", data.id, "accepted", metadata={"digest": data.digest})
            try:
                runtime.store.activate(data.id, data.digest, expected_revision=data.expected_revision,
                                       trust_code=data.trust_code, configuration=data.configuration)
            except PluginPackageError as exc:
                store.audit("选择插件包", data.id, "failed", metadata={"digest": data.digest, "error_code": exc.code})
                raise
            store.audit("选择插件包", data.id, "success", "下次启动生效；当前进程与模型未重启",
                        metadata={"digest": data.digest})
            return runtime.inventory(registry)

    @router.post("/deselect")
    def deselect_package(data: DeselectionInput, request: Request):
        admin(request, mutate=True)
        with mutation_lock:
            runtime._validate_selection(data.id, None, data.expected_revision)
            store.audit("取消插件选择请求", data.id, "accepted")
            try:
                runtime.store.deactivate(data.id, expected_revision=data.expected_revision)
            except PluginPackageError as exc:
                store.audit("取消插件选择", data.id, "failed", metadata={"error_code": exc.code})
                raise
            store.audit("取消插件选择", data.id, "success", "下次启动生效；当前进程与模型未重启")
            return runtime.inventory(registry)

    return router


def sdk_description():
    return {"schemaVersion": 1, "api_version": PLUGIN_API_VERSION, "features": list(FEATURES),
            "package_manifest_schema": manifest_schema(), "activation": "next-start", "trusted_code_only": True,
            "page_response_schema": status_table_schema(), "page_response_limit_bytes": 1024 * 1024,
            "maximum_configuration_bytes": MAX_CONFIGURATION_BYTES,
            "http_authentication": "host-session-rbac-csrf", "frontend_renderer": "status-table-v1",
            "host_services": ["public_info", "configuration", "resources"],
            "unsupported": ["agent-code-import", "anonymous-demo-code-import", "remote-javascript", "websockets", "hot-unload"]}
