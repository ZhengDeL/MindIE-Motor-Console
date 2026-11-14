/** Stable domain DTOs. Arbitrary Motor JSON is preserved as unknown fields. */
export type Json = Record<string, unknown>;

export type UserRole = "admin" | "operator" | "viewer";

export type ConsoleUser = {
  id: string;
  username: string;
  display_name: string;
  role: UserRole;
  kind?: string;
  auth_source?: string;
  disabled?: boolean;
  created_at?: string;
  updated_at?: string;
  last_login_at?: string | null;
};

export type AuthStatus = {
  setup_required: boolean;
  authenticated: boolean;
  user: ConsoleUser | null;
  csrf_token?: string | null;
  expires_at?: string | null;
  permissions?: string[];
  sso?: { enabled: boolean; login_url?: string };
};

export type Placement = {
  node_id: string;
  role: string;
  instance_name: string;
  devices: number[];
  ports: { node_manager?: number; rpc?: number; base?: number; kv?: number };
};

export type Service = {
  id: string;
  config_revision: number;
  name: string;
  runtime: "docker" | "kubernetes";
  model: string;
  scenario: string;
  engine: string;
  image: string;
  status: string;
  replicas: { prefill: number; decode: number; union: number };
  placements: Placement[];
  node_names: string[];
  device_count: number;
  endpoint: string;
  created_at: string;
  updated_at: string;
  user_config: MotorUserConfig;
  env: Json;
  last_error?: string;
  needs_rebuild?: boolean;
  last_job_id?: string | null;
  extra_manifests?: Json[];
  resources_may_exist?: boolean;
  config_redacted?: boolean;
  motor_profile?: string;
  deployed_placements?: Placement[];
  deployed_runtime?: "docker" | "kubernetes" | null;
  deployed_digest?: string | null;
  deployed_motor_profile?: string | null;
};

export type AgentCredentialPolicy = {
  schema?: number;
  legacy: boolean;
  disabled: boolean;
  current: { id: string; active: boolean; expires_at: string | null };
  previous: { id: string; active: boolean; expires_at: string | null } | null;
};

export type CollectionError = {
  source: string;
  code: string;
  message: string;
};

export type NpuProcess = {
  pid: number;
  memory_used_bytes: number | null;
  container_id?: string;
};

export type NpuDeviceTelemetry = {
  device_id: string;
  // /dev/davinciN uses the physical chip index; Chip Logic ID may differ.
  device_index?: number | null;
  physical_device_id?: number | null;
  logical_device_id?: number | null;
  npu_id?: number | null;
  chip_id?: number | null;
  mapping_status?: "unknown" | "configured" | "verified";
  pci_bus_id?: string | null;
  uuid?: string | null;
  model_name?: string | null;
  utilization_percent: number | null;
  memory_total_bytes: number | null;
  memory_used_bytes: number | null;
  memory_usage_percent: number | null;
  health: "healthy" | "unhealthy" | "unknown";
  temperature_celsius: number | null;
  power_watts: number | null;
  processes_status: "ready" | "unavailable";
  process_pid_scope?: "host" | "unknown";
  processes: NpuProcess[];
};

export type NpuTelemetrySnapshot = {
  status: "ready" | "partial" | "unavailable" | "error";
  source: string;
  collected_at: string | null;
  expires_at: string | null;
  devices: NpuDeviceTelemetry[];
  errors: CollectionError[];
};

export type NpuTelemetry = NpuTelemetrySnapshot & {
  last_good?: NpuTelemetrySnapshot;
};

export type ResourceAllocation = {
  allocation_id: string;
  service_id: string;
  node_id: string;
  operation_id: string;
  state: "pending" | "active" | "uncertain" | "released";
  devices: number[];
  ports: number[];
  container_ids: string[];
  container_names: string[];
  updated_at: string;
  observed_at: string | null;
};

export type ResourceLedger = {
  schema_version: number;
  scope: "single-agent-docker-daemon";
  daemon_id: string;
  collected_at: string;
  complete: boolean;
  items: ResourceAllocation[];
};

