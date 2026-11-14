import { captureServerClockRequest, observeServerClockResponse } from "./serverClock";
import { DATA_CHANGED_EVENT, PRODUCT_ID, PRODUCT_NAME, SESSION_EXPIRED_EVENT } from "./brand";

export type Json = Record<string, any>;
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
let csrfToken = "";
export const UI_GENERATION_CHANGED_EVENT = `${PRODUCT_ID}:ui-generation-changed`;
let uiGeneration: { generation: string; hostGeneration?: string; providerGeneration?: string } | null = null;
let uiGenerationFailure = "";
/** Set once from the verified boot graph; a live page never adopts another generation. */
export function configureUiGeneration(value: { generation: string; hostGeneration?: string; providerGeneration?: string }) {
  if (uiGeneration && JSON.stringify(uiGeneration) !== JSON.stringify(value)) throw Error("A running UI cannot switch provider generations");
  uiGeneration = Object.freeze({ generation: value.generation, hostGeneration: value.hostGeneration, providerGeneration: value.providerGeneration });
}
export function freezeUiGeneration(message = "UI 或宿主提供者代次已变化，请保存草稿并刷新页面。") {
  uiGenerationFailure = message;
  window.dispatchEvent(new CustomEvent(UI_GENERATION_CHANGED_EVENT, { detail: { message } }));
}
let productVerified = false;
let productVerificationSequence = 0;
export function setCsrfToken(value: string | null | undefined) {
  csrfToken = value ?? "";
}
export const roleLabel = (role: string) =>
  ({ admin: "系统管理员", operator: "运维操作员", viewer: "只读用户" })[role] ??
  role;

function browserJsonNumber(_key: string, value: unknown): unknown {
  if (typeof value === "number") {
    if (!Number.isFinite(value))
      throw Error(
        "JSON 必须使用有限数值，存在超出支持范围的数字；原始内容未保存",
      );
    if (Number.isInteger(value) && !Number.isSafeInteger(value))
      throw Error("数值超出浏览器安全精度，未保存；请通过 API 保留原始数值");
  }
  return value;
}
export function parseStrictJson(text: string) {
  return JSON.parse(text, browserJsonNumber);
}
export async function readJsonFile(file: File): Promise<ReturnType<typeof parseStrictJson>> {
  if (file.size > 4 * 1024 * 1024) throw Error("JSON 文件不能超过 4 MiB，请检查文件类型与内容。");
  return parseStrictJson(await file.text());
}
export function validateJsonNumbers(value: unknown) {
  JSON.stringify(value, browserJsonNumber);
}
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
  user_config: Json;
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
// Display hint from known service metadata; never an authorization or cluster-health check.
export function isUnsubmittedDraft(service?: Service | null): boolean {
  return Boolean(
    service &&
    service.status === "draft" &&
    !service.resources_may_exist &&
    !service.last_job_id &&
    !service.deployed_digest &&
    !service.deployed_runtime &&
    !service.deployed_motor_profile &&
    !service.deployed_placements?.length &&
    !service.needs_rebuild &&
    !service.last_error,
  );
}
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
export function isReadOnlyAgent(node: Pick<NodeInfo, "agent_metadata">): boolean {
  return node.agent_metadata?.read_only === true || node.agent_metadata?.execution_mode === "read_only" || node.agent_metadata?.execution_capabilities?.mutations === false;
}
export function executionReadOnlyReason(service: Service, nodes: NodeInfo[]): string {
  const targets = new Set([...(service.placements ?? []), ...(service.deployed_placements ?? [])].map((placement) => placement.node_id));
  const names = nodes.filter((node) => targets.has(node.id) && isReadOnlyAgent(node)).map((node) => node.name);
  return names.length ? `${names.join("、")} 当前为只读接入。可查看配置与资源；完成资源归属交接并启用管理权限后，才能执行运行变更。` : "";
}
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
  branding?: import("./brandingState").PlatformBranding;
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
  user_config: Json;
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
  user_config: Json;
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

