"""Reference packages: ordinary factories using only ProviderContext SDK v1.

No factory runs until its contract is selected and consumed. Third parties use
different provider IDs, the same context, lifecycle and runtime validators.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import os
from pathlib import Path
import secrets

from .catalogue import CONTRACTS
from .contracts import Provider


class AuditedStore:
    """Delegate critical writes to the selected transactional audit provider."""
    _methods = frozenset({"audit", "audit_items", "query_audit", "prune_audit", "mutate_audited",
                          "mutate_many_audited", "compare_and_mutate_audited"})
    def __init__(self, store, sink):
        self.storage, self.sink = store, sink

    def __getattr__(self, name):
        return getattr(self.sink if name in self._methods else self.storage, name)

    @contextmanager
    def unit_of_work(self, *, write=False):
        with self.storage.unit_of_work(write=write) as unit:
            yield AuditedUnit(unit, self.sink)


class AuditedUnit:
    def __init__(self, unit, sink):
        self._unit = unit
        self.audit = AuditedRepository(unit, sink)
    def __getattr__(self, name): return getattr(self._unit, name)


class AuditedRepository:
    def __init__(self, unit, sink): self._unit, self._sink = unit, sink
    def append(self, event, *, target_snapshot=None):
        return self._sink.append_event(self._unit, event, target_snapshot=target_snapshot)
    def query(self, **options): return self._unit.audit.query(**options)
    def prune(self, **options): return self._unit.audit.prune(**options)


class TransactionalAudit:
    """Reference audit implementation keeps resource writes in the same UoW."""
    def __init__(self, store): self.store = store
    def __getattr__(self, name): return getattr(self.store, name)
    def append_event(self, unit, event, *, target_snapshot=None):
        return unit.audit.append(event, target_snapshot=target_snapshot)


class LocalSessions:
    """Reference session package using only public storage repository ports."""
    def __init__(self, store, settings, legacy_token):
        import time
        self.store, self.settings, self.legacy_token = store, settings, legacy_token
        self.cookie_name, self.clock = settings.cookie_name, time.time
        if settings.allow_legacy_token and len(legacy_token) < 24:
            raise ValueError("Explicit compatibility credentials require at least 24 characters")
    def authenticate(self, request, *, required=True):
        import hashlib, hmac
        from fastapi import HTTPException
        from ..auth import Principal
        raw = request.cookies.get(self.cookie_name)
        if raw and len(raw) <= 256:
            captured = self.clock()
            token_hash = hashlib.sha256(raw.encode()).hexdigest()
            with self.store.unit_of_work(write=True) as unit:
                row = unit.accounts.get_session_account(token_hash).fetchone()
                if row and not row["disabled"] and row["auth_version"] == row["session_version"] and captured < row["expires_at"] and captured - row["last_seen"] < self.settings.idle_timeout:
                    unit.accounts.touch_session(captured, token_hash)
                    principal = Principal(row["id"], row["username"], row["display_name"], row["role"],
                        session_id=token_hash, expires_at=row["expires_at"], auth_source=row["auth_source"])
                    request.state.principal = principal
                    return principal
                unit.accounts.revoke_session(token_hash)
        elif not raw and self.settings.allow_legacy_token:
            supplied = request.headers.get("authorization", "")
            if hmac.compare_digest(supplied.encode(), ("Bearer " + self.legacy_token).encode()):
                principal = Principal("machine:legacy", "legacy-machine", "兼容机器凭据", self.settings.legacy_role,
                                      kind="machine", auth_source="legacy")
                request.state.principal = principal
                return principal
        if required: raise HTTPException(401, "请先登录控制台")
        return None
    def issue(self, unit, user):
        import hashlib
        raw, captured = secrets.token_urlsafe(32), self.clock()
        expires = captured + self.settings.session_ttl
        unit.accounts.prune_expired_sessions(captured, captured - self.settings.idle_timeout)
        unit.accounts.create_session(hashlib.sha256(raw.encode()).hexdigest(), user["id"], user["auth_version"], captured, captured, expires)
        unit.accounts.trim_user_sessions(user["id"])
        return raw, expires
    def revoke(self, unit, token_hash): return unit.accounts.revoke_session(token_hash)
    def revoke_user(self, unit, user_id): return unit.accounts.revoke_user_sessions(user_id)
    def window(self, unit, token_hash): return unit.accounts.get_session_window(token_hash).fetchone()
    def csrf(self, raw):
        import hashlib, hmac
        return hmac.new(raw.encode(), b"motor-console-csrf-v1", hashlib.sha256).hexdigest()
    def set_cookie(self, response, raw):
        response.set_cookie(self.cookie_name, raw, max_age=self.settings.session_ttl,
            secure=self.settings.secure_cookie, httponly=True, samesite="lax", path="/")
        response.headers["Cache-Control"] = "no-store"
    def clear_cookie(self, response):
        response.delete_cookie(self.cookie_name, path="/", secure=self.settings.secure_cookie, httponly=True, samesite="lax")
    def check_origin(self, request):
        from urllib.parse import urlsplit
        from fastapi import HTTPException
        origin = request.headers.get("origin")
        if origin is None:
            parsed = urlsplit(request.headers.get("referer", ""))
            origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        accepted = self.settings.trusted_origins or (str(request.base_url).rstrip("/"),)
        if not origin or origin == "null" or origin not in accepted or request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "请求来源校验失败")
    def check_csrf(self, request):
        import hmac
        from fastapi import HTTPException
        self.check_origin(request)
        expected = self.csrf(request.cookies.get(self.cookie_name, ""))
        if not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), expected.encode()):
            raise HTTPException(403, "CSRF 校验失败，请刷新页面后重试")


class RolePolicy:
    """Reference policy depends only on the selected Session v1 contract."""
    def __init__(self, session): self.session = session
    def authorize(self, request, allowed_roles=frozenset({"admin", "operator", "viewer"}), *, csrf=True, account_only=False):
        from fastapi import HTTPException
        from .authorization import authenticate_session
        principal = authenticate_session(self.session, request)
        if principal.role not in allowed_roles or account_only and principal.kind != "session":
            raise HTTPException(403, "当前账号无权执行此操作")
        if csrf and request.method not in {"GET", "HEAD", "OPTIONS"} and principal.kind == "session":
            self.session.check_csrf(request)
        return principal


class ReferenceNativeAdapter:
    def public_profile(self):
        from ..compatibility import public_profile
        return public_profile()
    def check_binding(self, profile_id, protocol=None):
        from ..compatibility import check_binding
        return check_binding(profile_id, protocol)
    def agent_metadata(self):
        from ..compatibility import agent_metadata
        return agent_metadata()
    def render_kubernetes(self, context, payload, namespace, revision):
        from .native_reference import render_kubernetes
        return render_kubernetes(context, payload, namespace, revision)
    def render_kubernetes_scale(self, context, baseline, target, document, service_id, revision):
        from .native_reference import render_kubernetes_scale
        return render_kubernetes_scale(context, baseline, target, document, service_id, revision)
    def upstream_path(self, directory=None):
        from ..motor_adapter import upstream_path
        return upstream_path(directory)
    def official_helpers(self, directory=None):
        from ..motor_adapter import official_helpers
        return official_helpers(directory)
    def validate_layout(self, config, placements, **options):
        from ..motor_adapter import validate_layout
        return validate_layout(config, placements, **options)
    def build_container_spec(self, payload, upstream, workspace):
        from ..motor_adapter import build_container_spec
        return build_container_spec(payload, upstream, workspace)
    def verify_upstream_source(self, upstream):
        from ..motor_adapter import verify_upstream_source
        return verify_upstream_source(upstream)
    def native_contract_checks(self, config, env, **options):
        from ..native_contracts import native_contract_checks
        return native_contract_checks(config, env, **options)


class ResourceAdmission:
    def check_physical_usage(self, context, requested, owned):
        from .admission_reference import check_physical_usage
        return check_physical_usage(context, requested, owned)
    def restart(self, context, daemon, owned, containers, operation_id):
        from .admission_reference import restart
        return restart(context, daemon, owned, containers, operation_id)


class AgentIdentity:
    def validate(self):
        from ..agent_credentials import validate_agent_credentials
        return validate_agent_credentials()
    def authorize(self, authorization):
        from ..agent_credentials import authorize_agent
        return authorize_agent(authorization)
    def metadata(self):
        from ..agent_credentials import agent_credential_metadata
        return agent_credential_metadata()


def application(ctx):
    from ..default_console import assemble_reference_application
    return assemble_reference_application(ctx)


def storage(ctx):
    from ..store import Store
    configuration = ctx.settings["configuration"]
    value = Store(ctx.settings["data_dir"], backend=configuration.database_backend,
                  database_url=configuration.database_url)
    ctx.on_shutdown(value.close)
    return value


def secret_store(ctx):
    from ..fleet import Secrets
    seed = os.getenv("MOTOR_SECRET_KEY") or ctx.settings["console_token"]
    if not seed:
        directory = Path(ctx.settings["data_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ".secret-key"
        if not path.exists():
            try:
                with path.open("x", encoding="utf-8") as output: output.write(secrets.token_urlsafe(40))
                path.chmod(0o600)
            except FileExistsError: pass
            except OSError:
                if not path.exists(): raise
        seed = path.read_text(encoding="utf-8")
    value = Secrets(seed)
    # The reference backup adapter needs its own key material. This is public
    # provider state, not a requirement that all secret implementations expose
    # raw encryption keys: third-party maintenance uses its selected contract.
    value.backup_seed = seed
    from ..observability import register_secret
    register_secret(seed)
    return value


def audit(ctx): return TransactionalAudit(ctx.consume("storage.store"))


def logs(ctx):
    from ..observability import JsonLogStore
    from ..version import PRODUCT_ID
    configuration = ctx.settings["configuration"]
    value = JsonLogStore(configuration.logs, max_bytes=configuration.log_segment_bytes,
                         backup_count=configuration.log_rotations, service_name=PRODUCT_ID)
    ctx.on_shutdown(value.close)
    return value


def identity(ctx):
    from ..auth import AuthManager, AuthSettings
    store = AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink"))
    return AuthManager(store, settings=AuthSettings.from_env(), legacy_token=ctx.settings["console_token"],
                       audit_provider=ctx.consume("audit.sink"))


def session(ctx):
    from ..auth import AuthSettings
    return LocalSessions(ctx.consume("storage.store"), AuthSettings.from_env(), ctx.settings["console_token"])
def policy(ctx): return RolePolicy(ctx.consume("security.session"))


def scheduler(ctx):
    from concurrent.futures import ThreadPoolExecutor
    value = ThreadPoolExecutor(max_workers=8, thread_name_prefix="motor-console-job")
    ctx.on_shutdown(value.shutdown, wait=True, cancel_futures=True)
    return value


def task_recovery(ctx):
    from .task_reference import reconcile_restart
    return reconcile_restart


def demo_seed(ctx):
    from ..demo import seed
    return seed


def connection(ctx):
    from ..plugin_execution import PluginFleet
    value = PluginFleet(ctx.consume("security.secrets"), ctx.require("registry"))
    value.provider_generation = ctx.generation
    return value


def inventory(ctx):
    from ..node_inventory_sampling import NodeInventorySampler
    value = NodeInventorySampler(AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink")),
                                 ctx.consume("agent.connection"), ctx.require("mutation_lock"), mode=ctx.settings["mode"])
    ctx.on_shutdown(value.close)
    return value


def health(ctx):
    from ..service_health import DockerServiceObserver
    value = DockerServiceObserver(ctx.consume("agent.connection"), upstream_dir=ctx.settings["upstream_dir"])
    ctx.on_shutdown(value.close)
    return value


def metrics(ctx):
    from ..service_metrics import ServiceMetricsSampler
    value = ServiceMetricsSampler()
    ctx.on_shutdown(value.close)
    return value


def runtime_logs(ctx):
    from ..log_records import ServiceLogReader
    value = ServiceLogReader()
    ctx.on_shutdown(value.close)
    return value


def resource_inventory(ctx):
    from ..resource_inventory import inventory_view
    return inventory_view


def template_catalog(ctx):
    from ..catalog import templates
    return templates(ctx.settings["upstream_dir"])


def docker(ctx):
    from ..docker_runtime import DockerRuntime
    return DockerRuntime(upstream_dir=ctx.settings["upstream_dir"], native_adapter=ctx.consume("motor.native"),
                         ledger_factory=ctx.consume("resources.ledger"), host_telemetry=ctx.consume("telemetry.host"),
                         admission=ctx.consume("resources.admission"),
                         provider_generation=ctx.generation, provider_binding=ctx.binding("runtime.docker"))


def kubernetes(ctx):
    from ..kubernetes_runtime import KubernetesRuntime
    return KubernetesRuntime(upstream_dir=ctx.settings["upstream_dir"], native_adapter=ctx.consume("motor.native"),
                             provider_generation=ctx.generation, provider_binding=ctx.binding("runtime.kubernetes"))


def grafana(ctx):
    from ..extensions import GrafanaIntegration
    return GrafanaIntegration(AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink")),
                              ctx.consume("security.secrets"), ctx.require("mutation_lock"), ctx.settings["upstream_dir"])


def maintenance(ctx):
    from ..system import SystemManager
    value = SystemManager(AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink")),
                          ctx.consume("logs.platform"), mode=ctx.settings["mode"],
                          configuration=ctx.settings["configuration"],
                          secret_seed=getattr(ctx.consume("security.secrets"), "backup_seed", ""),
                          native_adapter=ctx.consume("motor.native"))
    return value


def branding(ctx):
    from ..branding import BrandingManager
    return BrandingManager(AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink")), ctx.require("mutation_lock"))


def ui_assets(ctx):
    from ..ui_plugin_assets import create_ui_plugin_router
    return create_ui_plugin_router


def packages(ctx):
    from ..plugin_host.community import CommunityRuntime
    return CommunityRuntime(ctx.settings["data_dir"], mode=ctx.settings["mode"],
                            storage_backend=ctx.settings["configuration"].database_backend,
                            prepared_profiles=ctx.require("legacy_profiles"))


def host_telemetry(ctx):
    from ..resource_telemetry import collect_resource_telemetry
    return collect_resource_telemetry


def ledger(ctx):
    from ..docker_agent_state import DockerAgentState
    def create(directory, **options):
        return DockerAgentState(directory, **options, provider_generation=ctx.generation,
                                provider_binding=ctx.binding("runtime.docker"))
    return create


def placement(ctx):
    from ..placement_recommendations import recommend_placements
    return recommend_placements


def features(ctx):
    from ..catalog import capabilities
    adapter = ctx.consume("motor.native")
    return lambda upstream: capabilities(upstream, native_profile=adapter.public_profile())


def routes(ctx):
    """The reference HTTP module implementation, selected like installed code."""
    return ctx.require("reference_routes")


def service_workflow(ctx):
    from ..reference_modules.services_workflow import factory
    return factory(ctx)


def task_coordinator(ctx):
    from ..reference_modules.services_tasks import factory
    return factory(ctx)


def service_api(ctx):
    from ..reference_modules.services_api import factory
    return factory(ctx)


def console_api(ctx):
    from ..reference_modules.console_api import factory
    return factory(ctx)


def agent_api(ctx):
    from ..reference_modules.services_agents import factory
    return factory(ctx)


API_FACTORIES = {
    "api.agent-docker": (agent_api, ("runtime.docker", "runtime.kubernetes", "telemetry.host", "security.agent", "motor.native")),
    "api.agent-kubernetes": (agent_api, ("runtime.kubernetes", "security.agent")),
    "api.auth": (console_api, ("security.identity",)),
    "api.accounts": (console_api, ("security.identity",)),
    "api.nodes": (console_api, ("storage.store", "audit.sink", "agent.connection", "telemetry.inventory")),
    "api.templates": (console_api, ("storage.store", "audit.sink", "templates.catalog", "motor.native")),
    "api.system": (console_api, ("storage.store", "audit.sink", "maintenance.manager", "branding.manager")),
    "api.extensions": (console_api, ("storage.store", "audit.sink", "agent.connection", "integration.grafana")),
    "api.plugins": (console_api, ("storage.store", "audit.sink", "plugins.packages")),
    "api.services": (service_api, ("storage.store", "audit.sink", "agent.connection", "services.workflow", "tasks.coordinator", "resources.placement")),
    "api.metrics": (service_api, ("storage.store", "audit.sink", "agent.connection", "services.workflow", "telemetry.service-health", "telemetry.service-metrics")),
    "api.runtime-logs": (service_api, ("storage.store", "audit.sink", "agent.connection", "services.workflow", "logs.runtime")),
    "api.gateways": (service_api, ("storage.store", "audit.sink", "agent.connection", "services.workflow")),
    "api.tasks": (service_api, ("storage.store", "audit.sink", "tasks.coordinator", "agent.connection")),
    "api.overview": (console_api, ("storage.store", "audit.sink", "resources.inventory")),
    "api.images": (console_api, ("storage.store", "audit.sink", "agent.connection", "motor.native")),
    "api.audit": (console_api, ("storage.store", "audit.sink")),
    "api.frontend": (console_api, ()),
}

def reference_providers():
    definitions = {
        "console.application": (application, tuple(name for name in CONTRACTS if name != "console.application")),
        "storage.store": (storage, ()), "security.secrets": (secret_store, ()),
        "audit.sink": (audit, ("storage.store",)), "logs.platform": (logs, ()),
        "security.identity": (identity, ("storage.store", "audit.sink")),
        "security.session": (session, ("storage.store",)),
        "security.policy": (policy, ("security.session",)),
        "security.agent": (lambda ctx: AgentIdentity(), ()),
        "services.workflow": (service_workflow, ("storage.store", "audit.sink", "logs.platform", "agent.connection", "motor.native")),
        "tasks.coordinator": (task_coordinator, ("storage.store", "audit.sink", "logs.platform", "agent.connection", "services.workflow", "tasks.scheduler", "tasks.recovery")),
        "tasks.scheduler": (scheduler, ()), "agent.connection": (connection, ("security.secrets",)),
        "tasks.recovery": (task_recovery, ()), "demo.seed": (demo_seed, ()),
        "telemetry.inventory": (inventory, ("storage.store", "audit.sink", "agent.connection")),
        "telemetry.service-health": (health, ("agent.connection",)),
        "telemetry.service-metrics": (metrics, ()), "logs.runtime": (runtime_logs, ()),
        "telemetry.host": (host_telemetry, ()), "resources.ledger": (ledger, ()),
        "resources.placement": (placement, ()), "features.catalog": (features, ("motor.native",)),
        "resources.admission": (lambda ctx: ResourceAdmission(), ()),
        "resources.inventory": (resource_inventory, ()), "motor.native": (lambda ctx: ReferenceNativeAdapter(), ()),
        "templates.catalog": (template_catalog, ()),
        "runtime.docker": (docker, ("motor.native", "resources.ledger", "resources.admission", "telemetry.host")), "runtime.kubernetes": (kubernetes, ("motor.native",)),
        "integration.grafana": (grafana, ("storage.store", "audit.sink", "security.secrets")),
        "maintenance.manager": (maintenance, ("storage.store", "audit.sink", "security.secrets", "logs.platform", "motor.native")),
        "branding.manager": (branding, ("storage.store", "audit.sink")), "console.ui-assets": (ui_assets, ()),
        "plugins.packages": (packages, ()),
    }
    for name in CONTRACTS:
        if name.startswith("api."):
            definitions[name] = API_FACTORIES[name]
    from ..version import CONSOLE_VERSION
    source_files = {
        "console.application": ("default_console.py", "provider_host/http.py"),
        "services.workflow": ("reference_modules/services_workflow.py",),
        "tasks.coordinator": ("reference_modules/services_tasks.py", "reference_modules/services_workflow.py"),
        "storage.store": ("store.py", "ports/storage.py", "storage"),
        "security.secrets": ("fleet.py",), "security.identity": ("auth.py",),
        "security.agent": ("agent_credentials.py",), "audit.sink": ("store.py",),
        "logs.platform": ("observability.py",), "logs.runtime": ("log_records.py",),
        "agent.connection": ("plugin_execution.py", "fleet.py"),
        "tasks.recovery": ("provider_host/task_reference.py",), "demo.seed": ("demo.py",),
        "telemetry.inventory": ("node_inventory_sampling.py",),
        "telemetry.service-health": ("service_health.py",),
        "telemetry.service-metrics": ("service_metrics.py",), "telemetry.host": ("resource_telemetry.py", "node_exporter.py"),
        "resources.inventory": ("resource_inventory.py",), "resources.ledger": ("docker_agent_state.py",),
        "resources.placement": ("placement_recommendations.py",), "resources.admission": ("provider_host/admission_reference.py",),
        "motor.native": ("motor_adapter.py", "native_contracts.py", "motor_source_manifest.json", "compatibility.py", "provider_host/native_reference.py"),
        "features.catalog": ("catalog.py",), "templates.catalog": ("catalog.py",),
        "runtime.docker": ("docker_runtime.py", "agent_capacity.py", "execution_policy.py"),
        "runtime.kubernetes": ("kubernetes_runtime.py", "kubernetes_autoscaling.py", "kubernetes_role_stop.py", "agent_capacity.py", "execution_policy.py"),
        "integration.grafana": ("extensions.py",), "maintenance.manager": ("system.py",),
        "branding.manager": ("branding.py",), "console.ui-assets": ("ui_plugin_assets.py", "ui_plugin_packages.py"),
        "plugins.packages": ("plugin_host/community.py", "plugin_host/packages"),
    }
    source_types = {"security.session": LocalSessions, "security.policy": RolePolicy,
                    "security.agent": AgentIdentity, "audit.sink": TransactionalAudit,
                    "motor.native": ReferenceNativeAdapter, "resources.admission": ResourceAdmission}
    root = Path(__file__).resolve().parent.parent
    api_paths = tuple(root.rglob("*.py"))
    source_cache = {}
    def source(path):
        if path not in source_cache:
            source_cache[path] = path.read_bytes()
        return source_cache[path]
    def artifact(name, factory):
        digest = hashlib.sha256(inspect.getsource(factory).encode("utf-8"))
        if name in source_types:
            digest.update(inspect.getsource(source_types[name]).encode("utf-8"))
        import motor_console_sdk
        sdk_root = Path(motor_console_sdk.__file__).resolve().parent
        # Critical compatibility follows only the public contracts used by this
        # product; UI and unrelated SDK additions do not invalidate live work.
        sdk_sources = {"storage.store": ("storage.py",), "services.workflow": ("workflows.py",),
            "tasks.coordinator": ("workflows.py",)}
        for filename in sdk_sources.get(name, ()):
            digest.update(filename.encode("utf-8") + b"\0" + (sdk_root / filename).read_bytes())
        if name == "storage.store":
            from motor_console_sdk import StorageV1
            digest.update(inspect.getsource(StorageV1).encode("utf-8"))
        if name in {"motor.native", "runtime.kubernetes", "runtime.docker", "resources.ledger"}:
            from motor_console_sdk import AdmissionContextV1, NativeRenderContextV1
            for dto in (AdmissionContextV1, NativeRenderContextV1):
                digest.update(inspect.getsource(dto).encode("utf-8"))
        paths = api_paths if name.startswith("api.") else [root / item for item in source_files.get(name, ())]
        expanded = []
        for path in paths:
            expanded.extend(path.rglob("*.py") if path.is_dir() else [path])
        for path in sorted(set(expanded)):
            digest.update(str(path.relative_to(root)).replace("\\", "/").encode("utf-8") + b"\0")
            digest.update(source(path))
        return digest.hexdigest()
    return tuple(Provider("reference." + name, CONSOLE_VERSION, (name,), factory, dependencies, artifact(name, factory))
                 for name, (factory, dependencies) in definitions.items())
