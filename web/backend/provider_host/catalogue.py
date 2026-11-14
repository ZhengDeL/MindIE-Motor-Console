"""Executable v1 contracts and route ownership, independent of provider IDs."""
from .contracts import Contract

# A replacement must implement the actual methods called by reference modules.
# Storage includes its public repository transaction port: callers never receive
# the selected driver's SQL connection. See backend/ports/storage.py.
CONTRACTS = {item.id: item for item in (
    Contract("console.application", internal=True),
    Contract('storage.store', methods=('list', 'get', 'put', 'put_many', 'delete', 'page', 'revisions', 'unit_of_work', 'health', 'close', 'storage_info', 'backup', 'mutate_audited', 'mutate_many_audited', 'compare_and_mutate_audited', 'audit', 'audit_items', 'query_audit', 'prune_audit'), attributes=('directory', 'schema_version')),
    Contract("security.secrets", methods=("encrypt", "decrypt")),
    Contract('security.identity', methods=('setup_required', 'bootstrap', 'login', 'users', 'transaction', 'bind_security', 'authenticate', 'authorize', 'check_origin', 'csrf', 'clear_cookie', 'change_password', 'create_user', 'update_user', 'federated_login', 'security_event', 'throttle', 'revoke_session'), attributes=('settings', 'clock', 'sso_status', 'session_cookie_name')),
    Contract('security.session', methods=('authenticate', 'issue', 'revoke', 'revoke_user', 'window', 'csrf', 'set_cookie', 'clear_cookie', 'check_origin', 'check_csrf'), attributes=('cookie_name',)),
    Contract("security.policy", methods=("authorize",)),
    Contract("security.agent", methods=("validate", "authorize", "metadata")),
    Contract('audit.sink', methods=('audit', 'query_audit', 'audit_items', 'prune_audit', 'mutate_audited', 'mutate_many_audited', 'compare_and_mutate_audited', 'append_event'), attributes=()),
    Contract("logs.platform", methods=("write", "query", "export_jsonl", "close")),
    Contract("logs.runtime", methods=("read", "close")),
    Contract("tasks.scheduler", methods=("submit", "shutdown")),
    Contract("tasks.recovery", methods=("__call__",)),
    Contract("demo.seed", methods=("__call__",)),
    Contract('agent.connection', methods=('call', 'cluster_nodes'), attributes=('secrets',)),
    Contract("telemetry.inventory", methods=("start", "close")),
    Contract("telemetry.service-health", methods=("observe", "close")),
    Contract("telemetry.service-metrics", methods=("sample", "close")),
    Contract('telemetry.host', methods=('__call__',), attributes=()),
    Contract('resources.inventory', methods=('__call__',), attributes=()),
    Contract('resources.ledger', methods=('__call__',), attributes=()),
    Contract("resources.admission", methods=("check_physical_usage", "restart")),
    Contract('resources.placement', methods=('__call__',), attributes=()),
    Contract('motor.native', methods=('public_profile', 'check_binding', 'agent_metadata', 'upstream_path', 'official_helpers', 'validate_layout', 'build_container_spec', 'verify_upstream_source', 'native_contract_checks', 'render_kubernetes', 'render_kubernetes_scale'), attributes=()),
    Contract('features.catalog', methods=('__call__',), attributes=()),
    Contract("templates.catalog"),
    Contract("runtime.docker", methods=("inventory", "inventory_network_view", "images", "status", "preflight", "deploy", "action", "logs", "operation", "engine_client", "resource_ledger")),
    Contract("runtime.kubernetes", methods=("observation_version", "cluster_nodes", "external_endpoints", "plan", "deploy", "scale", "action", "status", "logs", "observations", "aigw_models", "inference")),
    Contract('integration.grafana', methods=('status', 'settings', 'save', 'probe', 'provision', 'download'), attributes=('descriptor',), optional=True),
    Contract('maintenance.manager', methods=('synchronize_results', 'versions', 'releases', 'plan', 'backup', 'verify', 'public_backup'), attributes=('store', 'logs', 'configuration', 'mode')),
    Contract('branding.manager', methods=('public', 'save', 'icon'), attributes=()),
    Contract('console.ui-assets', methods=('__call__',), attributes=()),
    Contract("plugins.packages", methods=("declarations", "roles_for", "mount", "catalog", "inventory", "validate_selection", "import_bytes", "activate", "deactivate"), attributes=("mode",)),
    *(Contract("api." + name) for name in (
        "auth", "accounts", "nodes", "services", "overview", "templates", "images", "gateways",
        "runtime-logs", "metrics", "tasks", "audit", "system", "extensions", "plugins", "frontend", "agent-docker", "agent-kubernetes")),
)}


def route_contract(path: str) -> str | None:
    """Most-specific stable module owner; query strings never enter ownership."""
    if path in {"/health", "/ready", "/api/meta"}:
        return None
    if path.startswith("/api/ui-plugins"):
        return "console.ui-assets"
    if path == "/api/auth/users" or path.startswith("/api/auth/users/"):
        return "api.accounts"
    if path == "/api/auth" or path.startswith("/api/auth/"):
        return "api.auth"
    if path.startswith(("/api/nodes", "/api/agent-rollouts")):
        return "api.nodes"
    if path.startswith("/api/services/"):
        if path.endswith("/logs"):
            return "api.runtime-logs"
        if path.endswith(("/metrics", "/observations")):
            return "api.metrics"
        if path.endswith("/aigw-models"):
            return "api.gateways"
    if path.startswith(("/api/services", "/api/resources")):
        return "api.services"
    if path.startswith("/api/integrations/images") or path.startswith("/api/images"):
        return "api.images"
    for prefix, name in (("/api/templates", "templates"), ("/api/gateways", "gateways"),
                         ("/api/overview", "overview"), ("/api/jobs", "tasks"),
                         ("/api/audit", "audit"), ("/api/extensions", "extensions"),
                         ("/api/system", "system"), ("/api/branding", "system"),
                         ("/api/plugins", "plugins"), ("/api/plugin-", "plugins")):
        if path == prefix or path.startswith(prefix + "/") or prefix.endswith("-") and path.startswith(prefix):
            return "api." + name
    if path.startswith("/agent/kubernetes"):
        return "api.agent-kubernetes"
    if path.startswith("/agent/"):
        return "api.agent-docker"
    if not path.startswith(("/api/", "/agent/")):
        return "api.frontend"
    return None
