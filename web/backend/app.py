"""Containerized Motor control plane; Docker and Kubernetes execution stay on agents."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager, ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
import contextvars
import re
import sqlite3
import sys
import traceback
from uuid import uuid4
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import demo
from .catalog import MOTOR_COMMIT, capabilities, summarize, templates
from .fleet import FleetError, Secrets, endpoint, motor_request, observations, ready_value
from .store import Store, now, uid
from .auth import AuthManager, AuthSettings, create_auth_router
from .compatibility import CompatibilityError, check_binding, public_profile
from .maintenance import MaintenanceError, ProcessLease
from .observability import JsonLogStore, context_fields, redact, register_secret, request_context
from .settings import documentation_url, load_secret_files, validate_production_settings, sqlite_wal_fix_available
from .system import SystemManager, create_system_router
from .version import (AGENT_PROTOCOL_VERSION, BASELINE_PROFILE_ID, CONSOLE_VERSION, PRODUCT_NAME, PRODUCT_ID,
                      AGENT_PROTOCOL_HEADER, CONSOLE_PROTOCOL_HEADER, PRODUCT_HEADER)
from .http_middleware import RequestBodyLimit
from .template_library import TEMPLATE_FORMAT
from .integrations import image_catalog, discover_models, project_models, MODEL_RESPONSE_BYTES
from .node_configuration import execution_node
from .node_inventory_sampling import NodeInventorySampler
from .resource_inventory import inventory_view
from .service_health import DockerServiceObserver
from .service_metrics import ServiceMetricsSampler, metrics_revision
from .inference_results import inference_output
from .agent_credentials import (AgentCredentialError, agent_credential_metadata,
                                validate_agent_credentials)
from .agent_security import agent_tls_context
from .platform_configuration import PlatformConfiguration
from .ports.storage import RevisionConflict, StorageUnavailable
from .extensions import create_extension_router
from .branding import branding_icon_read, public_branding_read
from .deployment_view import configuration_digest
from .json_documents import validate_json_document
from .log_records import ServiceLogReader, fair_tail, role_matches, source_key, validate_log_role
from .plugin_host import build_registry, PluginContext
from .plugin_host.providers import default_branding, mount_branding
from .plugin_host.console_providers import mount_nodes, mount_services, mount_gateways, mount_images, mount_templates
from .plugin_execution import PluginFleet, include_available_router, require_plugin, require_runtime
from .plugin_host.community import CommunityRuntime, create_package_router, sdk_description
from .plugin_host.packages import MAX_PACKAGE_BYTES, PluginPackageError
from .plugin_host import PluginConfigurationError


class ServiceInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    runtime: Literal["docker", "kubernetes"] = "docker"
    user_config: dict[str, Any]
    env: dict[str, Any]
    placements: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    extra_manifests: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    motor_profile: str = Field(default="", max_length=96)
    expected_revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str):
        if any(ord(c) < 32 for c in value): raise ValueError("名称不能含控制字符")
        if not value.strip(): raise ValueError("名称不能为空")
        return value.strip()

    @model_validator(mode="after")
    def finite_configuration(self):
        validate_json_document({"user_config": self.user_config, "env": self.env,
                                "placements": self.placements, "extra_manifests": self.extra_manifests})
        return self


class InferenceInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=100000)
    max_tokens: int = Field(default=128, ge=1, le=32768)


class ImageInspectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    node_id: str = Field(min_length=1, max_length=100)
    image: str = Field(min_length=1, max_length=256)
    probe_runtime: bool = False

    @field_validator("image")
    @classmethod
    def literal_image(cls, value: str) -> str:
        from .image_contract import validate_image_reference
        return validate_image_reference(value)


class DeploymentApproval(BaseModel):
    extra_manifests_approved_sha256: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")
    reviewed_configuration_digest: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")


class ScaleInput(BaseModel):
    counts: dict[str, Any] = Field(min_length=1, max_length=3)

    @field_validator("counts")
    @classmethod
    def strict_counts(cls, value: dict):
        allowed = {"p_instances_num", "d_instances_num", "hybrid_instances_num"}
        if set(value) - allowed or any(type(number) is not int or not 1 <= number <= 16 for number in value.values()):
            raise ValueError("仅允许 p/d/hybrid_instances_num，且实例数必须为 1..16 的整数")
        return value


class UndeployInput(BaseModel):
    confirmation_name: str = Field(min_length=1, max_length=100)


def extra_manifest_digest(service: dict) -> str | None:
    documents = service.get("extra_manifests") or []
    return hashlib.sha256(json.dumps(documents, sort_keys=True).encode()).hexdigest() if documents else None


def create_app(*, mode: str | None = None, data_dir: str | Path | None = None,
               upstream_dir: str | Path | None = None, console_token: str | None = None) -> FastAPI:
    # Constructor failures must release both the process lease and any opened
    # plugin. On success ownership moves to the application's lifespan.
    with ExitStack() as startup:
        result = _assemble_app(mode=mode, data_dir=data_dir, upstream_dir=upstream_dir,
                               console_token=console_token, startup=startup)
        result.state.resource_lifetime = startup.pop_all()
        return result


def _assemble_app(*, mode: str | None, data_dir: str | Path | None,
                  upstream_dir: str | Path | None, console_token: str | None, startup: ExitStack) -> FastAPI:
    load_secret_files()
    selected_mode = mode or os.getenv("MOTOR_MODE", "demo")
    if selected_mode not in {"demo", "control", "agent"}: raise ValueError("MOTOR_MODE must be demo, control or agent")
    token = console_token if console_token is not None else os.getenv("MOTOR_CONSOLE_TOKEN", "")
    if selected_mode == "agent":
        validate_agent_credentials()
        from .agent_capacity import native_capacity
        native_capacity()
    if selected_mode == "control": agent_tls_context()
    validate_production_settings(selected_mode)
    upstream = Path(upstream_dir or os.getenv("MOTOR_UPSTREAM_DIR", Path(__file__).resolve().parents[2] / "mindie-motor/upstream"))
    directory = Path(data_dir or os.getenv("MOTOR_DATA_DIR", Path(__file__).resolve().parents[1] / ".data"))
    configuration = PlatformConfiguration.from_env(directory)
    community = CommunityRuntime(directory, mode=selected_mode, storage_backend=configuration.database_backend)
    plugins = build_registry(storage_backend=configuration.database_backend, extra_manifests=community.declarations())
    plugin_cleanup = ExitStack()
    lease = ProcessLease(directory, name=selected_mode).acquire() if selected_mode in {"control", "agent"} else None
    if lease: startup.callback(lease.release)
    try:
        store = Store(directory, backend=configuration.database_backend, database_url=configuration.database_url)
        startup.callback(store.close)
        logs = JsonLogStore(configuration.logs, max_bytes=configuration.log_segment_bytes,
                            backup_count=configuration.log_rotations, service_name=PRODUCT_ID)
        startup.callback(logs.close)
        auth = AuthManager(store, settings=AuthSettings.from_env(), legacy_token=token) if selected_mode == "control" else None
        if auth and os.getenv("MOTOR_ENV") == "production" and auth.setup_required() and not auth.settings.bootstrap_token:
            raise RuntimeError("First administrator setup requires an independently generated MOTOR_BOOTSTRAP_TOKEN_FILE")
    except BaseException:
        if lease: lease.release()
        raise
    seed = os.getenv("MOTOR_SECRET_KEY") or token
    if not seed:
        key_file = directory / ".secret-key"
        if not key_file.exists():
            try:
                with key_file.open("x", encoding="utf-8") as output: output.write(secrets.token_urlsafe(40))
            except FileExistsError:
                pass
            try: key_file.chmod(0o600)
            except OSError: pass
        seed = key_file.read_text(encoding="utf-8")
    fleet = PluginFleet(Secrets(seed), plugins)
    service_observer = DockerServiceObserver(fleet, upstream_dir=upstream)
    for secret in (seed, token, os.getenv("MOTOR_BOOTSTRAP_TOKEN", ""), os.getenv("MOTOR_AGENT_TOKEN", "")): register_secret(secret)
    executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="motor-console-job")
    startup.callback(executor.shutdown, wait=True, cancel_futures=True)
    mutation_lock = threading.RLock()
    service_metrics_sampler = ServiceMetricsSampler()
    service_log_reader = ServiceLogReader()
    startup.callback(service_log_reader.close)
    inventory_sampler = NodeInventorySampler(store, fleet, mutation_lock, mode=selected_mode)
    startup.callback(inventory_sampler.close)
    startup.callback(service_metrics_sampler.close)
    startup.callback(service_observer.close)
    rollout_manager = None
    template_list = templates(upstream)
    if selected_mode == "demo": demo.seed(store, template_list)
    for old in store.list("jobs"):
        if old["status"] in {"queued", "running"}:
            old.update(status="interrupted", error="控制面进程重启；节点任务及容器需重新探测，未自动重复执行")
            store.put("jobs", old)
            service = store.get("services", old.get("service_id", ""))
            if service:
                service.update(status="error", last_error=old["error"], updated_at=now())
                if old.get("host_actions_attempted"):
                    service["resources_may_exist"] = True
                    previous = service.get("deployed_placements", [])
                    service["deployed_placements"] = list({json.dumps(p, sort_keys=True): p for p in [*previous, *service["placements"]]}.values())
                store.put("services", service)
                store.audit("任务中断", service["name"], "interrupted", "保留可能存在的资源归属；不会自动重复部署或停止")

    @asynccontextmanager
    async def lifespan(_app):
        try:
            if plugins.enabled("nodes"):
                inventory_sampler.start()
            yield
        finally:
            # ExitStack attempts every disposer even if one plugin fails to
            # close. The same ownership covers failed assembly and shutdown.
            _app.state.resource_lifetime.close()

    application = FastAPI(title=PRODUCT_NAME, version=CONSOLE_VERSION, lifespan=lifespan,
                          docs_url="/api/docs" if selected_mode == "demo" else None,
                          redoc_url=None, openapi_url="/api/openapi.json" if selected_mode == "demo" else None)
    application.state.store, application.state.fleet = store, fleet
    application.state.mode, application.state.upstream = selected_mode, upstream
    application.state.inventory_sampler = inventory_sampler
    application.state.service_metrics_sampler = service_metrics_sampler
    application.state.service_log_reader = service_log_reader
    application.state.auth, application.state.logs, application.state.lease = auth, logs, lease
    application.state.log_health = True
    application.state.configuration = configuration
    application.state.plugins = plugins
    application.state.community_plugins = community
    startup.callback(plugin_cleanup.close)

    def authorized(request: Request) -> bool:
        return selected_mode == "demo" or auth is not None and auth.authenticate(request, required=False) is not None

    def require_auth(request: Request):
        if not authorized(request): raise HTTPException(401, "请先登录控制台")

    secured = [Depends(require_auth)]

    def role_for_request(request: Request):
        if selected_mode == "demo": return "demo"
        principal = getattr(request.state, "principal", None)
        return principal.role if principal else None

    plugin_context = PluginContext(application=application, services={
        "store": store, "mutation_lock": mutation_lock, "fleet": fleet, "mode": selected_mode,
        "upstream": upstream, "secured": secured, "role_for_request": role_for_request,
        "inventory_sampler": inventory_sampler, "template_list": template_list,
    }, registry=plugins, cleanup=plugin_cleanup)
    branding_manager = plugins.mount("branding", mount_branding, plugin_context, plugin_cleanup) if selected_mode != "agent" else None
    application.state.branding = branding_manager

    def service_view(value: dict, request: Request, *, nodes: dict | None = None):
        lookup = nodes if nodes is not None else node_map()
        identifiers = list(dict.fromkeys(p["node_id"] for p in value.get("placements", []) if p.get("node_id")))
        value = {**value, "config_revision": value.get("config_revision", 1),
                 "node_names": [lookup.get(identifier, {}).get("name") or identifier for identifier in identifiers]}
        # Public addresses are projections, not trusted persisted observations.
        # Existing Kubernetes records may contain AgentIP:PodPort from an older
        # release; recompute against the immutable binding instead of echoing it.
        try: value["endpoint"] = endpoint(value, lookup, deployed=bool(value.get("resources_may_exist")))
        except (FleetError, ValueError, KeyError, TypeError): value["endpoint"] = ""
        if role_for_request(request) != "viewer": return value
        return {**value, "user_config": {}, "env": {}, "deployed_user_config": {}, "deployed_env": {},
                "extra_manifests": [], "config_redacted": True,
                "last_error": redact(value.get("last_error", ""))}

    body_limits = {"/api/plugin-packages/import": MAX_PACKAGE_BYTES}
    if selected_mode != "agent":
        # Authorize control-plane requests before buffering uploaded code. The
        # Agent still needs its byte limit outside its JSON protocol checks.
        application.add_middleware(RequestBodyLimit, logs=logs, path_limits=body_limits)

    @application.middleware("http")
    async def bounds(request: Request, call_next):
        started = time.monotonic()
        raw_id = request.headers.get("X-Request-ID", "")
        request_id = raw_id if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", raw_id) else uuid4().hex
        try: length = int(request.headers.get("content-length", "0"))
        except ValueError: return JSONResponse({"detail": "无效 Content-Length"}, status_code=400)
        maximum = body_limits.get(request.url.path, 5 * 1024 * 1024)
        if length < 0: return JSONResponse({"detail": "无效 Content-Length"}, status_code=400)
        if length > maximum: return JSONResponse({"detail": "请求体超过此接口限制"}, status_code=413)
        with request_context(actor="anonymous", request_id=request_id):
            try:
                path, method = request.url.path.rstrip("/"), request.method
                auth_path = path == "/api/auth" or path.startswith("/api/auth/")
                auth_write = method not in {"GET", "HEAD", "OPTIONS"} or method == "GET" and path in {
                    "/api/auth/oidc/login", "/api/auth/oidc/callback"}
                # Block before authentication, flow consumption or IdP traffic:
                # a later backup restore must not undo a successful credential,
                # permission or session-revocation change during maintenance.
                if selected_mode == "control" and auth_path and auth_write and (directory / "maintenance.json").exists():
                    raise HTTPException(503, "平台正在维护，请稍后重试账号操作")
                if (selected_mode == "control" and request.url.path.startswith("/api/")
                        and not request.url.path.startswith("/api/auth/") and request.url.path != "/api/meta"
                        and not public_branding_read(request.url.path, request.method)):
                    path, method = request.url.path, request.method
                    roles = {"admin", "operator", "viewer"}
                    if method not in {"GET", "HEAD", "OPTIONS"}: roles = {"admin", "operator"}
                    if path.startswith(("/api/system/", "/api/audit", "/api/agent-rollouts")) or path.startswith("/api/nodes") and method not in {"GET", "HEAD", "OPTIONS"}:
                        roles = {"admin"}
                    if path.startswith("/api/gateways") and method not in {"GET", "HEAD", "OPTIONS"} and not path.endswith("/probe"):
                        roles = {"admin"}
                    if (path.startswith("/api/extensions") and method not in {"GET", "HEAD", "OPTIONS"}
                            or path.startswith("/api/extensions/grafana/provisions/")):
                        roles = {"admin"}
                    if path == "/api/branding" and method not in {"GET", "HEAD", "OPTIONS"}:
                        roles = {"admin"}
                    if path == "/api/plugin-packages" or path.startswith("/api/plugin-packages/"):
                        roles = {"admin"}
                    external_roles = community.roles_for(plugins.owner_for_path(path),
                        write=method not in {"GET", "HEAD", "OPTIONS"})
                    if external_roles is not None:
                        roles = external_roles
                    if path.startswith("/api/services/") and path.endswith(("/export", "/revisions")): roles = {"admin", "operator"}
                    principal = auth.authorize(request, roles)
                    if method not in {"GET", "HEAD", "OPTIONS"} and (directory / "maintenance.json").exists():
                        raise HTTPException(503, "平台正在维护，暂停新的配置和部署操作")
                    if method not in {"GET", "HEAD", "OPTIONS"} and not application.state.log_health:
                        raise HTTPException(503, "平台日志存储不可用，暂停写操作，请由运维恢复存储")
                else:
                    principal = auth.authenticate(request, required=False) if auth else None
                owner = plugins.owner_for_path(request.url.path, request.method)
                if owner:
                    require_plugin(plugins, owner)
                # The nodes page owns this API, but actual cluster reads also
                # require the Kubernetes provider, before contacting any Agent.
                if re.fullmatch(r"/api/nodes/[^/]+/kubernetes/nodes/?", request.url.path):
                    require_runtime(plugins, "kubernetes")
                match = re.fullmatch(r"/api/services/([^/]+)(?:/(.*))?/?", request.url.path)
                if match:
                    service = store.get("services", match.group(1))
                    if service and (request.method not in {"GET", "HEAD", "OPTIONS"}
                                    or match.group(2) in {"logs", "metrics", "observations", "aigw-models"}):
                        require_runtime(plugins, service["runtime"])
                if selected_mode == "agent" and request.url.path.startswith("/agent/") and request.method == "POST":
                    body = await request.json()
                    if not isinstance(body, dict): raise HTTPException(422, "Agent 操作请求必须是 JSON 对象")
                    if request.headers.get(PRODUCT_HEADER) != PRODUCT_ID:
                        raise HTTPException(409, "Agent write requires a matching MindIE-Motor Console product identity")
                    if request.url.path in {"/agent/images/pull", "/agent/images/remove"}:
                        if request.headers.get(CONSOLE_PROTOCOL_HEADER) != str(AGENT_PROTOCOL_VERSION):
                            raise HTTPException(409, "镜像管理协议不兼容")
                    else:
                        check_binding(body.get("motor_profile"), request.headers.get(CONSOLE_PROTOCOL_HEADER))
                with request_context(actor=principal.user_id if principal else "demo" if selected_mode == "demo" else "anonymous", request_id=request_id):
                    response = await call_next(request)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            except CompatibilityError as exc:
                response = JSONResponse({"detail": str(exc)}, status_code=409)
            except Exception as exc:
                frames = [{"file": frame.filename, "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(exc.__traceback__)[-12:]]
                try: logs.write("http.unhandled_error", severity="ERROR", path=request.url.path, method=request.method, error_type=type(exc).__name__, frames=frames)
                except (OSError, RuntimeError, sqlite3.Error): application.state.log_health = False
                response = JSONResponse({"detail": "服务暂时无法处理请求，请凭请求编号联系管理员", "request_id": request_id}, status_code=500)
            actor = getattr(request.state, "principal", None)
            try:
                logs.write("http.request", actor=actor.user_id if actor else "demo" if selected_mode == "demo" else "anonymous",
                           path=request.url.path, method=request.method, status_code=response.status_code, duration_ms=round((time.monotonic() - started) * 1000, 2))
            except (OSError, RuntimeError, sqlite3.Error):
                application.state.log_health = False
                print(json.dumps({"event": "request_log_unavailable", "request_id": request_id}), file=sys.stderr)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Request-ID"] = request_id
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        response.headers["X-Frame-Options"] = "DENY"
        if os.getenv("MOTOR_ENV") == "production": response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers[PRODUCT_HEADER] = PRODUCT_ID
        if selected_mode == "agent": response.headers[AGENT_PROTOCOL_HEADER] = str(AGENT_PROTOCOL_VERSION)
        if request.url.path.startswith("/api/") and not (branding_icon_read(request.url.path, request.method)
                and response.status_code in {200, 304}):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(FleetError)
    async def fleet_failure(_request, error):
        return JSONResponse({"detail": redact(str(error))}, status_code=502)

    @application.exception_handler(RevisionConflict)
    async def revision_failure(_request, _error):
        return JSONResponse({"detail": "配置已被其他操作修改，请读取最新修订并对比后重试。当前编辑内容仍可保留。",
                             "code": "revision_conflict"}, status_code=409)

    @application.exception_handler(StorageUnavailable)
    async def storage_failure(_request, _error):
        return JSONResponse({"detail": "持久存储暂不可用，未接受新的配置或部署操作", "code": "storage_unavailable"}, status_code=503)

    @application.exception_handler(RequestValidationError)
    async def safe_validation(_request, error):
        return JSONResponse({"detail": [{key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()]}, status_code=422)

    @application.exception_handler(MaintenanceError)
    async def maintenance_failure(_request, error):
        return JSONResponse({"detail": redact(str(error))}, status_code=409)

    @application.exception_handler(PluginPackageError)
    async def package_failure(_request, error):
        return JSONResponse({"detail": str(error), "code": error.code}, status_code=error.status_code)

    @application.exception_handler(PluginConfigurationError)
    async def plugin_configuration_failure(_request, error):
        return JSONResponse({"detail": str(error), "code": "invalid_plugin_composition"}, status_code=422)

    if selected_mode == "agent":
        application.add_middleware(RequestBodyLimit, logs=logs, path_limits=body_limits)

    @application.get("/health")
    def health(): return {"status": "ok", "mode": selected_mode, "version": CONSOLE_VERSION, "product_id": PRODUCT_ID}

    @application.get("/ready")
    def ready():
        valid = application.state.log_health and not (directory / "maintenance.json").exists()
        if selected_mode == "agent":
            try:
                policy = agent_credential_metadata()
                valid = valid and any(entry and entry.get("active") for entry in (policy.get("current"), policy.get("previous")))
            except (AgentCredentialError, HTTPException):
                valid = False
        try:
            valid = valid and bool(store.health().get("healthy"))
        except Exception: valid = False
        return JSONResponse({"status": "ready" if valid else "not_ready", "mode": selected_mode, "product_id": PRODUCT_ID}, status_code=200 if valid else 503)

    @application.get("/api/meta")
    def meta(request: Request):
        return {"name": PRODUCT_NAME, "product_id": PRODUCT_ID, "version": CONSOLE_VERSION, "mode": selected_mode,
                "branding": (branding_manager.public() if branding_manager else default_branding()) if selected_mode != "agent" else None,
                "documentation_url": documentation_url(),
                "template_format": TEMPLATE_FORMAT, "motor_commit": public_profile()["commit"],
                "motor_package_version": "3.1.0" if public_profile()["bundled"] else None,
                "documented_release": "3.2.0" if public_profile()["bundled"] else None, "authenticated": authorized(request),
                "capabilities": capabilities(upstream) if plugins.enabled("features") else [],
                "runtimes": plugins.enabled_runtimes(), "agent_protocol": AGENT_PROTOCOL_VERSION, "motor_profile": public_profile()}

    @application.get("/api/plugins", dependencies=secured)
    def plugin_catalog():
        return community.catalog(plugins)

    @application.get("/api/plugin-sdk", dependencies=secured)
    def plugin_sdk():
        return sdk_description()

    if selected_mode == "agent":
        from .node_agent import create_agent_router
        from .kubernetes_runtime import create_kubernetes_router
        include_available_router(application, plugins, create_agent_router(
            docker_enabled=plugins.enabled("motor-docker"), kubernetes_enabled=plugins.enabled("motor-kubernetes")), plugin_cleanup)
        if plugins.enabled("motor-kubernetes"):
            include_available_router(application, plugins, create_kubernetes_router(), plugin_cleanup)
        plugins.seal()
        return application

    if auth:
        include_available_router(application, plugins, create_auth_router(auth), plugin_cleanup)
        from .oidc import create_oidc_router
        application.include_router(create_oidc_router(auth))
    else:
        @application.get("/api/auth/status")
        def demo_identity(): return {"setup_required": False, "authenticated": True, "user": None, "csrf_token": None,
                                     "expires_at": None, "permissions": [], "sso": {"enabled": False}, "demo": True}
    system_manager = SystemManager(store, logs, mode=selected_mode, configuration=configuration, secret_seed=seed)
    application.state.system = system_manager
    include_available_router(application, plugins, create_system_router(system_manager), plugin_cleanup)
    application.include_router(create_package_router(community, plugins, store, mutation_lock, role_for_request),
                               dependencies=secured)
    rollout_manager = plugins.mount("nodes", mount_nodes, plugin_context, plugin_cleanup)
    application.state.agent_rollouts = rollout_manager
    plugins.mount("services", mount_services, plugin_context, plugin_cleanup)
    plugins.mount("gateways", mount_gateways, plugin_context, plugin_cleanup)
    include_available_router(application, plugins, create_extension_router(store, fleet.secrets, mutation_lock, upstream,
                             enabled=plugins.enabled), plugin_cleanup, dependencies=secured)
    plugins.mount("images", mount_images, plugin_context, plugin_cleanup)

    def service_or_404(identifier: str) -> dict:
        result = store.get("services", identifier)
        if result is None: raise HTTPException(404, "服务不存在")
        return result

    def node_or_404(identifier: str) -> dict:
        result = store.get("nodes", identifier)
        if result is None: raise HTTPException(404, "节点不存在")
        return result

    def node_map(): return {value["id"]: execution_node(value) for value in store.list("nodes")}

    def reconcile_stopping(service: dict) -> dict:
        if (selected_mode != "demo" and service["runtime"] == "docker" and service["status"] == "removing"
                and service.get("deletion_requested_at") and service.get("deletion_operation_id")):
            placements = execution_placements(service, "remove")
            if not placements: return service
            seen = set()
            try:
                for placement in placements:
                    identifier = placement["node_id"]
                    if identifier in seen: continue
                    seen.add(identifier)
                    node = store.get("nodes", identifier)
                    if node is None: return service
                    operation = service["deletion_operation_id"] + "-" + identifier
                    receipt = fleet.call(execution_node(node), "GET", "/agent/operations/" + operation, timeout=5)
                    if (receipt.get("operation_id") != operation or receipt.get("action") != "remove"
                            or receipt.get("service_id") != service["id"] or receipt.get("node_id") != identifier
                            or receipt.get("operation_status") != "completed" or receipt.get("status") != "removed"
                            or receipt.get("resources_may_exist") is not False):
                        return service
            except FleetError:
                return service
            with mutation_lock:
                latest = store.get("services", service["id"])
                if (latest and latest.get("status") == "removing"
                        and latest.get("deletion_operation_id") == service["deletion_operation_id"]
                        and latest.get("updated_at") == service.get("updated_at")):
                    tombstone = {**latest, "deleted_at": now(), "resources_may_exist": False, "removal_converged": True}
                    store.mutate_many_audited([("deleted_services", tombstone)], deletes=[("services", latest["id"])],
                        action="删除服务完成", target=latest["name"], detail="所有执行节点已返回匹配的删除完成回执；未重放删除操作")
                    return {**latest, "deleted": True, "status": "removed"}
            return service
        if selected_mode == "demo" or service["runtime"] != "kubernetes" or service["status"] not in {"stopping", "removing"}: return service
        candidates = execution_placements(service, "stop")
        if not candidates: return service
        node = store.get("nodes", candidates[0]["node_id"])
        if not node: return service
        try:
            state = fleet.call(node, "GET", "/agent/kubernetes/status?service_id=" + service["id"], timeout=5)
            removed = (service["status"] == "removing" and state.get("status") == "removed"
                       and state.get("removal_converged") is True and not state.get("pending_deletions"))
            if service["status"] == "stopping" and state.get("status") == "stopped" or removed:
                with mutation_lock:
                    latest = store.get("services", service["id"])
                    if latest and latest["status"] == service["status"] and latest["updated_at"] == service["updated_at"]:
                        if removed and latest.get("deletion_requested_at"):
                            store.mutate_many_audited([("deleted_services", {**latest, "deleted_at": now(),
                                                        "removal_converged": True})],
                                deletes=[("services", latest["id"])], action="删除服务完成", target=latest["name"],
                                detail="Agent 已确认受管 UID、资源和 Pod 全部删除，归档服务记录")
                            return {**latest, "deleted": True, "status": "removed"}
                        latest.update(status="draft" if removed else "stopped", updated_at=now())
                        if removed: latest.update(resources_may_exist=False, deployed_placements=[], needs_rebuild=False, has_unapplied_changes=False, deployed_digest=None)
                        store.put("services", latest)
                        return latest
        except FleetError: pass
        return service

    def execution_placements(service: dict, action: str):
        placements = service.get("deployed_placements", service["placements"]) if action in {"stop", "restart", "remove", "undeploy"} else service["placements"]
        return placements[:1] if service["runtime"] == "kubernetes" else placements

    def check_not_active(service: dict):
        if service["status"] in {"running", "deploying", "starting", "stopping", "partial", "restarting", "scaling", "removing"}:
            raise HTTPException(409, "请先停止服务，避免修改正在使用的配置或删除运行记录")
        if service["status"] == "error" and service.get("resources_may_exist"):
            raise HTTPException(409, "失败任务可能已创建部分资源，请先停止并核对资源")

    def render_service(data: ServiceInput, previous: dict | None = None):
        require_runtime(plugins, data.runtime)
        values = data.model_dump()
        values.pop("expected_revision", None)
        if values["runtime"] != "kubernetes" and values["extra_manifests"]:
            raise HTTPException(422, "附加 Kubernetes 清单只能用于 Kubernetes 服务")
        config = values["user_config"]
        if not isinstance(config.get("motor_deploy_config"), dict): raise HTTPException(422, "配置缺少 motor_deploy_config 对象")
        try: summary = summarize(config)
        except (ValueError, TypeError) as error: raise HTTPException(422, "实例数量格式无效") from error
        nodes = node_map()
        profiles = {nodes[p["node_id"]].get("agent_metadata", {}).get("motor_profile", {}).get("profile_id") for p in values["placements"] if p.get("node_id") in nodes}
        profiles.discard(None)
        if len(profiles) > 1: raise HTTPException(422, "同一服务的执行节点必须使用一致的 Motor 适配配置")
        values["motor_profile"] = values["motor_profile"] or (next(iter(profiles)) if profiles else public_profile()["profile_id"])
        if profiles and values["motor_profile"] not in profiles: raise HTTPException(409, "服务绑定的 Motor 适配配置与所选执行节点不一致")
        if values["runtime"] == "kubernetes" and len(values["placements"]) != 1:
            raise HTTPException(422, "Kubernetes 服务必须选择且仅选择一个执行 Agent；Pod 布局由原生 Motor 调度配置决定")
        for placement in values["placements"]:
            if placement.get("node_id") not in nodes: raise HTTPException(422, "部署布局引用了不存在的节点")
            if not isinstance(placement.get("devices", []), list): raise HTTPException(422, "设备编号必须为列表")
        stamp = now()
        requested_devices = sum(len(p.get("devices", [])) for p in values["placements"])
        if values["runtime"] == "kubernetes":
            deploy = config["motor_deploy_config"]
            try:
                requested_devices = sum(int(deploy.get(f"{r}_instances_num", 0) or 0) * int(deploy.get(f"single_{r}_instance_pod_num", 1) or 1) * int(deploy.get(f"{r}_pod_npu_num", 0) or 0) for r in ("p", "d", "e", "hybrid"))
            except (ValueError, TypeError) as error:
                raise HTTPException(422, "Kubernetes 配置中的实例数或申请卡数无效") from error
        service = {"id": previous["id"] if previous else uid("svc_"), **values, **summary,
                   "status": "draft", "created_at": previous["created_at"] if previous else stamp,
                   "updated_at": stamp, "last_error": "", "node_names": list(dict.fromkeys(nodes[p["node_id"]]["name"] for p in values["placements"])),
                   "device_count": requested_devices, "endpoint": "", "demo": selected_mode == "demo"}
        if previous:
            for key in ("deployed_placements", "deployed_runtime", "deployed_digest", "deployed_motor_profile",
                        "deployed_user_config", "deployed_env", "config_revision", "resources_may_exist", "last_job_id"):
                if key in previous: service[key] = previous[key]
        service["has_unapplied_changes"] = bool(service.get("deployed_digest") and service["deployed_digest"] != configuration_digest(service))
        service["needs_rebuild"] = bool(service.get("resources_may_exist") and service["runtime"] == "docker")
        try: service["endpoint"] = endpoint(service, nodes)
        except (ValueError, FleetError): pass
        return service

    def payload(service: dict, placement: dict, nodes: dict[str, dict]):
        node = nodes[placement["node_id"]]
        management = next((nodes[p["node_id"]] for p in service["placements"] if p["role"] in {"coordinator_controller", "coordinator", "coordinator,controller", "single_container"}), node)
        return {"service_id": service["id"], "service_name": service["name"], "revision_id": service.get("revision_id", service["updated_at"]),
                "user_config": copy.deepcopy(service["user_config"]), "env": copy.deepcopy(service["env"]), "placement": copy.deepcopy(placement),
                "extra_manifests": copy.deepcopy(service.get("extra_manifests", [])),
                "motor_profile": service.get("motor_profile") or BASELINE_PROFILE_ID,
                "full_placements": copy.deepcopy(service["placements"]),
                "placement_nodes": {identifier: {key: value.get(key, "") for key in ("id", "address", "nic_name")} for identifier, value in nodes.items() if identifier in {p["node_id"] for p in service["placements"]}},
                "node": {"id": node["id"], "name": node["name"], "address": node["address"], "nic_name": node.get("nic_name", "")},
                "management": {"address": management["address"]}}

    def plan_service(service: dict, *, replacing: bool = False):
        checks, commands, warnings, manifests = [], [], [], []
        nodes = node_map()
        placements = service["placements"]
        if not placements: checks.append({"name": "部署布局", "status": "fail", "detail": "请至少选择一个执行节点并填写角色布局"})
        if service["runtime"] == "docker" and (service.get("motor_profile") or BASELINE_PROFILE_ID) == public_profile()["profile_id"]:
            try:
                from .motor_adapter import validate_layout
                checks.extend(validate_layout(service["user_config"], placements, env=service["env"], nodes=nodes))
            except ImportError:
                checks.append({"name": "Motor 适配器", "status": "fail", "detail": "当前构建缺少 Docker 适配器"})
            except (ValueError, TypeError, KeyError) as error:
                checks.append({"name": "Motor 配置", "status": "fail", "detail": str(error)})
        target_placements = placements[:1] if service["runtime"] == "kubernetes" else placements
        for placement in target_placements:
            if placement.get("node_id") not in nodes:
                checks.append({"name": "目标节点", "status": "fail", "detail": "目标节点不存在"}); continue
            node = nodes[placement["node_id"]]
            if selected_mode == "demo":
                checks.append({"name": node["name"] + " · 模拟预检", "status": "pass", "detail": "仅演示流程，未检查实际镜像、设备或集群"})
                commands.append({"node_id": node["id"], "role": placement.get("role", ""), "command": "# 演示环境：真实环境将由固定 Motor 适配器生成计划"})
                if service["runtime"] == "kubernetes":
                    manifests.append({"node_id": node["id"], "namespace": "演示命名空间", "resources": [],
                                      "manifest_yaml": "# 演示预览：以下仅为附加对象；未生成真实集群的原生清单\n" + json.dumps(service.get("extra_manifests", []), ensure_ascii=False, indent=2),
                                      "extra_manifest_sha256": extra_manifest_digest(service)})
                continue
            path = "/agent/kubernetes/plan" if service["runtime"] == "kubernetes" else "/agent/preflight"
            try:
                request_payload = payload(service, placement, nodes)
                if replacing: request_payload["allow_owned_stopped"] = True
                result = fleet.call(node, "POST", path, request_payload, timeout=120)
                checks.extend({**item, "name": node["name"] + " · " + str(item.get("name", "预检"))} for item in result.get("checks", []))
                if not result.get("valid") and not any(item.get("status") == "fail" for item in result.get("checks", [])):
                    checks.append({"name": node["name"], "status": "fail", "detail": "节点预检未通过"})
                warnings.extend(result.get("warnings", []))
                commands.append({"node_id": node["id"], "role": placement.get("role", ""), "command": result.get("command", "")})
                if service["runtime"] == "kubernetes":
                    manifests.append({"node_id": node["id"], **{key: result.get(key) for key in ("namespace", "resources", "manifest_yaml", "extra_manifest_sha256")}})
                    if extra_manifest_digest(service) != result.get("extra_manifest_sha256"):
                        checks.append({"name": node["name"] + " · 附加清单摘要", "status": "fail", "detail": "执行 Agent 返回的附加清单摘要与当前配置不一致"})
            except FleetError as error:
                checks.append({"name": node["name"] + " · Agent", "status": "fail", "detail": str(error)})
        if selected_mode == "demo": warnings.append("当前为演示环境；通过仅表示模拟流程，不构成部署验收。")
        return {"valid": bool(placements) and not any(item.get("status") == "fail" for item in checks), "checks": checks, "commands": commands, "warnings": warnings,
                "manifests": manifests, "extra_manifest_sha256": extra_manifest_digest(service), "configuration_digest": configuration_digest(service)}

    def run_job(job_id: str):
        job = store.get("jobs", job_id)
        if job is None: return
        with request_context(actor=job.get("actor", "system"), request_id=job.get("request_id")):
            try:
                logs.write("job.started", job_id=job_id, action=job["action"], service_id=job["service_id"])
                execute_job(job_id)
                result = store.get("jobs", job_id)
                logs.write("job.finished", severity="ERROR" if result["status"] == "failed" else "INFO", job_id=job_id,
                           action=result["action"], status=result["status"], error=result.get("error", ""))
            except Exception:
                application.state.log_health = False
                with mutation_lock:
                    current = store.get("jobs", job_id)
                    if current and current["status"] in {"queued", "running"}:
                        current.update(status="failed", finished_at=now(), error="平台任务日志不可用；任务已终止，请核对执行证据后重试")
                        service = store.get("services", current["service_id"])
                        if service:
                            service.update(status="error" if current.get("host_actions_attempted") else current.get("previous_status", "error"),
                                           last_error=current["error"], updated_at=now())
                            store.put("services", service)
                        store.put("jobs", current)
                print(json.dumps({"event": "job_log_unavailable", "job_id": job_id}), file=sys.stderr)

    def execute_job(job_id: str):
        job = store.get("jobs", job_id)
        if job is None: return
        service = store.get("services", job["service_id"])
        if service is None: return
        action, nodes = job["action"], node_map()
        scaled_config = None
        try:
            job["status"] = "running"; store.put("jobs", job)
            if action in {"deploy", "rebuild"}:
                plan = plan_service(service, replacing=action == "rebuild")
                job["plan"] = plan; store.put("jobs", job)
                if not plan["valid"]: raise FleetError("预检未通过：" + "; ".join(item["detail"] for item in plan["checks"] if item["status"] == "fail")[:2000])
                if plan["extra_manifest_sha256"] and plan["extra_manifest_sha256"] != job.get("extra_manifests_approved_sha256"):
                    raise FleetError("附加清单已变化，请重新审查计划并确认摘要")
                if plan["extra_manifest_sha256"] and plan["configuration_digest"] != job.get("reviewed_configuration_digest"):
                    raise FleetError("服务配置已变化，请重新审查部署计划")
            if action == "scale":
                scale_plan = plan_scale(service, job["scale_counts"])
                job["scale_plan"] = scale_plan; store.put("jobs", job)
                scaled_config = scale_plan["user_config"]
            if selected_mode == "demo":
                for step in job["steps"]:
                    step.update(status="running", message="演示任务执行中"); store.put("jobs", job)
                    time.sleep(0.2)
                    step.update(status="completed", message="演示操作已完成，未操作真实节点"); store.put("jobs", job)
                service["status"] = "stopped" if action == "stop" else "running"
            else:
                all_placements = execution_placements(service, action)
                placements = all_placements[:1] if service["runtime"] == "kubernetes" else all_placements
                if action not in {"deploy", "rebuild"}:
                    seen = set()
                    placements = [p for p in placements if not (p["node_id"] in seen or seen.add(p["node_id"]))]

                if action == "rebuild" and service["runtime"] == "docker":
                    # Explicitly authorized rebuild: all new checks pass before removing stopped resources.
                    for identifier in dict.fromkeys(p["node_id"] for p in service.get("deployed_placements", [])):
                        operation_id = f"{job_id}:cleanup:{identifier}:remove"
                        cleanup = {"node_id": identifier, "role": "cleanup", "status": "running", "operation_id": operation_id}
                        job.setdefault("cleanup_steps", []).append(cleanup)
                        job["host_actions_attempted"] = True
                        store.put("jobs", job)
                        try:
                            receipt = fleet.call(nodes[identifier], "POST", "/agent/remove", {"service_id": service["id"],
                                "motor_profile": service.get("deployed_motor_profile") or BASELINE_PROFILE_ID,
                                "node_id": identifier,
                                "operation_id": operation_id})
                            cleanup.update(status="completed", operation_status="completed")
                        except FleetError as error:
                            cleanup.update(status="interrupted" if error.outcome_unknown else "failed",
                                           operation_status=(error.receipt or {}).get("operation_status", "unknown"), message=str(error))
                            job["outcome_unknown"] = error.outcome_unknown
                            raise
                        finally:
                            store.put("jobs", job)

                def execute(index: int, placement: dict):
                    node = nodes[placement["node_id"]]
                    with mutation_lock:
                        job["steps"][index].update(status="running", message="已提交节点执行")
                        store.put("jobs", job)
                    prefix = "/agent/kubernetes" if service["runtime"] == "kubernetes" else "/agent"
                    operation = "deploy" if action == "rebuild" else "remove" if action == "undeploy" else action
                    with mutation_lock:
                        job["host_actions_attempted"] = True
                        store.put("jobs", job)
                    request_payload = payload(service, placement, nodes)
                    if action == "scale": request_payload = {"service_id": service["id"], "counts": job["scale_counts"], "motor_profile": service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID}
                    elif action not in {"deploy", "rebuild"}: request_payload["motor_profile"] = service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID
                    if action in {"deploy", "rebuild"}:
                        request_payload["extra_manifests_approved_sha256"] = job.get("extra_manifests_approved_sha256", "")
                    if service["runtime"] == "docker":
                        request_payload["operation_id"] = f"{job_id}:{index}:{operation}"
                        with mutation_lock:
                            job["steps"][index].update(operation_id=request_payload["operation_id"], operation_status="submitting")
                            store.put("jobs", job)
                    try:
                        result = fleet.call(node, "POST", prefix + "/" + operation, request_payload, timeout=240)
                    except FleetError as error:
                        with mutation_lock:
                            if error.outcome_unknown: job["outcome_unknown"] = True
                            job["steps"][index].update(status="interrupted" if error.outcome_unknown else "failed",
                                operation_status=(error.receipt or {}).get("operation_status", "unknown" if error.outcome_unknown else "failed"),
                                message=redact(str(error))[:2000])
                            store.put("jobs", job)
                        raise
                    if result.get("status") in {"error", "failed"}: raise FleetError(result.get("message", "节点执行失败"))
                    if action == "scale" and result.get("user_config") != scaled_config:
                        raise FleetError("执行 Agent 返回的扩缩容配置与已校验计划不一致，请核对受管资源")
                    with mutation_lock:
                        job["steps"][index].update(status="completed", message=result.get("message", "节点执行完成"), resource_id=result.get("container_id", ""), runtime_status=result.get("status", "unknown"),
                                                  **({"operation_status": "completed"} if service["runtime"] == "docker" else {}))
                        store.put("jobs", job)

                management_roles = {"coordinator_controller", "coordinator", "controller", "coordinator,controller", "kv_store"}
                # Preserve dependencies and the Agent's non-queuing execution capacity.
                first = [i for i, p in enumerate(placements) if action in {"deploy", "rebuild"} and p.get("role") in management_roles]
                for index in first: execute(index, placements[index])
                remaining = [i for i in range(len(placements)) if i not in first]
                agent_groups = {}
                for index in remaining:
                    agent_groups.setdefault(placements[index]["node_id"], []).append(index)

                def execute_group(indices):
                    for offset, index in enumerate(indices):
                        try:
                            execute(index, placements[index])
                        except Exception as error:
                            with mutation_lock:
                                if job["steps"][index]["status"] != "interrupted":
                                    job["steps"][index].update(status="failed", message=redact(str(error))[:2000])
                                for pending_index in indices[offset + 1:]:
                                    if job["steps"][pending_index]["status"] == "pending":
                                        job["steps"][pending_index].update(status="cancelled",
                                            message="同一 Agent 的前序步骤失败或结果未确认；后续步骤未提交，请核对资源与回执")
                                store.put("jobs", job)
                            raise

                failures = []
                with ThreadPoolExecutor(max_workers=min(8, max(1, len(agent_groups)))) as node_pool:
                    futures = [node_pool.submit(contextvars.copy_context().run, execute_group, indices)
                               for indices in agent_groups.values()]
                    for future in as_completed(futures):
                        try: future.result()
                        except Exception as error:
                            failures.append(str(error))
                if failures: raise FleetError("; ".join(failures)[:3000])
                if action == "undeploy": service["status"] = "removing"
                elif action == "stop":
                    service["status"] = "stopping" if service["runtime"] == "kubernetes" and any(step.get("runtime_status") != "stopped" for step in job["steps"]) else "stopped"
                else: service["status"] = "starting"
            if action == "scale":
                completed_status = service["status"]
                fields = {key: service[key] for key in ("name", "runtime", "env", "placements")}
                fields["motor_profile"] = service.get("motor_profile", BASELINE_PROFILE_ID)
                service = render_service(ServiceInput(**fields, user_config=scaled_config, extra_manifests=service.get("extra_manifests", [])), service)
                service["status"] = completed_status
            if action in {"deploy", "rebuild", "scale"}:
                actual = service["placements"][:1] if service["runtime"] == "kubernetes" else service["placements"]
                service.update(deployed_placements=copy.deepcopy(actual), deployed_runtime=service["runtime"], deployed_digest=configuration_digest(service),
                               deployed_motor_profile=service.get("motor_profile") or BASELINE_PROFILE_ID,
                               deployed_user_config=copy.deepcopy(service["user_config"]), deployed_env=copy.deepcopy(service["env"]))
            service.update(updated_at=now(), last_error="", resources_may_exist=True,
                           needs_rebuild=action == "stop" and service["runtime"] == "docker",
                           has_unapplied_changes=bool(service.get("deployed_digest") and service["deployed_digest"] != configuration_digest(service)))
            if action == "undeploy" and selected_mode == "demo":
                service.update(status="draft", resources_may_exist=False, deployed_placements=[], deployed_digest=None, has_unapplied_changes=False)
            job.update(status="completed", finished_at=now(), error="")
            store.mutate_many_audited([("services", service), ("jobs", job)],
                revision_keys={("services", service["id"])} if action == "scale" else set(),
                action=action, target=service["name"],
                detail="节点操作完成；业务就绪需独立探测" if selected_mode != "demo" else "演示操作")
        except Exception as error:
            message = "平台状态或审计存储不可用，请核对任务执行证据" if isinstance(error, sqlite3.Error) else redact(str(error))[:4000]
            interrupted = bool(job.get("outcome_unknown") or getattr(error, "outcome_unknown", False))
            job.update(status="interrupted" if interrupted else "failed", error=message, finished_at=now())
            may_exist = service.get("resources_may_exist", False) or bool(job.get("host_actions_attempted"))
            failed_status = job.get("previous_status", "error") if action == "scale" and not job.get("host_actions_attempted") else "error"
            service.update(status=failed_status, last_error=message, updated_at=now(), resources_may_exist=may_exist)
            if job.get("host_actions_attempted"):
                previous_placements = service.get("deployed_placements", [])
                service["deployed_placements"] = list({json.dumps(p, sort_keys=True): p for p in [*previous_placements, *service["placements"]]}.values())
            for step in job["steps"]:
                if step["status"] == "running": step.update(status="interrupted" if interrupted else "failed", message=message[:2000])
            try:
                store.mutate_many_audited([("services", service), ("jobs", job)], action=action,
                                         target=service["name"], status="failed", detail=message[:2000])
            except Exception:
                # Accepted host effects cannot be rolled back by an audit fault.
                application.state.log_health = False
                job["audit_recorded"] = False
                store.put_many([("services", service), ("jobs", job)])
                try: logs.write("job.audit_unavailable", severity="ERROR", job_id=job["id"], error_type=type(error).__name__)
                except (OSError, RuntimeError, sqlite3.Error): pass

    def submit_job(identifier: str, action: str, approval: DeploymentApproval | None = None, scale: ScaleInput | None = None, undeploy: UndeployInput | None = None, request: Request | None = None):
        with mutation_lock:
            service = service_or_404(identifier)
            key = request.headers.get("Idempotency-Key", "") if request else ""
            command_hash = None
            fingerprint = None
            if key:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", key): raise HTTPException(422, "Idempotency-Key 格式无效")
                command_hash = hashlib.sha256((context_fields()["actor"] + ":" + key).encode()).hexdigest()
                intent = {"service_id": identifier, "action": action,
                          "approval": approval.model_dump() if approval else None, "counts": scale.counts if scale else None,
                          "confirmation": undeploy.confirmation_name if undeploy else None}
                fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
                previous = store.get("idempotency", command_hash)
                if previous:
                    if previous["fingerprint"] != fingerprint: raise HTTPException(409, "此幂等键已用于不同操作，请核对原任务")
                    return {"job_id": previous["job_id"], "replayed": True}
            if service["status"] == "removing" and action != "undeploy":
                raise HTTPException(409, "受管资源正在卸载，请等待删除收敛后操作")
            if action == "undeploy":
                if service["runtime"] != "kubernetes" or not service.get("resources_may_exist"):
                    raise HTTPException(409, "仅能卸载已部署的 Kubernetes 受管资源")
                if undeploy is None or undeploy.confirmation_name != service["name"]:
                    raise HTTPException(409, "请准确输入服务名称，确认卸载该服务的受管部署")
            if action == "scale":
                require_scalable(service)
                if scale is None: raise HTTPException(422, "缺少目标实例数")
            expected_hash = extra_manifest_digest(service) if action in {"deploy", "rebuild"} else None
            approved_hash = approval.extra_manifests_approved_sha256 if approval else ""
            if expected_hash and approved_hash != expected_hash:
                raise HTTPException(409, "请先查看 Kubernetes 部署计划，明确确认附加清单摘要")
            if expected_hash and (not approval or approval.reviewed_configuration_digest != configuration_digest(service)):
                raise HTTPException(409, "已审查的服务配置不一致，请重新生成计划并确认")
            if action in {"restart", "stop"} and (not service.get("resources_may_exist") or not execution_placements(service, action)):
                raise HTTPException(409, "服务尚未部署，没有可操作的受管资源")
            if action == "stop" and not execution_placements(service, action):
                raise HTTPException(409, "没有可停止的执行目标")
            active_jobs = [job for job in store.list("jobs") if job["status"] in {"queued", "running"}]
            if any(job["service_id"] == identifier for job in active_jobs):
                raise HTTPException(409, "该服务已有执行中的任务")
            if len(active_jobs) >= 64:
                raise HTTPException(429, "平台部署任务队列已满，请等待现有任务完成", headers={"Retry-After": "10"})
            if action == "deploy" and service["status"] not in {"draft", "stopped", "error"}: raise HTTPException(409, "请先停止当前服务再部署")
            if action == "deploy" and service.get("resources_may_exist") and service["runtime"] == "docker": raise HTTPException(409, "已有受管容器，请使用明确的重建操作应用当前配置")
            if action == "rebuild" and service["status"] not in {"draft", "stopped"}: raise HTTPException(409, "重建前必须停止服务")
            if action == "restart" and service.get("deployed_digest") and configuration_digest(service) != service["deployed_digest"]: raise HTTPException(409, "有未应用配置；重启只使用旧容器修订，请选择重建并应用")
            if action == "restart" and service["runtime"] == "docker" and (service.get("needs_rebuild") or service["status"] == "stopped"):
                raise HTTPException(409, "Docker 服务已停止或需要重建；请重新预检资源并执行重建，不能跳过准入直接重启旧容器")
            source_placements = execution_placements(service, action)
            targets = source_placements[:1] if service["runtime"] == "kubernetes" else source_placements
            if action not in {"deploy", "rebuild"}:
                seen = set(); targets = [p for p in targets if not (p["node_id"] in seen or seen.add(p["node_id"]))]
            job = {"id": uid("job_"), "service_id": identifier, "action": action, "status": "queued", "created_at": now(), "error": "",
                   "execution_runtime": service["runtime"], "execution_placements": copy.deepcopy(targets),
                   **context_fields(),
                   "extra_manifests_approved_sha256": approved_hash,
                   "reviewed_configuration_digest": approval.reviewed_configuration_digest if approval else "",
                   "scale_counts": scale.counts if scale else None, "previous_status": service["status"],
                   "steps": [{"node_id": p["node_id"], "role": p.get("role", ""), "status": "pending", "message": "等待执行"} for p in targets]}
            service.update(status={"deploy": "deploying", "rebuild": "deploying", "stop": "stopping", "restart": "restarting", "scale": "scaling", "undeploy": "removing"}[action], updated_at=now(), last_job_id=job["id"])
            records = [("jobs", job), ("services", service)]
            if command_hash: records.append(("idempotency", {"id": command_hash, "fingerprint": fingerprint, "job_id": job["id"], "created_at": now()}))
            store.mutate_many_audited(records, action="任务受理", target=service["name"], status="accepted",
                                     metadata={"job_id": job["id"], "action": action})
            try:
                executor.submit(run_job, job["id"])
            except Exception as error:
                # Return the durable accepted ID even if scheduling then fails.
                job.update(status="failed", error="任务调度不可用，未执行节点操作", finished_at=now())
                service.update(status=job["previous_status"], last_error=job["error"], updated_at=now())
                try:
                    store.mutate_many_audited([("jobs", job), ("services", service)], action="任务调度失败",
                                             target=service["name"], status="failed", detail=job["error"])
                except Exception:
                    application.state.log_health = False
                    job["audit_recorded"] = False
                    store.put_many([("jobs", job), ("services", service)])
                try: logs.write("job.schedule_failed", severity="ERROR", job_id=job["id"], error_type=type(error).__name__)
                except (OSError, RuntimeError, sqlite3.Error): application.state.log_health = False
            return {"job_id": job["id"]}

    def require_scalable(service: dict):
        if service["runtime"] != "kubernetes": raise HTTPException(409, "在线扩缩容仅支持 Kubernetes 原生实例组")
        if service["status"] not in {"running", "starting", "partial"} or not service.get("resources_may_exist"):
            raise HTTPException(409, "请先部署 Kubernetes 服务，且不能同时执行其他操作")

    def plan_scale(service: dict, counts: dict):
        candidate = copy.deepcopy(service["user_config"])
        candidate["motor_deploy_config"].update(counts)
        if selected_mode == "demo":
            return {"valid": True, "demo": True, "counts": counts, "user_config": candidate,
                    "message": "演示扩缩容计划；真实环境仅支持原生受管 InferServiceSet，不能与自动扩缩容竞争"}
        placements = execution_placements(service, "restart")
        if len(placements) != 1: raise FleetError("缺少唯一 Kubernetes 执行 Agent")
        node = node_or_404(placements[0]["node_id"])
        result = fleet.call(node, "POST", "/agent/kubernetes/scale", {"service_id": service["id"], "counts": counts, "dry_run": True,
                            "motor_profile": service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID}, timeout=120)
        if result.get("user_config") != candidate: raise FleetError("执行 Agent 的配置基线与控制面不一致，未执行扩缩容")
        return result

    @application.get("/api/overview", dependencies=secured)
    def overview(request: Request):
        services, nodes = store.list("services"), store.list("nodes")
        current = [inventory_view(n) for n in nodes if n.get("docker_available") is True]
        known = [n for n in current if (n.get("resource_freshness") == "fresh" or selected_mode == "demo" and n.get("demo"))
                 and n.get("npu_total") is not None and not n.get("identity_conflict")
                 and not any(other["id"] != n["id"] and (
                     n.get("host_identity") and n["host_identity"] == other.get("host_identity")
                     or n.get("daemon_id") and n["daemon_id"] == other.get("daemon_id")
                     or n.get("address") == other.get("address")) for other in current)]
        npu_value = sum(n["npu_total"] for n in known) if known else "—"
        counts = {"running": sum(s["status"] == "running" for s in services), "stopped": sum(s["status"] == "stopped" for s in services), "error": sum(s["status"] == "error" for s in services), "total": len(services)}
        return {"stats": [{"key": "services", "label": "推理服务", "value": len(services), "unit": "个", "detail": f"{counts['running']} 个业务就绪"},
                          {"key": "nodes", "label": "接入节点", "value": len(nodes), "unit": "台", "detail": f"{sum(n['status'] == 'online' for n in nodes)} 台探测在线"},
                          {"key": "npu", "label": "Docker NPU 已采集", "value": npu_value, "unit": "个逻辑设备", "detail": f"{len(known)}/{len(current)} 个 Agent 有有效观测；声明容量不代表空闲"},
                          {"key": "runtimes", "label": "运行时", "value": len(plugins.enabled_runtimes()), "unit": "种", "detail": " / ".join(plugins.enabled_runtimes())}],
                "series": demo.series() if selected_mode == "demo" else [], "activities": [{"id": a["id"], "title": a["action"], "detail": a["target"] + " · " + a["detail"], "time": a["time"], "status": a["status"]} for a in store.audit_items(10)] if role_for_request(request) in {"admin", "demo"} else [], "service_counts": counts, "demo": selected_mode == "demo"}

    plugins.mount("templates", mount_templates, plugin_context, plugin_cleanup)

    @application.get("/api/services", dependencies=secured)
    def get_services(request: Request):
        nodes = node_map()
        current = [reconcile_stopping(service) for service in store.list("services")]
        return {"items": [service_view(service, request, nodes=nodes) for service in current if not service.get("deleted")]}

    @application.post("/api/services", dependencies=secured, status_code=201)
    def create_service(data: ServiceInput):
        with mutation_lock:
            if any(s["name"] == data.name for s in store.list("services")): raise HTTPException(409, "服务名称已存在")
            result = render_service(data)
            store.mutate_audited("services", result["id"], result, revision=True,
                                 action="创建服务", target=result["name"], detail="保存草稿，尚未执行部署")
            return result

    @application.get("/api/services/{identifier}", dependencies=secured)
    def get_service(identifier: str, request: Request):
        service = reconcile_stopping(service_or_404(identifier))
        if service.get("deleted"): raise HTTPException(404, "服务已完成删除")
        return service_view(service, request)

    @application.put("/api/services/{identifier}", dependencies=secured)
    def edit_service(identifier: str, data: ServiceInput):
        with mutation_lock:
            if data.expected_revision is None:
                raise HTTPException(422, "保存配置必须提供 expected_revision，请先读取当前服务修订")
            previous = service_or_404(identifier); check_not_active(previous)
            if previous.get("resources_may_exist") and previous["runtime"] != data.runtime: raise HTTPException(409, "切换运行时前请先移除原运行时资源，再创建新服务")
            if any(s["name"] == data.name and s["id"] != identifier for s in store.list("services")): raise HTTPException(409, "服务名称已存在")
            result = render_service(data, previous)
            store.mutate_audited("services", result["id"], result, revision=True, expected_revision=data.expected_revision,
                                 action="更新配置", target=result["name"], detail="已保存新修订，尚未应用")
            return result

    @application.post("/api/services/{identifier}/plan", dependencies=secured)
    def service_plan(identifier: str):
        service = service_or_404(identifier)
        return plan_service(service, replacing=bool(service.get("needs_rebuild")))

    @application.post("/api/services/{identifier}/deploy", dependencies=secured)
    def deploy_service(identifier: str, request: Request, approval: DeploymentApproval | None = None): return submit_job(identifier, "deploy", approval, request=request)

    @application.post("/api/services/{identifier}/stop", dependencies=secured)
    def stop_service(identifier: str, request: Request): return submit_job(identifier, "stop", request=request)

    @application.post("/api/services/{identifier}/restart", dependencies=secured)
    def restart_service(identifier: str, request: Request): return submit_job(identifier, "restart", request=request)

    @application.post("/api/services/{identifier}/rebuild", dependencies=secured)
    def rebuild_service(identifier: str, request: Request, approval: DeploymentApproval | None = None): return submit_job(identifier, "rebuild", approval, request=request)

    @application.post("/api/services/{identifier}/scale/plan", dependencies=secured)
    def scale_service_plan(identifier: str, data: ScaleInput):
        service = service_or_404(identifier)
        require_scalable(service)
        return plan_scale(service, data.counts)

    @application.post("/api/services/{identifier}/scale", dependencies=secured)
    def scale_service(identifier: str, request: Request, data: ScaleInput): return submit_job(identifier, "scale", scale=data, request=request)

    @application.post("/api/services/{identifier}/undeploy", dependencies=secured)
    def undeploy_service(identifier: str, request: Request, data: UndeployInput): return submit_job(identifier, "undeploy", undeploy=data, request=request)

    @application.delete("/api/services/{identifier}", dependencies=secured)
    def delete_service(identifier: str):
        with mutation_lock:
            service = service_or_404(identifier)
            if not (service["status"] == "removing" and service.get("deletion_requested_at")):
                check_not_active(service)
            if selected_mode != "demo" and service.get("resources_may_exist"):
                if not execution_placements(service, "remove"):
                    raise HTTPException(409, "服务可能仍有资源，但缺少可验证的执行布局；保留记录，请先核对原任务与节点")
                service.update(status="removing", updated_at=now(),
                               deletion_requested_at=service.get("deletion_requested_at") or now(),
                               deletion_operation_id=service.get("deletion_operation_id") or uid("delete_"))
                store.mutate_audited("services", identifier, service, action="删除服务受理", target=service["name"],
                                     status="accepted", detail="先持久记录删除意图，受管资源收敛前保留服务与节点引用")
                seen = set()
                pending = False
                try:
                    for placement in execution_placements(service, "remove"):
                        if placement["node_id"] in seen: continue
                        seen.add(placement["node_id"])
                        node = node_or_404(placement["node_id"])
                        path = "/agent/kubernetes/remove" if service["runtime"] == "kubernetes" else "/agent/remove"
                        result = fleet.call(node, "POST", path, {"service_id": identifier, "node_id": node["id"],
                            "operation_id": service["deletion_operation_id"] + "-" + node["id"],
                            "motor_profile": service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID})
                        if service["runtime"] == "kubernetes":
                            pending = pending or result.get("removal_converged") is not True
                        else:
                            pending = pending or bool(result.get("resources_may_exist")) or result.get("status") != "removed"
                except Exception:
                    try: logs.write("service.delete_external_failed", severity="ERROR", service_id=identifier, attempted_node_ids=sorted(seen))
                    except (OSError, RuntimeError, sqlite3.Error): application.state.log_health = False
                    raise
                if pending:
                    return JSONResponse({"ok": False, "status": "removing", "resources_may_exist": True,
                                         "detail": "删除已受理，等待受管资源与 Pod 收敛；服务记录和引用继续保留"}, status_code=202)
            try:
                store.mutate_many_audited([("deleted_services", {**service, "deleted_at": now()})],
                    deletes=[("services", identifier)], action="删除服务", target=service["name"],
                    detail="删除受管资源请求已提交；修订和审计保留，不删除权重")
            except Exception:
                try: logs.write("service.delete_uncommitted", severity="ERROR", service_id=identifier,
                                external_effects_possible=bool(service.get("resources_may_exist")))
                except (OSError, RuntimeError, sqlite3.Error): application.state.log_health = False
                raise
            return {"ok": True}

    @application.get("/api/services/{identifier}/export", dependencies=secured)
    def export_service(identifier: str):
        service = service_or_404(identifier)
        return {**{key: service[key] for key in ("name", "runtime", "user_config", "env", "placements")},
                "extra_manifests": service.get("extra_manifests", []), "motor_profile": service.get("motor_profile") or BASELINE_PROFILE_ID}

    @application.get("/api/services/{identifier}/revisions", dependencies=secured)
    def service_revisions(identifier: str):
        service_or_404(identifier); return {"items": store.revisions(identifier)}

    @application.get("/api/services/{identifier}/logs", dependencies=secured)
    def service_logs(identifier: str, tail: int = Query(200, ge=1, le=2000),
                     node_id: str | None = Query(None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
                     role: str | None = Query(None, min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_-]*$"),
                     limit: int | None = Query(None, ge=1, le=2000), offset: int = Query(0, ge=0, le=1999)):
        from urllib.parse import urlencode
        service = service_or_404(identifier)
        try:
            role = validate_log_role(role)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        page_size = limit or tail
        window_size = max(tail, offset + page_size)
        if window_size > 2000:
            raise HTTPException(422, "日志分页窗口最多2000条，请缩小页码范围或按节点、角色筛选")
        errors = []
        if selected_mode == "demo":
            items = [{"time": now(), "level": "INFO", "level_source": "platform", "role": "coordinator", "node": "ascend-a3-01", "source": "demo", "message": "[演示] Coordinator started; waiting for native instance registration"},
                     {"time": now(), "level": "INFO", "level_source": "platform", "role": "union", "node": "ascend-a3-01", "source": "demo", "message": "[演示] 模型实例已注册，演示请求已完成；此日志不来自真实推理引擎"}]
        else:
            targets, seen = [], set()
            log_service = {**service, "runtime": service.get("deployed_runtime") or service["runtime"]}
            for placement in execution_placements(log_service, "stop"):
                target = placement["node_id"]
                if target in seen:
                    continue
                seen.add(target)
                if node_id is None or target == node_id:
                    targets.append(node_or_404(target))
            if node_id is not None and node_id not in seen:
                raise HTTPException(422, "该执行节点不在当前服务的运行布局中")
            def fetch(node, budget):
                prefix = "/agent/kubernetes" if log_service["runtime"] == "kubernetes" else "/agent"
                query = {"service_id": identifier, "tail": window_size}
                if prefix == "/agent":
                    query["node_id"] = node["id"]
                if role is not None:
                    query["role"] = role
                response = fleet.call(node, "GET", prefix + "/logs?" + urlencode(query), timeout=budget, total_timeout=budget)
                if role is not None and (response.get("filters") or {}).get("role") != role:
                    response = {**response, "partial": True}
                return response
            items, errors = service_log_reader.read(targets, fetch, seconds=20)
            messages = {"log_total_deadline": "日志读取超时，本次只显示已返回的来源", "log_reader_busy": "日志读取繁忙，请稍后重试",
                        "source_log_window_partial": "该来源仅返回了部分日志片段", "source_log_error": "该来源的部分容器日志暂不可读取"}
            for error in errors:
                items.append({"time": "", "observed_at": now(), "level": "WARN" if error["code"] == "source_log_window_partial" else "ERROR",
                              "level_source": "platform", "role": "agent", "node": error["node_id"], "agent_node_id": error["node_id"],
                              "node_name": error["node_name"], "message": messages.get(error["code"], "无法读取该节点日志，请检查Agent连接"), "source": "agent_error"})
        items = [item for item in items if item.get("source") == "agent_error" or role_matches(item.get("role"), role)]
        sources = {source_key(item) for item in items}
        window = fair_tail(items, window_size)
        page = window[offset:offset + page_size]
        return {"items": [redact(item) for item in page], "errors": errors, "partial": bool(errors),
                "total": len(window), "limit": page_size, "offset": offset, "has_more": offset + len(page) < len(window),
                "window": "bounded_recent_tail", "consistent_snapshot": False,
                "sources_total": len(sources), "sources_included": len({source_key(item) for item in page}),
                "filters": {"node_id": node_id, "role": role}}

    @application.get("/api/integrations/images/catalog", dependencies=secured)
    def integration_images():
        return image_catalog()

    @application.post("/api/integrations/images/inspect", dependencies=secured)
    def inspect_integration_image(data: ImageInspectionInput):
        from .image_contract import sanitize_inspection_result
        node = node_or_404(data.node_id)
        if selected_mode == "demo":
            raise HTTPException(409, "演示环境不执行真实镜像检查，请接入授权执行 Agent")
        if node.get("docker_available") is False:
            raise HTTPException(422, "此执行 Agent 未接入 Docker；K8S 镜像不能用宿主 Docker 库存代替检查")
        try:
            profile = public_profile()
            payload = {"image": data.image, "probe_runtime": data.probe_runtime,
                       "motor_profile": profile["profile_id"]}
            result = fleet.call(node, "POST", "/agent/images/inspect", payload,
                                timeout=150 if data.probe_runtime else 45, max_response_bytes=MODEL_RESPONSE_BYTES)
            result = sanitize_inspection_result(result, expected_image=data.image, expected_profile=profile)
        except (FleetError, ValueError) as exc:
            raise HTTPException(502, redact(str(exc))) from exc
        store.audit("检查推理镜像", node["name"], "success", detail="已读取镜像检查报告，未部署或加载模型",
                    metadata={"node_id": data.node_id, "image_id": result["image_id"],
                              "probe_requested": data.probe_runtime, "compatibility": "unverified"})
        return result

    @application.get("/api/services/{identifier}/aigw-models", dependencies=secured)
    def service_aigw_models(identifier: str):
        service = service_or_404(identifier)
        original_digest = configuration_digest(service)
        empty = {"source": "motor_coordinator", "items": [], "collected_at": now()}
        if selected_mode == "demo":
            return {**empty, "status": "demo", "error": "演示环境不模拟真实 AIGW 模型发现"}
        if service["status"] in {"draft", "stopped", "stopping", "removing"} or not service.get("resources_may_exist"):
            return {**empty, "status": "not_deployed", "error": "服务尚未运行，未连接该地址上的业务"}
        try:
            if service["runtime"] == "kubernetes":
                if not service["placements"]:
                    raise FleetError("缺少 Kubernetes 执行入口")
                node = node_or_404(service["placements"][0]["node_id"])
                result = fleet.call(node, "GET", "/agent/kubernetes/aigw-models?service_id=" + identifier,
                                    timeout=40, max_response_bytes=MODEL_RESPONSE_BYTES)
                if not isinstance(result, dict) or result.get("status") != "available":
                    raise FleetError("执行 Agent 返回了无效的模型发现结果")
                result = {**empty, "status": "available", "items": project_models({"data": result.get("items")})}
            else:
                result = discover_models(service, node_map())
            latest = store.get("services", identifier)
            if (not latest or configuration_digest(latest) != original_digest
                    or latest["status"] in {"draft", "stopped", "stopping", "removing"}
                    or not latest.get("resources_may_exist")):
                return {**empty, "status": "unavailable", "error": "服务配置或运行状态已变化，请重新读取模型列表"}
            return {**result, "items": [redact(item) for item in result["items"]]}
        except FleetError as exc:
            return {**empty, "status": "unavailable", "error": redact(str(exc))}

    @application.get("/api/services/{identifier}/metrics", dependencies=secured)
    def service_metrics(identifier: str):
        with mutation_lock:
            service = service_or_404(identifier)
            nodes = node_map()
            current_digest = configuration_digest(service)
            revision = metrics_revision(service, nodes, current_digest)
            if service.get("deployed_digest") and service["deployed_digest"] != current_digest:
                service = {**service, "has_unapplied_changes": True}

        def is_current():
            with mutation_lock:
                latest = store.get("services", identifier)
                return latest is not None and metrics_revision(
                    latest, node_map(), configuration_digest(latest)) == revision

        return service_metrics_sampler.sample(service, nodes, revision=revision,
                                              is_current=is_current, fleet=fleet, demo=selected_mode == "demo")

    @application.get("/api/services/{identifier}/observations", dependencies=secured)
    def service_observations(identifier: str):
        service = service_or_404(identifier)
        observation_started = now()
        original_digest = configuration_digest(service)
        original_updated = service["updated_at"]
        if selected_mode == "demo":
            return {"demo": True, "instances": {"instances": [{"id": 1, "role": "union", "healthy": service["status"] == "running", "status": "ACTIVE"}]}, "readiness": {"ready": service["status"] == "running"}, "metrics_text": "# DEMO: simulated metrics\nmotor:request_rate 56.8\nmotor:request_latency 185.4\n", "observed_at": now(), "errors": []}
        if service["status"] in {"draft", "stopped"} or not service.get("resources_may_exist"):
            return {"instances": None, "readiness": None, "metrics_text": "", "errors": [{"source": "lifecycle", "detail": "服务尚未部署或已停止，未连接该地址上的业务"}], "observed_at": now()}
        if service["runtime"] == "kubernetes":
            if not service["placements"]: raise HTTPException(422, "缺少 K8S 执行节点")
            node = node_or_404(service["placements"][0]["node_id"])
            deadline = time.monotonic() + 40
            result = fleet.call(node, "GET", "/agent/kubernetes/observations?service_id=" + identifier,
                                timeout=32, total_timeout=32)
            status = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise FleetError("Kubernetes capacity observation deadline elapsed")
                status = fleet.call(node, "GET", "/agent/kubernetes/status?service_id=" + identifier,
                                    timeout=min(8, remaining), total_timeout=remaining)
            except Exception:
                result = {**result, "errors": [*result.get("errors", []),
                          {"source": "workload", "detail": "受管 Kubernetes UID 与角色目标未在本次总预算内核实。"}]}
            from .kubernetes_health import kubernetes_observation_gates
            result = kubernetes_observation_gates(service, result, status)
        else:
            result = service_observer.observe(service, node_map(), observations)
        with mutation_lock:
            latest = store.get("services", identifier)
            eligible = latest and latest["updated_at"] == original_updated and configuration_digest(latest) == original_digest
            eligible = eligible and latest["status"] in {"running", "starting", "partial"}
            eligible = eligible and latest.get("last_observation_started", "") <= observation_started
            if eligible:
                gate = result.get("readiness_gate")
                latest["status"] = ({"ready": "running", "starting": "starting", "degraded": "partial", "unknown": "partial"}[gate["state"]]
                                    if gate else "running" if ready_value(result["readiness"]) else "starting" if not result["errors"] else "partial")
                latest["last_observation_started"] = observation_started
                latest["last_observed_at"] = result.get("observed_at", now())
                store.put("services", latest)
            else:
                result.update(stale=True, readiness=None, instances=None)
                for field in ("runtime", "registration_gate", "readiness_gate"):
                    if field in result:
                        result[field] = None
                result.setdefault("errors", []).append({"source": "revision", "detail": "服务修订或运行状态已变化，本次旧观测未应用"})
        result["errors"] = [redact(item) for item in result.get("errors", [])]
        for field in ("runtime", "registration_gate", "readiness_gate"):
            if isinstance(result.get(field), dict):
                result[field]["issues"] = [redact(item) for item in result[field].get("issues", [])]
        return result

    @application.post("/api/services/{identifier}/inference", dependencies=secured)
    def inference(identifier: str, data: InferenceInput):
        service = service_or_404(identifier)
        if service["status"] not in {"running", "starting", "partial"} or (selected_mode != "demo" and not service.get("resources_may_exist")):
            raise HTTPException(409, "只能向已部署且正在运行的服务发起推理，草稿或已停止服务不会连接其他同地址业务")
        if selected_mode == "demo": return {"text": "这是平台的演示响应。真实环境会通过所选服务的 Coordinator 执行推理，并返回实际结果。", "latency_ms": 186, "usage": {"prompt_tokens": 12, "completion_tokens": 32}, "endpoint": service["endpoint"], "demo": True}
        if service["runtime"] == "kubernetes":
            if not service["placements"]: raise HTTPException(422, "缺少 K8S 执行节点")
            node = node_or_404(service["placements"][0]["node_id"])
            result = fleet.call(node, "POST", "/agent/kubernetes/inference", {"service_id": identifier, "motor_profile": service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID, **data.model_dump()}, timeout=190)
            store.audit("推理验证", service["name"], "success", "经 K8S Agent 收到推理结果")
            return result
        begin = time.monotonic()
        result = motor_request(service, node_map(), "infer", "/v1/chat/completions", method="POST", body={"model": service["model"], "messages": [{"role": "user", "content": data.prompt}], "max_tokens": data.max_tokens, "stream": False}, timeout=180)
        try:
            output = inference_output(result)
        except ValueError as exc:
            store.audit("推理验证", service["name"], "failed", str(exc))
            raise FleetError(str(exc)) from exc
        state = output["output_status"]
        detail = {"complete": "收到非流式文本响应", "truncated": "达到原生生成上限；文本未完整结束",
                  "incomplete": f"原生响应以 {output['finish_reason']} 结束，未确认正常完成",
                  "reasoning_only": "收到思考内容，尚无最终文本回答", "empty": "原生响应没有文本或思考内容，输出验收未通过"}[state]
        store.audit("推理验证", service["name"], "success" if state == "complete" else "failed" if state == "empty" else "partial", detail,
                    metadata={key: output[key] for key in ("response_id", "model", "finish_reason", "output_status", "truncated") if key in output})
        return {**output, "latency_ms": round((time.monotonic() - begin) * 1000, 1),
                "usage": result.get("usage", {}), "endpoint": service["endpoint"]}


    @application.get("/api/images", dependencies=secured)
    def images():
        if selected_mode == "demo": return {"items": [{"id": "demo-image-1", "node_id": "demo-node-1", "node_name": "ascend-a3-01", "tags": ["mindie-motor:demo-a3"], "size": 18500000000, "architecture": "arm64", "demo": True}], "errors": [], "unsupported_nodes": []}
        items, errors, unsupported_nodes = [], [], []
        for node in store.list("nodes"):
            if node.get("docker_available") is False:
                unsupported_nodes.append({"node_id": node["id"], "node_name": node["name"],
                    "reason": "此节点不提供 Docker 镜像库存；Kubernetes 镜像通过集群或镜像仓库管理。"})
                continue
            try:
                response = fleet.call(node, "GET", "/agent/images", timeout=30)
                for item in (response if isinstance(response, list) else response.get("items", [])):
                    items.append({**item, "node_id": node["id"], "node_name": node["name"]})
            except FleetError as error:
                detail = str(error)
                prefix = node["name"] + ": "
                errors.append(redact(detail if detail.startswith(prefix) else prefix + detail))
        return {"items": items, "errors": errors, "unsupported_nodes": unsupported_nodes}

    @application.get("/api/jobs/{identifier}", dependencies=secured)
    def job_status(identifier: str, request: Request):
        result = store.get("jobs", identifier)
        if result is None: raise HTTPException(404, "任务不存在")
        return redact({key: value for key, value in result.items() if key not in {"plan", "scale_plan"}}) if role_for_request(request) == "viewer" else result

    @application.get("/api/jobs", dependencies=secured)
    def job_history(request: Request, service_id: str | None = None):
        items = [job for job in store.list("jobs") if service_id is None or job.get("service_id") == service_id]
        if role_for_request(request) == "viewer": items = [redact({key: value for key, value in job.items() if key not in {"plan", "scale_plan"}}) for job in items]
        return {"items": items}

    @application.get("/api/audit", dependencies=secured)
    def audit_log(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0, le=100000),
                  actor: str | None = None, request_id: str | None = None, action: str | None = None,
                  status: str | None = None, since: str | None = None, until: str | None = None,
                  actor_username: str | None = Query(None, max_length=128)):
        try: return store.query_audit(limit=limit, offset=offset, actor=actor, actor_username=actor_username, request_id=request_id, action=action, status=status, since=since, until=until)
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc

    community.mount(plugins, plugin_context, plugin_cleanup)

    dist = Path(os.getenv("MOTOR_STATIC_DIR", Path(__file__).resolve().parents[1] / "frontend/dist"))
    if configuration.static_enabled and (dist / "assets").is_dir(): application.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @application.get("/{path:path}")
    def frontend(path: str):
        if not configuration.static_enabled:
            raise HTTPException(404, "静态 Web 由独立前端服务提供")
        if path.startswith(("api/", "agent/")): raise HTTPException(404, "接口不存在")
        asset = (dist / path).resolve()
        if asset.is_relative_to(dist.resolve()) and asset.is_file(): return FileResponse(asset)
        index = dist / "index.html"
        if index.is_file(): return FileResponse(index)
        return JSONResponse({"name": PRODUCT_NAME, "detail": "前端尚未构建；开发时运行 npm run dev，或先构建 frontend/dist", "mode": selected_mode})

    plugins.seal()
    return application


app = create_app()