export type AgentOperationReceipt = {
  operation_id: string;
  action: string;
  service_id: string;
  node_id: string;
  operation_status:
    "accepted" | "running" | "completed" | "failed" | "interrupted";
  received_at: string;
  started_at: string | null;
  finished_at: string | null;
  result?: Record<string, unknown>;
  error_code?: string;
  message?: string;
  resources_may_exist?: boolean;
};

export type NodeInfo = {
  id: string;
  name: string;
  address: string;
  agent_url: string;
  labels: string[];
  status: string;
  architecture: string;
  os: string;
  docker_version: string;
  docker_available?: boolean;
  kubernetes_available?: boolean;
  kubernetes_version?: string | null;
  npu_total: number | null;
  npu_used: number | null;
  cpu_percent: number | null;
  memory_percent: number | null;
  nic_name: string;
  last_seen: string;
  error?: string;
  resource_scope?: string;
  devices_in_use?: number[];
  devices?: number[];
  inventory_schema?: number;
  inventory_status?: "ready" | "partial" | "error";
  collected_at?: string | null;
  expires_at?: string | null;
  resource_freshness?: "fresh" | "stale" | "unknown" | "demo";
  collection_errors?: CollectionError[];
  daemon_id?: string;
  host_identity?: string;
  identity_conflict?: {
    code: string;
    other_node_ids: string[];
    message: string;
  };
  npu_telemetry?: NpuTelemetry | null;
  resource_ledger?: ResourceLedger;
  demo?: boolean;
  last_good_inventory?: {
    collected_at?: string;
    expires_at?: string;
    npu_total?: number | null;
    npu_used?: number | null;
    devices?: number[];
    devices_in_use?: number[];
    resource_ledger?: ResourceLedger;
  };
  credential_revision?: number;
  credentials_updated_at?: string;
  agent_metadata?: {
    read_only?: boolean;
    execution_mode?: "read_only" | "managed";
    execution_capabilities?: { mutations: boolean };
    transport_security?: { tls_enabled: boolean; client_certificate_required: boolean };
    agent_version?: string;
    agent_protocol?: number;
    operation_protocol?: number;
    supported_runtimes?: string[];
    motor_profile?: { profile_id?: string; commit?: string; driver?: string };
    credential_policy?: AgentCredentialPolicy;
  };
  tls_trust?: {
    kind: "deployment_trust" | "node_ca" | "invalid_node_ca";
    sha256?: string;
    ca_expires_at?: string;
    certificates?: number;
  };
};

export type NodeEditableField =
  | "name"
  | "address"
  | "nic_name"
  | "labels"
  | "credentials"
  | "agent_url";

export type NodeDetail = NodeInfo & {
  configuration_revision: number;
  configured_nic_name: string;
  reported_nic_name: string;
  credential_configured: boolean;
  edit_policy: Record<NodeEditableField, { allowed: boolean; reason: string }>;
  references: {
    services: { id: string; name: string; status: string; resources_may_exist: boolean; blocks_network_edit?: boolean }[];
    jobs: { id: string; action: string; status: string; service_id: string }[];
    service_count?: number;
    job_count?: number;
    resource_claim_count?: number;
    network_protected?: boolean;
  };
};

export type CapabilityGuide = {
  schema_version: 1;
  motor_commit: string;
  purpose: string;
  config_path: string;
  prerequisites: string[];
  notes: string[];
  examples: {
    title: string;
    format: "json" | "yaml" | "shell" | "text";
    target: string;
    code: string;
    notes: string[];
  }[];
  steps: string[];
  validation: { action: string; expected: string }[];
  rollback: string[];
  sources: { path: string; reason: string }[];
};

export type Capability = {
  id: string;
  name: string;
  category: string;
  supported: boolean;
  description: string;
  config_hint: string;
  doc_path: string;
  engines?: string[];
  validation?: string;
  runtimes?: string[];
  docker_supported?: boolean;
  kubernetes_supported?: boolean;
  docker?: boolean;
  kubernetes?: boolean;
  adapter_note?: string;
  execution_supported?: Record<string, boolean>;
  maturity?: "supported" | "poc";
  guide?: CapabilityGuide | null;
};