/** One key identifies one user intent. It is not an authentication credential. */
export function createOperationKey(prefix = "web"): string {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,15}$/.test(prefix))
    throw Error("操作标识前缀无效");
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return `${prefix}_${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

/** Keep this object across an explicit retry of the same submission. No retry runs automatically. */
export function createMutationIntent(prefix = "web", existingKey?: string) {
  if (existingKey !== undefined && (typeof existingKey !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(existingKey)))
    throw Error("待确认操作标识无效");
  const key = existingKey ?? createOperationKey(prefix);
  let fingerprint: string | undefined;
  return {
    key,
    submit<T>(path: string, body?: unknown): Promise<T> {
      const next = `${path}\n${body === undefined ? "" : JSON.stringify(body)}`;
      if (fingerprint !== undefined && fingerprint !== next)
        throw Error("此操作标识已用于不同提交，请建立新的操作");
      fingerprint = next;
      return post<T>(path, body, key);
    },
  };
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  responseLimitBytes?: number,
): Promise<T> {
  if (responseLimitBytes !== undefined && (!Number.isSafeInteger(responseLimitBytes) || responseLimitBytes < 1 || responseLimitBytes > 4 * 1024 * 1024))
    throw Error("响应读取上限无效");
  const productCheck = path === "/meta" ? ++productVerificationSequence : 0;
  const mutation = !["GET", "HEAD", "OPTIONS"].includes(
    (options.method ?? "GET").toUpperCase(),
  );
  if (mutation && uiGenerationFailure)
    throw Object.assign(new Error(uiGenerationFailure), { outcomeUnknown: false, generationChanged: true });
  if (mutation && !productVerified)
    throw Object.assign(new Error("平台身份尚未验证，未发送操作请求。请刷新页面并检查后端连接。"), { outcomeUnknown: false });
  const headers = new Headers(options.headers);
  if (uiGeneration?.hostGeneration) headers.set("X-Console-Expected-Generation", uiGeneration.hostGeneration);
  if (uiGeneration?.providerGeneration) headers.set("X-Motor-Expected-Generation", uiGeneration.providerGeneration);
  if (mutation && uiGeneration) headers.set("X-Motor-UI-Generation", uiGeneration.generation);
  if (!headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  if (mutation && csrfToken && !headers.has("X-CSRF-Token"))
    headers.set("X-CSRF-Token", csrfToken);
  if (mutation && !headers.has("Idempotency-Key"))
    headers.set("Idempotency-Key", createOperationKey());
  const operationKey = headers.get("Idempotency-Key") || undefined;
  const clockRequest = captureServerClockRequest();
  let response: Response;
  try {
    response = await fetch("/api" + path, {
      ...options,
      credentials: "same-origin",
      headers,
    });
  } catch (reason) {
    if (productCheck && productCheck === productVerificationSequence) productVerified = false;
    // A lost response does not establish that a mutation was never accepted.
    if (reason instanceof Error)
      Object.assign(reason, { operationKey, outcomeUnknown: mutation });
    throw reason;
  }
  observeServerClockResponse(clockRequest, response);
  if (response.headers.get("X-Motor-Generation-Mismatch") === "true") freezeUiGeneration();
  if (uiGeneration && (uiGeneration.hostGeneration && response.headers.get("X-Console-Generation") !== uiGeneration.hostGeneration
      || uiGeneration.providerGeneration && response.headers.get("X-Motor-Provider-Generation") !== uiGeneration.providerGeneration)) {
    freezeUiGeneration();
    throw Object.assign(new Error(uiGenerationFailure), { status: 409, operationKey,
      outcomeUnknown: mutation && response.ok, generationChanged: true });
  }
  if (!response.ok) {
    if (productCheck && productCheck === productVerificationSequence) productVerified = false;
    if (response.status === 401 && !path.startsWith("/auth/")) {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    let detail = `请求失败 (${response.status})`;
    let structuredDetail: unknown;
    try {
      const body = await responseJson(response, responseLimitBytes);
      structuredDetail = body.detail ?? body;
      detail =
        typeof body.detail === "string"
          ? body.detail
          : typeof body.detail?.message === "string" ? body.detail.message
          : JSON.stringify(body.detail ?? body);
    } catch {}
    if (response.status === 409 && /generation|代次/i.test(detail)) freezeUiGeneration(detail);
    throw Object.assign(new Error(detail), {
      status: response.status,
      detail: structuredDetail,
      operationKey,
      outcomeUnknown: mutation && response.status >= 500,
      retryAfterMs: retryAfterMilliseconds(response.headers.get("Retry-After")),
    });
  }
  try {
    const value = await responseJson(response, responseLimitBytes);
    if (path === "/meta") {
      if (productCheck === productVerificationSequence) productVerified = false;
      if (!value || value.product_id !== PRODUCT_ID || value.name !== PRODUCT_NAME || typeof value.version !== "string" || !value.version.trim())
        throw Object.assign(new Error("后端产品身份不匹配。请连接本平台的正式服务，未加载登录或执行操作。"), { productMismatch: true });
      if (productCheck === productVerificationSequence) productVerified = true;
    }
    if (mutation) window.dispatchEvent(new CustomEvent(DATA_CHANGED_EVENT, { detail: { path } }));
    return value;
  } catch (reason) {
    if (productCheck && productCheck === productVerificationSequence) productVerified = false;
    if (reason instanceof Error)
      Object.assign(reason, { operationKey, outcomeUnknown: mutation });
    throw reason;
  }
}
async function responseJson(response: Response, maximum?: number) {
  if (maximum === undefined) return response.json();
  const length = response.headers.get("Content-Length");
  if (length && /^\d+$/.test(length) && Number(length) > maximum) {
    await response.body?.cancel();
    throw Error("插件响应超出读取上限，已停止读取");
  }
  const reader = response.body?.getReader();
  if (!reader) throw Error("插件响应没有可读取的内容");
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximum) {
        await reader.cancel();
        throw Error("插件响应超出读取上限，已停止读取");
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const content = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { content.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(content));
}
function retryAfterMilliseconds(value: string | null): number | undefined {
  if (!value) return undefined;
  if (/^\d+$/.test(value.trim())) return Math.min(Number(value) * 1000, 300_000);
  const time = Date.parse(value);
  return Number.isFinite(time) ? Math.min(Math.max(0, time - Date.now()), 300_000) : undefined;
}
export function post<T>(path: string, body?: unknown, operationKey?: string) {
  return api<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    ...(operationKey ? { headers: { "Idempotency-Key": operationKey } } : {}),
  });
}
export function download(data: unknown, name: string) {
  validateJsonNumbers(data);
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
export async function downloadFile(path: string, name: string, options: { signal?: AbortSignal } = {}) {
  const clockRequest = captureServerClockRequest();
  const response = await fetch("/api" + path, { credentials: "same-origin", signal: options.signal });
  observeServerClockResponse(clockRequest, response);
  if (!response.ok) {
    let message = `下载失败 (${response.status})`;
    try {
      message = (await response.json()).detail ?? message;
    } catch {}
    throw new Error(message);
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const when = (value: string) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
};
export const roleName = (role: string) =>
  ({
    coordinator_controller: "管理面",
    prefill: "Prefill",
    decode: "Decode",
    union: "PD 混部",
    kv_store: "KV Store",
    single_container: "单容器全组件",
  })[role] ?? role;
export const statusName = (status: string) =>
  ({
    running: "运行中",
    ready: "就绪",
    online: "在线",
    healthy: "在线",
    stopped: "已停止",
    draft: "待部署",
    deploying: "部署中",
    starting: "加载中",
    restarting: "重启中",
    scaling: "扩缩容中",
    removing: "卸载中",
    interrupted: "任务中断",
    cancelled: "未执行",
    stopping: "停止中",
    pending: "等待中",
    accepted: "已受理",
    error: "异常",
    failed: "失败",
    offline: "离线",
    unknown: "未探测",
    partial: "部分可用",
    completed: "已完成",
    success: "成功",
    succeeded: "成功",
    pass: "通过",
    planned: "待主机执行",
    rolled_back: "已回滚",
    rolling_back: "回滚中",
    blocked: "已阻断",
    verified: "已验证",
    warn: "待核对",
    fail: "未通过",
  })[status] ?? status;
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
  usage?: Json | null;
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
  user_config: Json;
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
export const supportedFor = (cap: Capability, runtime: string): boolean =>
  cap.runtimes
    ? cap.runtimes.includes(runtime)
    : runtime === "docker"
      ? (cap.docker_supported ?? cap.docker ?? cap.supported)
      : (cap.kubernetes_supported ?? cap.kubernetes ?? true);
