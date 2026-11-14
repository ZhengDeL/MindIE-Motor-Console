"""Containerized Motor control plane; Docker and Kubernetes execution stay on agents."""
from __future__ import annotations

from contextlib import asynccontextmanager, ExitStack
import json
import os
from pathlib import Path
import threading
import time
import re
import sqlite3
import sys
import traceback
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from .fleet import FleetError
from .compatibility import CompatibilityError, check_binding, public_profile
from .maintenance import MaintenanceError, ProcessLease
from .observability import redact, register_secret, request_context
from .settings import documentation_url, load_secret_files, validate_production_settings
from .version import AGENT_PROTOCOL_VERSION, CONSOLE_VERSION, PRODUCT_NAME, PRODUCT_ID, AGENT_PROTOCOL_HEADER, CONSOLE_PROTOCOL_HEADER, PRODUCT_HEADER
from .http_middleware import RequestBodyLimit
from .template_library import TEMPLATE_FORMAT
from .agent_credentials import AgentCredentialError
from .agent_security import agent_tls_context
from .platform_configuration import PlatformConfiguration
from .ports.storage import RevisionConflict, StorageUnavailable
from .branding import branding_icon_read, public_branding_read
from .plugin_host import build_registry
from .plugin_host.providers import default_branding
from .plugin_execution import require_plugin, require_runtime
from .plugin_host.packages import MAX_PACKAGE_BYTES, PluginPackageError
from .plugin_host import PluginConfigurationError
from .reference_modules.services_models import (ServiceInput, InferenceInput, ImageInspectionInput,
                                                DeploymentApproval, ScaleInput, UndeployInput)


