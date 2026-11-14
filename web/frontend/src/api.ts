import type { Service, NodeInfo, Capability } from "@mindie-motor/ui-sdk";
export type { MotorUserConfig, MotorDeployConfig, MotorEngineConfig } from "@mindie-motor/ui-sdk";
export type { Json, UserRole, ConsoleUser, AuthStatus, Placement, Service, AgentCredentialPolicy, CollectionError, NpuProcess, NpuDeviceTelemetry, NpuTelemetrySnapshot, NpuTelemetry, ResourceAllocation, ResourceLedger, AgentOperationReceipt, NodeInfo, NodeEditableField, NodeDetail, CapabilityGuide, Capability, Meta, Overview, Template, TemplateInput, Plan, DeploymentApproval, JobStep, Job, AgentReceiptObservations, JobReconciliation, Log, ServiceHealthIssue, ServiceReadinessGate, ServiceRuntimeObservation, InferenceResult, Observations, ServiceMetrics, Revision, IntegrationImage, ImageInspection, AigwModel, AigwModels, GatewayCheck, Gateway } from '@mindie-motor/ui-sdk';
import { captureServerClockRequest, observeServerClockResponse } from "./serverClock";
import { DATA_CHANGED_EVENT, PRODUCT_ID, PRODUCT_NAME, SESSION_EXPIRED_EVENT } from "./brand";

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

export function isReadOnlyAgent(node: Pick<NodeInfo, "agent_metadata">): boolean {
  return node.agent_metadata?.read_only === true || node.agent_metadata?.execution_mode === "read_only" || node.agent_metadata?.execution_capabilities?.mutations === false;
}
export function executionReadOnlyReason(service: Service, nodes: NodeInfo[]): string {
  const targets = new Set([...(service.placements ?? []), ...(service.deployed_placements ?? [])].map((placement) => placement.node_id));
  const names = nodes.filter((node) => targets.has(node.id) && isReadOnlyAgent(node)).map((node) => node.name);
  return names.length ? `${names.join("、")} 当前为只读接入。可查看配置与资源；完成资源归属交接并启用管理权限后，才能执行运行变更。` : "";
}

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

export const supportedFor = (cap: Capability, runtime: string): boolean =>
  cap.runtimes
    ? cap.runtimes.includes(runtime)
    : runtime === "docker"
      ? (cap.docker_supported ?? cap.docker ?? cap.supported)
      : (cap.kubernetes_supported ?? cap.kubernetes ?? true);
