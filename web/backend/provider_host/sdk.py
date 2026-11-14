"""Compatibility exports and host-owned machine-readable product catalogue."""
from motor_console_sdk.protocols import *  # noqa: F401,F403
from motor_console_sdk.workflows import *  # noqa: F401,F403
from .authorization import authenticate_session  # legacy SDK v1 compatibility
from .contracts import ProviderContext  # preserve the legacy runtime class export

def product_shape(contract: str) -> dict:
    """Machine-readable product shape without importing business providers."""
    import inspect
    protocols = {"services.workflow": ServiceWorkflowV1, "tasks.coordinator": TaskCoordinatorV1, "storage.store": StorageV1, "security.identity": IdentityV1, "security.session": SessionV1,
                 "security.policy": PolicyV1, "audit.sink": AuditV1, "tasks.scheduler": SchedulerV1,
                 "agent.connection": AgentConnectionV1, "logs.runtime": RuntimeLogsV1,
                 "telemetry.host": HostTelemetryV1, "resources.inventory": InventoryProjectionV1,
                 "resources.placement": PlacementV1, "resources.admission": AdmissionV1,
                 "resources.ledger": LedgerFactoryV1, "motor.native": NativeAdapterV1, "console.ui-assets": UiAssetsV1}
    protocol = protocols.get(contract)
    signatures = ({name: str(inspect.signature(value)) for name, value in vars(protocol).items()
                   if callable(value) and (not name.startswith("_") or name == "__call__")}
                  if protocol is not None else {})
    if contract.startswith("api."):
        shape = {"kind": "APIRouter | tuple[BaseRoute, ...]", "inputs": ["reference_routes (explicit lazy compatibility)", "application_state", "api_inputs: ReferenceApiInputsV1"],
                "ownership": "provider_host.catalogue.route_contract(path) must equal this exact contract",
                "nested_routers": False, "websockets": False,
                "authorization": "mandatory direct selected Policy/Session or Agent identity"}
        if contract == "api.frontend":
            shape["methods"] = ["GET", "HEAD"]
            shape["static_mount"] = "standard Starlette Mount with an exact StaticFiles app; host enforces GET/HEAD"
            shape["writes"] = "declare a protected api.* contract instead"
        if contract in {"api.auth", "api.accounts"}:
            from .authorization import PUBLIC_AUTH_ROUTES
            shape["access"] = {"account_administration": "admin browser session; unsafe methods require selected policy CSRF",
                               "other_auth_routes": "authenticated browser session; unsafe methods require selected policy CSRF",
                               "public_auth_requests": [{"method": method, "path": path} for method, path in sorted(PUBLIC_AUTH_ROUTES)]}
        return shape
    if contract == "templates.catalog": return {"kind": "list[dict]", "inputs": []}
    literals = {
        "security.secrets": {"encrypt": "(value: str) -> str", "decrypt": "(value: str) -> str"},
        "security.agent": {"validate": "() -> None", "authorize": "(authorization: str | None) -> None", "metadata": "() -> dict"},
        "features.catalog": {"__call__": "(upstream: Path) -> list[dict]"},
        "tasks.recovery": {"__call__": "(store: StorageV1) -> None; reconcile recorded state without replay"},
        "demo.seed": {"__call__": "(store: StorageV1, templates: list[dict]) -> None; demo mode only"},
        "telemetry.inventory": {"start": "() -> None", "close": "() -> None"},
        "telemetry.service-health": {"observe": "(service: dict, nodes: dict, native_observer: Callable) -> dict", "close": "() -> None"},
        "telemetry.service-metrics": {"sample": "(service: dict, nodes: dict, *, revision: str, is_current: Callable, fleet=None, demo=False) -> dict", "close": "() -> None"},
        "logs.platform": {"write": "(event: str, *, severity='INFO', actor=None, request_id=None, **fields) -> dict", "query": "(*, limit=100, offset=0, **filters) -> dict", "export_jsonl": "(*, limit=10000, offset=0, **filters) -> BinaryIO", "close": "() -> None"},
        "integration.grafana": {"status": "() -> dict", "settings": "() -> dict", "save": "(settings) -> dict", "probe": "() -> dict", "provision": "() -> dict", "download": "(operation_id, request) -> Response"},
        "branding.manager": {"public": "() -> dict", "save": "(settings) -> dict", "icon": "(digest: str) -> bytes"},
        "maintenance.manager": {"versions": "() -> dict", "releases": "() -> dict", "plan": "(release_id: str) -> dict", "backup": "() -> dict", "verify": "(identifier: str) -> dict", "public_backup": "(value: dict) -> dict", "synchronize_results": "() -> Any"},
        "plugins.packages": {"declarations": "() -> tuple[PluginManifest, ...]", "roles_for": "(owner, *, write=False) -> set[str] | None", "mount": "(registry, context, cleanup) -> None", "catalog": "(registry) -> dict", "inventory": "(registry) -> dict", "validate_selection": "(identifier, digest, expected_revision) -> None", "import_bytes": "(raw: bytes) -> dict", "activate": "(identifier, digest, *, expected_revision, trust_code, configuration) -> dict", "deactivate": "(identifier, *, expected_revision) -> dict"},
        "runtime.docker": {"inventory": "(node_address=None) -> dict", "inventory_network_view": "(snapshot: dict, node_address: str) -> dict", "images": "() -> list[dict]", "status": "(service_id, node_id) -> dict", "preflight": "(payload: dict) -> dict", "deploy": "(payload: dict) -> dict", "action": "(action, service_id, node_id=None, operation_id=None) -> dict", "logs": "(service_id, tail=200, node_id=None, role=None) -> dict", "operation": "(operation_id) -> dict | None", "engine_client": "() -> DockerEnginePort", "resource_ledger": "() -> LedgerV1"},
        "runtime.kubernetes": {"observation_version": "() -> dict", "cluster_nodes": "() -> dict", "external_endpoints": "(service_id) -> dict", "plan": "(payload: dict) -> dict", "deploy": "(payload: dict) -> dict", "scale": "(payload: dict) -> dict", "action": "(service_id, action) -> dict", "status": "(service_id) -> dict", "logs": "(service_id, tail=200, role=None) -> dict", "observations": "(service_id) -> dict", "aigw_models": "(service_id) -> dict", "inference": "(payload: dict) -> dict"},
    }
    signatures.update(literals.get(contract, {}))
    inputs = {"services.workflow": ["workflow_inputs: ServiceWorkflowInputsV1"], "tasks.coordinator": ["coordinator_inputs: TaskCoordinatorInputsV1"], "agent.connection": ["registry"], "telemetry.inventory": ["mutation_lock"],
              "branding.manager": ["mutation_lock"], "integration.grafana": ["mutation_lock"],
              "plugins.packages": ["legacy_profiles"]}.get(contract, [])
    kind = "callable" if "__call__" in signatures else "object"
    return {"kind": kind, "signatures": signatures, "inputs": inputs,
            "method_contract": "See required methods/attributes and provider_host/sdk.py public protocols"}