export type Meta = {
  branding?: PlatformBranding;
  documentation_url?: string;
  product_id?: string;
  template_format?: string;
  name: string;
  version: string;
  mode: string;
  motor_commit: string;
  authenticated: boolean;
  capabilities: Capability[];
  runtimes?: ("docker" | "kubernetes")[];
};

export type Overview = {
  stats: {
    key: string;
    label: string;
    value: string | number;
    unit: string;
    detail: string;
  }[];
  series: { time: string; requests: number; latency: number }[];
  activities: {
    id: string;
    title: string;
    detail: string;
    time: string;
    status: string;
  }[];
  service_counts: {
    running: number;
    stopped: number;
    error: number;
    total: number;
  };
  demo: boolean;
};

export type Template = {
  id: string;
  name: string;
  engine: string;
  scenario: string;
  hardware: string;
  user_config: MotorUserConfig;
  env: Json;
  source: "official" | "custom";
  description: string;
  tags: string[];
  runtime: "docker" | "kubernetes" | null;
  motor_profile: string;
  revision: number;
  created_at: string | null;
  updated_at: string | null;
  extra_manifests: Json[];
  config_redacted?: boolean;
};

export type TemplateInput = {
  name: string;
  description: string;
  tags: string[];
  runtime: "docker" | "kubernetes" | null;
  motor_profile: string;
  user_config: MotorUserConfig;
  env: Json;
  extra_manifests: Json[];
};

export type Plan = {
  valid: boolean;
  checks: { name: string; status: string; detail: string }[];
  commands: { node_id: string; role: string; command: string }[];
  warnings: string[];
  extra_manifest_sha256?: string | null;
  configuration_digest?: string | null;
  manifests?: {
    node_id: string;
    namespace: string;
    resources: {
      apiVersion?: string;
      kind: string;
      name: string;
      namespace?: string;
    }[];
    manifest_yaml: string;
    extra_manifest_sha256?: string | null;
  }[];
  demo?: boolean;
};

export type DeploymentApproval = {
  extra_manifests_approved_sha256?: string;
  reviewed_configuration_digest?: string;
};

export type JobStep = { node_id: string; role: string; status: string; message?: string; operation_id?: string; operation_status?: string };

export type Job = {
  id: string;
  status: string;
  steps: JobStep[];
  cleanup_steps?: JobStep[];
  receipt_observations?: AgentReceiptObservations;
  error?: string;
  created_at: string;
  reconciliation?: JobReconciliation;
};

export type AgentReceiptObservations = {
  offset: number;
  target_count: number;
  next_offset: number | null;
  observed_at: string;
  read_only: boolean;
  note: string;
  items: { node_id: string; operation_id: string; operation_status: string; error?: string }[];
};

export type JobReconciliation = {
  observed_at: string;
  read_only: boolean;
  target_count: number;
  checked_count: number;
  next_offset: number | null;
  functional_acceptance: string;
  note: string;
  nodes: {
    node_id: string;
    node_name: string;
    runtime: string;
    observed_at: string;
    result: "present" | "absent" | "unknown";
    error?: string;
    state?: {
      items: Record<string, string | boolean | null>[];
      resources?: {
        kind?: string;
        name?: string;
        namespace?: string;
        uid?: string;
      }[];
      status?: string;
      namespace?: string;
      pod_readiness?: boolean;
    };
  }[];
};

export type Log = {
  time?: string | null;
  timestamp_valid?: boolean;
  level: string;
  level_source?: string;
  role: string;
  node: string;
  node_name?: string;
  agent_node_id?: string;
  container_id?: string;
  container_name?: string;
  pod?: string;
  namespace?: string;
  source?: string;
  message: string;
};

export type ServiceHealthIssue = {
  source: string;
  node_id?: string;
  role?: string;
  detail: string;
};

export type ServiceReadinessGate = {
  state: "ready" | "starting" | "degraded" | "unknown";
  ready: boolean;
  observed_at: string;
  native_ready?: boolean;
  serving_ready?: boolean;
  configured?: { encode?: number | null; prefill: number | null; decode: number | null; union: number | null };
  expected_source?: string;
  expected: { encode?: number | null; prefill: number | null; decode: number | null; union: number | null };
  observed: { encode?: number | null; prefill: number | null; decode: number | null; union: number | null };
  issues: ServiceHealthIssue[];
};