def assemble_reference_application(providers) -> FastAPI:
    """Selected reference application plugin; dependencies are SDK bindings."""
    mode = providers.settings["mode"]
    data_dir = providers.settings["data_dir"]
    upstream_dir = providers.settings["upstream_dir"]
    console_token = providers.settings["console_token"]
    startup = providers.settings["startup"]
    load_secret_files()
    selected_mode = mode or os.getenv("MOTOR_MODE", "demo")
    if selected_mode not in {"demo", "control", "agent"}: raise ValueError("MOTOR_MODE must be demo, control or agent")
    token = console_token if console_token is not None else os.getenv("MOTOR_CONSOLE_TOKEN", "")
    agent_policy = providers.consume("security.agent") if selected_mode == "agent" else None
    if selected_mode == "agent":
        agent_policy.validate()
        from .agent_capacity import native_capacity
        native_capacity()
    if selected_mode == "control": agent_tls_context()
    validate_production_settings(selected_mode)
    upstream = Path(upstream_dir or os.getenv("MOTOR_UPSTREAM_DIR", Path(__file__).resolve().parents[2] / "mindie-motor/upstream"))
    directory = Path(data_dir or os.getenv("MOTOR_DATA_DIR", Path(__file__).resolve().parents[1] / ".data"))
    configuration = PlatformConfiguration.from_env(directory)
    community = providers.consume("plugins.packages", legacy_profiles=providers.require("legacy_profiles"))
    plugins = build_registry(storage_backend=configuration.database_backend, extra_manifests=community.declarations())
    plugin_cleanup = ExitStack()
    lease = ProcessLease(directory, name=selected_mode).acquire() if selected_mode in {"control", "agent"} else None
    if lease: startup.callback(lease.release)
    store = providers.consume("storage.store")
    from .provider_host.defaults import AuditedStore
    audit_sink = providers.consume("audit.sink")
    store = AuditedStore(store, audit_sink)
    logs = providers.consume("logs.platform")
    session_provider = policy_provider = None
    auth = providers.consume("security.identity") if selected_mode == "control" else None
    if auth:
        session_provider = providers.consume("security.session")
        policy_provider = providers.consume("security.policy")
        # The identity implementation explicitly accepts the selected session
        # and authorization contracts; the reference account APIs use them too.
        configure = getattr(auth, "bind_security", None)
        if not callable(configure):
            raise PluginConfigurationError("Identity provider must implement bind_security(session, policy)")
        configure(session_provider, policy_provider)
    if auth and os.getenv("MOTOR_ENV") == "production" and auth.setup_required() and not auth.settings.bootstrap_token:
        raise RuntimeError("First administrator setup requires an independently generated MOTOR_BOOTSTRAP_TOKEN_FILE")
    secret_provider = providers.consume("security.secrets")
    seed = getattr(secret_provider, "backup_seed", "")
    fleet = providers.consume("agent.connection", registry=plugins)
    for secret in (token, os.getenv("MOTOR_BOOTSTRAP_TOKEN", ""), os.getenv("MOTOR_AGENT_TOKEN", "")): register_secret(secret)
    mutation_lock = threading.RLock()
    inventory_sampler = providers.consume("telemetry.inventory", mutation_lock=mutation_lock) if providers.is_reference("api.nodes") and plugins.enabled("nodes") else None
    native_adapter = providers.consume("motor.native")
    public_profile, check_binding = native_adapter.public_profile, native_adapter.check_binding
    feature_catalog = providers.consume("features.catalog")
    rollout_manager = None
    template_list = providers.consume("templates.catalog")
    if selected_mode == "demo": providers.consume("demo.seed")(store, template_list)
    from .provider_host.generation import checkpoint
    def checkpoint_providers(**options):
        checkpoint(directory, providers.describe(), store, **options)
    from .provider_host.generation import record_agent_operation
    def operation_tracker(phase, runtime, action, payload, result=None):
        return record_agent_operation(directory, providers.describe(), phase, runtime, action, payload, result)
    checkpoint_providers()

    @asynccontextmanager
    async def lifespan(_app):
        try:
            if inventory_sampler is not None:
                inventory_sampler.start()
            yield
        finally:
            # ExitStack attempts every disposer even if one plugin fails to
            # close. The same ownership covers failed assembly and shutdown.
            _app.state.resource_lifetime.close()

    from .provider_host.http import ProviderApplication, FRONTEND_METHODS
    from .provider_host.catalogue import route_contract
    application = ProviderApplication(title=PRODUCT_NAME, version=CONSOLE_VERSION, lifespan=lifespan,
                          docs_url="/api/docs" if selected_mode == "demo" else None,
                          redoc_url=None, openapi_url="/api/openapi.json" if selected_mode == "demo" else None)
    application.state.store, application.state.fleet = store, fleet
    application.state.mode, application.state.upstream = selected_mode, upstream
    application.state.inventory_sampler = inventory_sampler
    application.state.service_metrics_sampler = None
    application.state.service_log_reader = None
    application.state.auth, application.state.logs, application.state.lease = auth, logs, lease
    application.state.log_health = True
    application.state.operation_tracker = operation_tracker
    application.state.provider_generation = providers.generation
    application.state.providers = providers.describe()
    application.state.configuration = configuration
    application.state.plugins = plugins
    application.state.community_plugins = community
    startup.callback(plugin_cleanup.close)

    def authorized(request: Request) -> bool:
        return selected_mode == "demo" or session_provider is not None and authenticate_session(session_provider, request, required=False) is not None

    def require_auth(request: Request):
        if not authorized(request): raise HTTPException(401, "请先登录控制台")

    secured = [Depends(require_auth)]

    def role_for_request(request: Request):
        if selected_mode == "demo": return "demo"
        principal = getattr(request.state, "principal", None)
        return principal.role if principal else None

    from .ports.services import ServiceWorkflowInputsV1, TaskCoordinatorInputsV1, ReferenceApiInputsV1

    def mark_unhealthy():
        application.state.log_health = False

    workflow_inputs = ServiceWorkflowInputsV1(selected_mode, upstream, mutation_lock,
        checkpoint_providers, mark_unhealthy, lambda runtime: require_runtime(plugins, runtime))
    coordinator_inputs = TaskCoordinatorInputsV1(selected_mode, mutation_lock,
        checkpoint_providers, mark_unhealthy, providers.generation,
        lambda runtime: providers.binding("runtime." + runtime), workflow_inputs,
        execution_enabled=providers.is_reference("api.services"))
    application.state.reference_api_inputs = ReferenceApiInputsV1(
        workflow_inputs, coordinator_inputs, tuple(secured), role_for_request)

    from .reference_modules.services_legacy import LegacyPluginContext, LegacyPublications, LEGACY_API_CONTRACTS
    plugin_context = LegacyPluginContext(application=application, services={
        "store": store, "mutation_lock": mutation_lock, "fleet": fleet, "mode": selected_mode,
        "upstream": upstream, "secured": secured, "role_for_request": role_for_request,
        "inventory_sampler": inventory_sampler, "template_list": template_list,
        "placement_recommender": providers.consume("resources.placement"),
        "native_adapter": native_adapter,
    }, registry=plugins, cleanup=plugin_cleanup)
    legacy_publications = LegacyPublications(plugin_context, providers.describe()["bindings"])
    plugin_context.legacy_publications = legacy_publications
    application.state.legacy_publications = legacy_publications
    branding_manager = None
    if selected_mode != "agent" and plugins.enabled("branding"):
        branding_manager = providers.consume("branding.manager", mutation_lock=mutation_lock)
        legacy_publications.publish_selected("branding.manager", "branding.manager", branding_manager)
    application.state.branding = branding_manager


    from .ui_plugin_assets import public_ui_plugin_read
    from .provider_host.authorization import account_path, public_auth_request, authenticate_session
    body_limits = {"/api/plugin-packages/import": MAX_PACKAGE_BYTES,
                   "/api/ui-plugins/packages/import": MAX_PACKAGE_BYTES}
    if selected_mode != "agent":
        # Authorize control-plane requests before buffering uploaded code. The
        # Agent still needs its byte limit outside its JSON protocol checks.
        application.add_middleware(RequestBodyLimit, logs=logs, path_limits=body_limits)

    @application.middleware("http")
    async def bounds(request: Request, call_next):
        tracked_operation = None
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
                if route_contract(request.url.path) == "api.frontend" and method not in FRONTEND_METHODS:
                    raise HTTPException(405, "前端插件只提供 GET/HEAD；写操作必须使用受保护 API", headers={"Allow": "GET, HEAD"})
                # Block before authentication, flow consumption or IdP traffic:
                # a later backup restore must not undo a successful credential,
                # permission or session-revocation change during maintenance.
                if selected_mode == "control" and auth_path and auth_write and (directory / "maintenance.json").exists():
                    raise HTTPException(503, "平台正在维护，请稍后重试账号操作")
                if (selected_mode == "control" and request.url.path.startswith("/api/")
                        and not public_auth_request(request.url.path, request.method)
                        and not (request.url.path == "/api/meta" and method in {"GET", "HEAD"})
                        and not public_branding_read(request.url.path, request.method)
                        and not public_ui_plugin_read(request.url.path, request.method)):
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
                    if path == "/api/plugin-packages" or path.startswith(("/api/plugin-packages/", "/api/ui-plugins")):
                        roles = {"admin"}
                    external_roles = community.roles_for(plugins.owner_for_path(path),
                        write=method not in {"GET", "HEAD", "OPTIONS"})
                    if external_roles is not None:
                        roles = external_roles
                    if path.startswith("/api/services/") and path.endswith(("/export", "/revisions")): roles = {"admin", "operator"}
                    account_only = auth_path
                    if auth_path:
                        # Account administration is always an admin browser
                        # session. Other non-public auth operations are browser
                        # self-service, including any unknown extension path.
                        roles = {"admin"} if account_path(path) else {"admin", "operator", "viewer"}
                    authenticated = authenticate_session(session_provider, request, required=True)
                    request.state.principal = authenticated
                    principal = policy_provider.authorize(request, roles, account_only=account_only)
                    if principal is None:
                        raise HTTPException(503, {"code": "invalid_authorization_result", "message": "所选授权提供者未返回有效身份，已拒绝请求"})
                    if (getattr(principal, "role", None) not in roles
                            or account_only and getattr(principal, "kind", None) != "session"):
                        raise HTTPException(403, "此身份无权执行此操作")
                    if method not in {"GET", "HEAD", "OPTIONS"} and (directory / "maintenance.json").exists():
                        raise HTTPException(503, "平台正在维护，暂停新的配置和部署操作")
                    if method not in {"GET", "HEAD", "OPTIONS"} and not application.state.log_health:
                        raise HTTPException(503, "平台日志存储不可用，暂停写操作，请由运维恢复存储")
                else:
                    principal = authenticate_session(session_provider, request, required=False) if session_provider is not None else None
                if principal is not None:
                    if (not isinstance(getattr(principal, "user_id", None), str) or not principal.user_id
                            or getattr(principal, "role", None) not in {"admin", "operator", "viewer"}
                            or getattr(principal, "kind", None) not in {"session", "machine"}):
                        raise HTTPException(503, {"code": "invalid_identity_result", "message": "所选身份提供者返回了不兼容身份，已拒绝请求"})
                    request.state.principal = principal
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
                if (selected_mode == "agent" and request.url.path.startswith("/agent/")
                        and not (request.url.path == "/agent/health" and method in {"GET", "HEAD"})):
                    agent_policy.authorize(request.headers.get("authorization"))
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
                    if request.url.path not in {"/agent/preflight", "/agent/kubernetes/plan", "/agent/kubernetes/inference"} and not (
                            request.url.path == "/agent/kubernetes/scale" and body.get("dry_run") is True):
                        from .execution_policy import require_mutations, AgentReadOnlyError
                        try: require_mutations()
                        except AgentReadOnlyError as error: raise HTTPException(403, str(error)) from error
                    action = request.url.path.rstrip("/").rsplit("/", 1)[-1]
                    if action in {"deploy", "stop", "restart", "remove", "scale", "pull"}:
                        runtime = "kubernetes" if request.url.path.startswith("/agent/kubernetes/") else "docker"
                        tracked_action = "image_" + action if request.url.path.startswith("/agent/images/") else action
                        tracked_operation = (runtime, tracked_action, body)
                with request_context(actor=principal.user_id if principal else "demo" if selected_mode == "demo" else "anonymous", request_id=request_id):
                    if request.method not in {"GET", "HEAD", "OPTIONS"}:
                        from .ui_plugin_assets import guard_ui_generation
                        guard_ui_generation(request)
                        expected_generation = request.headers.get("X-Motor-Expected-Generation", "")
                        if expected_generation and expected_generation != providers.generation:
                            raise HTTPException(409, {"code": "provider_generation_mismatch", "message": "提供者代次已变化，请保存草稿并刷新"})
                    if tracked_operation is not None:
                        operation_tracker("begin", *tracked_operation)
                    audited_request = selected_mode == "control" and request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}
                    if audited_request:
                        # Record admission before any selected HTTP module can
                        # act. Domain modules additionally commit their own
                        # transactional audit/outbox with persistent changes.
                        audit_sink.audit("api.write.admitted", request.url.path, "accepted",
                            metadata={"method": request.method, "provider_generation": providers.generation})
                    response = await call_next(request)
                    if audited_request:
                        try:
                            audit_sink.audit("api.write.result", request.url.path, "success" if response.status_code < 400 else "rejected",
                                metadata={"method": request.method, "status_code": response.status_code,
                                          "provider_generation": providers.generation})
                        except Exception as error:
                            application.state.log_health = False
                            raise HTTPException(503, {"code": "operation_audit_unavailable", "outcome_unknown": True,
                                "message": "操作结果的审计无法确认；请按请求编号核对，勿自动重放"}) from error
                    if selected_mode != "agent" and request.method not in {"GET", "HEAD", "OPTIONS"} and response.status_code < 400:
                        checkpoint_providers()
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
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        response.headers["X-Frame-Options"] = "DENY"
        if os.getenv("MOTOR_ENV") == "production": response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers[PRODUCT_HEADER] = PRODUCT_ID
        response.headers["X-Motor-Provider-Generation"] = providers.generation
        if selected_mode == "agent": response.headers[AGENT_PROTOCOL_HEADER] = str(AGENT_PROTOCOL_VERSION)
        if request.url.path.startswith("/api/") and not (branding_icon_read(request.url.path, request.method)
                and response.status_code in {200, 304}) and not (request.url.path.startswith("/api/ui-plugins/assets/") and response.status_code in {200, 304}):
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
                policy = agent_policy.metadata()
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
                "capabilities": feature_catalog(upstream) if plugins.enabled("features") else [],
                "runtimes": plugins.enabled_runtimes(), "agent_protocol": AGENT_PROTOCOL_VERSION, "motor_profile": public_profile()}

    if selected_mode == "agent":
        application.state.docker_runtime, application.state.kubernetes_runtime = None, None
        providers.select_api_routes(application)
        plugins.seal()
        return application

    application.state.system = None
    application.state.agent_rollouts = None
    ui_router = providers.consume("console.ui-assets")(directory=directory, mode=selected_mode,
                                                       store=store, role_for_request=role_for_request)
    application.include_router(ui_router)
    application.state.ui_generation = getattr(ui_router, "ui_generation", "")
    providers.prepare_api_routes(application, LEGACY_API_CONTRACTS)
    legacy_publications.finalize()
    community.mount(plugins, plugin_context, plugin_cleanup)


    providers.select_api_routes(application)
    plugins.seal()
    return application