export type ServiceRuntimeObservation = {
  state: "ready" | "starting" | "degraded" | "unknown";
  observed_at: string | null;
  containers: {
    id: string;
    name: string;
    node_id: string;
    role: string;
    instance_name?: string;
    status: string;
    health: string | null;
    revision: string;
  }[];
  issues: ServiceHealthIssue[];
};

export type InferenceResult = {
  text: string;
  reasoning?: string;
  finish_reason?: string | null;
  truncated?: boolean;
  output_status?: "complete" | "truncated" | "reasoning_only" | "empty" | "incomplete";
  usage?: (Json & { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; completion_tokens_details?: { reasoning_tokens?: number } }) | null;
  latency_ms?: number | null;
  endpoint?: string;
  demo?: boolean;
};

export type Observations = {
  instances: Json | null;
  metrics_text: string;
  readiness: Json | null;
  errors: unknown[];
  observed_at: string;
  runtime?: ServiceRuntimeObservation;
  registration_gate?: ServiceReadinessGate;
  readiness_gate?: ServiceReadinessGate;
  demo?: boolean;
};

export type ServiceMetrics = {
  service_id: string;
  revision: string;
  metrics_view?: "dp" | "full";
  state: "ready" | "unavailable" | "stale" | "warming" | "demo";
  metrics_text: string;
  sampled_at: string | null;
  expires_at: string | null;
  cache_hit: boolean;
  interval_seconds: number;
  error?: string;
};

export type Revision = {
  id: string;
  created_at: string;
  user_config: MotorUserConfig;
  env: Json;
  placements: Placement[];
  runtime: string;
  extra_manifests?: Json[];
  motor_profile?: string;
};

export type IntegrationImage = {
  id: string;
  name: string;
  image: string;
  manifest_digest: string;
  platforms: string[];
  hardware: string;
  motor_release: string;
  vllm_ascend: string;
  python: string;
  os: string;
  source_url: string;
  verification: "registry_metadata_only";
};

export type ImageInspection = {
  image: string;
  image_id: string;
  repo_digests: string[];
  architecture: string;
  os: string;
  created: string;
  entrypoint: string[];
  command: string[];
  declared_revision?: string;
  motor_profile: Json;
  identity: {
    status: "verified" | "declared" | "mismatch" | "unknown";
    evidence: string;
    expected_commit: string;
    declared_commit?: string;
    matched_files?: number;
    expected_files?: number;
  };
  probe: {
    requested: boolean;
    status: "not_requested" | "passed" | "failed" | "timeout" | "unavailable";
    packages: Partial<
      Record<"motor" | "vllm" | "vllm_ascend" | "torch" | "torch_npu", string>
    >;
    modules: Record<"coordinator" | "controller" | "node_manager", boolean>;
    cli: Record<"bash" | "python3" | "vllm", boolean>;
    errors: string[];
  };
  compatibility: "unverified";
  checks: { name: string; status: "pass" | "fail" | "warn"; detail: string }[];
  collected_at: string;
};

export type AigwModel = {
  id: string;
  object?: string;
  owned_by?: string;
  p_max_seqlen?: number;
  d_max_seqlen?: number;
  slo_ttft?: number;
  slo_tpot?: number;
  p_instances_num?: number;
  d_instances_num?: number;
  created?: number;
};

export type AigwModels = {
  status: "available" | "not_deployed" | "unavailable" | "demo";
  items: AigwModel[];
  collected_at: string;
  error?: string;
  source: "motor_coordinator";
};

export type GatewayCheck = {
  status: "matched" | "discovered" | "missing" | "unreachable";
  stale?: boolean;
  checked_at: string;
  reason: string;
  message: string;
  model_ids: string[];
  model_count: number;
  bindings: {
    service_id: string;
    expected_model_ids: string[];
    missing_model_ids: string[];
    status: "matched" | "missing" | "unconfigured";
  }[];
  http_status?: number;
};

export type Gateway = {
  id: string;
  name: string;
  base_url: string;
  service_ids: string[];
  revision: number;
  created_at: string;
  updated_at: string;
  has_api_key: boolean;
  probe_allowed: boolean;
  deployment: { status: "artifact_not_configured"; message: string };
  last_check: GatewayCheck | null;
};

export type ClusterNode = {
  name: string;
  uid: string;
  roles: string[];
  internal_ips: string[];
  ready: "True" | "False" | "Unknown";
  unschedulable: boolean;
  kubelet_version: string;
  container_runtime: string;
  architecture: string;
  os: string;
  labels: Record<string, string>;
  taints: { key: string; value?: string; effect: string }[];
  resources: {
    capacity: Record<string, string>;
    allocatable: Record<string, string>;
  };
};

export type KubernetesInventory = {
  items: ClusterNode[];
  collected_at: string;
  cluster_version: string;
  expires_at?: string;
};

export type AgentNpuInventory = {
  agentId: string;
  agentName: string;
  status: "loading" | "ready" | "error" | "offline";
  inventory?: KubernetesInventory;
  error?: string;
  refreshing?: boolean;
};

export type NpuResourceGroup = {
  key: string;
  allocatable: string | null;
  capacity: string | null;
  nodeCount: number;
  conflictCount: number;
  partial: boolean;
};

export type KubernetesNpuSummary = {
  groups: NpuResourceGroup[];
  status: "none" | "loading" | "ready" | "partial" | "error" | "stale";
  agentCount: number;
  readyAgentCount: number;
  nodeCount: number;
  collectedAt: string | null;
  issues: string[];
};

export type ReadState = { loaded: boolean; refreshing: boolean; error: string };

export type NpuResourcesState = { sources: AgentNpuInventory[]; summary: KubernetesNpuSummary; loading: boolean; refresh(): void };
export type PlatformBranding = { name: string; subtitle: string; icon_url: string | null; revision: number; updated_at?: string | null };

/** Known reference fields are optional; every unrecognized Motor key is retained. */
export type MotorDeployConfig = Json & {
  deploy_mode?: string; hardware_type?: string; image_name?: string; weight_mount_path?: string;
  hybrid_instances_num?: number; p_instances_num?: number; d_instances_num?: number;
};
export type MotorEngineConfig = Json & { engine_type?: string; engine_config?: Json & { model?: string; "model-path"?: string; kv_transfer_config?: Json & { kv_connector?: string } } };
export type MotorUserConfig = Json & {
  motor_deploy_config?: MotorDeployConfig;
  motor_engine_union_config?: MotorEngineConfig; motor_engine_prefill_config?: MotorEngineConfig;
  motor_engine_decode_config?: MotorEngineConfig; motor_engine_encode_config?: MotorEngineConfig;
  kv_conductor_config?: Json & { http_server_port?: number };
  motor_coordinator_config?: Json & { aigw?: Json };
};

export type ConsolePlugin = {
  id: string; name: string; version: string; api_version: number; kind: string;
  enabled: boolean; required: boolean; description: string; dependencies: string[];
  capabilities: string[]; pages: string[]; runtimes: string[]; restart_required: boolean;
};

export type PluginCatalog = { schemaVersion: 1; digest: string; active_storage_plugin: string; items: ConsolePlugin[]; navigation: PluginNavigation[] };

export type PluginAccess = "admin" | "operator" | "viewer";

export type PluginNavigation = {
  plugin_id: string; id: string; title: string; description: string; kind: "status-table";
  endpoint: string; group: string; access: PluginAccess;
};

export type PluginPackage = {
  id: string; name: string; version: string; digest: string; license: string; author: string;
  description: string; access: PluginAccess; selected: boolean; loaded: boolean; blocked_reason?: string;
  compatible_with_host?: boolean;
};

export type PluginPackages = { schemaVersion: 1; revision: number; pending_restart: boolean; items: PluginPackage[] };

export type PluginTable = { title: string; summary: string; columns: { key: string; label: string }[];
  rows: Record<string, string | number | boolean | null>[]; updated_at: string };
