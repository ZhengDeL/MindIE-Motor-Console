import { McpError, type AuthProvider, type ControlClient, type ControlRequest, type JsonValue, type OperationContext } from "@mindie-motor/mcp-sdk";
import { integerSetting, stringSetting, type PluginContext } from "@mindie-motor/host-sdk";

/** A lost reply never proves that a mutation was rejected. Do not retry blindly. */
export class ControlError extends McpError {
  constructor(code: string, message: string, status: number, readonly outcomeUnknown = false,
    readonly reconciliation?: { method: string; path: string; plan_id?: string; job_id?: string }, readonly backendCode?: string) { super(code, message, status); }
}

const readRoutes = [
  /^\/api\/nodes(?:\/[^/?]+)?$/,
  /^\/api\/templates(?:\/[^?]+)?$/,
  /^\/api\/services(?:\/[^/?]+(?:\/(?:logs|metrics))?)?$/,
  /^\/api\/jobs(?:\/[^/?]+)?$/,
  /^\/api\/plugins\/mcp\/operations\/plans\/[^/?]+$/,
];
function checkedPath(request: ControlRequest): string {
  const { path, method } = request;
  if (!path.startsWith("/api/") || path.length > 2048 || /[\\#\x00-\x20\x7f]/.test(path) || /%25|%2f|%5c|%00|%2e/i.test(path) || path.split("?")[0]!.split("/").some(part => part === "." || part === "..")) throw new McpError("CONTROL_PATH", "Unsupported Control path");
  const url = new URL(path, "https://control.invalid");
  if (url.pathname.split("/").some(part => part === "." || part === "..")) throw new McpError("CONTROL_PATH", "Unsupported path segment");
  const allowed = method === "GET" ? readRoutes.some(route => route.test(url.pathname))
    : method === "POST" ? url.pathname === "/api/services" || /^\/api\/plugins\/mcp\/operations\/(plan|apply)$/.test(url.pathname)
    : method === "PUT" && /^\/api\/services\/[^/?]+$/.test(url.pathname);
  if (!allowed) throw new McpError("CONTROL_PATH", "Control route is outside the MCP client allowlist", 403);
  const keys = url.pathname.endsWith("/logs") ? new Set(["tail", "node_id", "role"])
    : url.pathname === "/api/jobs" ? new Set(["service_id"]) : new Set<string>();
  const seen = new Set<string>();
  for (const [key, value] of url.searchParams) {
    if (!keys.has(key) || seen.has(key) || !value || value.length > 200 || /[\x00-\x1f\x7f]/.test(value)) throw new McpError("CONTROL_QUERY", "Unsupported Control query");
    if (key === "tail" && (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 2000)) throw new McpError("CONTROL_QUERY", "Log tail must be in 1..2000");
    seen.add(key);
  }
  return url.pathname + url.search;
}

function validateJson(value: unknown, depth = 0): asserts value is JsonValue {
  if (depth > 64) throw new McpError("CONTROL_JSON", "JSON exceeds the supported nesting limit");
  if (value === null || typeof value === "boolean" || typeof value === "string") return;
  if (typeof value === "number" && Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value))) return;
  if (Array.isArray(value)) { for (const item of value) validateJson(item, depth + 1); return; }
  if (value && typeof value === "object" && [Object.prototype, null].includes(Object.getPrototypeOf(value))) { for (const item of Object.values(value)) validateJson(item, depth + 1); return; }
  throw new McpError("CONTROL_JSON", "JSON contains a nonfinite number, unsafe integer or unsupported value");
}

const secretKey = /(?:password|passwd|secret|token|authorization|cookie|credential|private[_-]?key|api[_-]?key|kubeconfig|client[_-]?key)(?:$|[_-])/i;
function cleanText(value: string): string {
  return value.replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----/g, "[REDACTED]")
    .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9+/_=.-]+/gi, "$1 [REDACTED]")
    .replace(/((?:password|passwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)/gi, "$1[REDACTED]")
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, "$1[REDACTED]@");
}
/** Response copies retain native field names and redact values, including log text. */
export function redact(value: JsonValue): JsonValue {
  if (typeof value === "string") return cleanText(value);
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, secretKey.test(key) ? "[REDACTED]" : redact(item)]));
  return value;
}

export function activate(context: PluginContext): void {
  if (process.env.NODE_TLS_REJECT_UNAUTHORIZED === "0") throw new McpError("SETTINGS", "TLS certificate verification must remain enabled");
  const auth = context.consume("console.mcp.auth") as AuthProvider;
  const raw = stringSetting(context.settings.baseUrl, "baseUrl");
  const base = new URL(raw);
  if (!["https:", "http:"].includes(base.protocol) || base.username || base.password || base.pathname !== "/" || base.search || base.hash) throw new McpError("SETTINGS", "baseUrl must be a fixed HTTP(S) origin without credentials");
  if (base.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(base.hostname) && context.settings.allowInsecureHttp !== true) throw new McpError("SETTINGS", "Non-loopback HTTP requires explicit allowInsecureHttp for a trusted isolated network");
  const timeout = integerSetting(context.settings.requestTimeoutMs ?? 30000, "requestTimeoutMs", 100, 120000);
  const requestLimit = integerSetting(context.settings.maxRequestBytes ?? 2097152, "maxRequestBytes", 1024, 16777216);
  const responseLimit = integerSetting(context.settings.maxResponseBytes ?? 4194304, "maxResponseBytes", 1024, 16777216);
  async function readReply(response: Response): Promise<JsonValue> {
    const length = response.headers.get("content-length");
    if (length && (!/^\d+$/.test(length) || Number(length) > responseLimit)) { await response.body?.cancel(); throw new McpError("BODY_LIMIT", "Control response exceeds configured byte limit", 502); }
    const reader = response.body?.getReader();
    let size = 0;
    const chunks: Uint8Array[] = [];
    if (reader) try {
      for (;;) {
        const part = await reader.read(); if (part.done) break;
        size += part.value.byteLength;
        if (size > responseLimit) { await reader.cancel(); throw new McpError("BODY_LIMIT", "Control response exceeds configured byte limit", 502); }
        chunks.push(part.value);
      }
    } finally { reader.releaseLock(); }
    let data: unknown;
    try { data = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
    catch { throw new McpError("CONTROL_JSON", "Control returned an invalid JSON reply", 502); }
    validateJson(data);
    return data;
  }
  function sameSet(left: unknown, right: readonly string[] | undefined): boolean {
    if (left === undefined || right === undefined) return left === undefined && right === undefined;
    return Array.isArray(left) && left.every(item => typeof item === "string") && JSON.stringify([...new Set(left)].sort()) === JSON.stringify([...new Set(right)].sort());
  }
  const client: ControlClient = {
    async request<T extends JsonValue>(operation: OperationContext, request: ControlRequest): Promise<T> {
      const path = checkedPath(request);
      if (operation.signal.aborted || !context.running || context.signal.aborted) throw new McpError("ABORTED", "Request cancelled", 499);
      const budget = Math.min(timeout, operation.deadline - Date.now());
      if (budget <= 0) throw new McpError("DEADLINE", "Request deadline elapsed", 504);
      const controller = new AbortController();
      const abort = () => controller.abort();
      operation.signal.addEventListener("abort", abort, { once: true });
      context.signal.addEventListener("abort", abort, { once: true });
      const timer = setTimeout(abort, budget);
      let sent = false;
      let definiteRejection = false;
      const reconcile = { method: request.method, path, ...(request.body && typeof request.body === "object" && !Array.isArray(request.body) && typeof request.body.plan_id === "string" ? { plan_id: request.body.plan_id } : {}) };
      try {
        const token = await auth.backendToken(operation.principal);
        if (controller.signal.aborted) throw new McpError("DEADLINE", "Request deadline elapsed", 504);
        if (!token || /[\r\n]/.test(token)) throw new McpError("CREDENTIAL", "Downstream credential is invalid", 401);
        const headers: Record<string, string> = { accept: "application/json", authorization: `Bearer ${token}` };
        // Revalidate both independently registered identities before each business request.
        const identityReply = await fetch(new URL("/api/plugins/mcp/identity", base), { method: "GET", headers, signal: controller.signal, redirect: "manual" });
        if (!identityReply.ok) { await identityReply.body?.cancel(); throw new McpError("BACKEND_IDENTITY", "Downstream identity could not be verified", 401); }
        const identity = await readReply(identityReply);
        const principal = operation.principal;
        if (!identity || typeof identity !== "object" || Array.isArray(identity) || identity.id !== principal.credentialId || identity.subject !== principal.subject || identity.role !== principal.role || !sameSet(identity.scopes, principal.scopes) || !sameSet(identity.service_ids, principal.serviceIds) || !sameSet(identity.node_ids, principal.nodeIds)) throw new McpError("BACKEND_IDENTITY", "Gateway and downstream identity or grants differ", 403);
        for (const [name, value] of Object.entries(request.headers ?? {})) {
          if (name.toLowerCase() !== "idempotency-key" || !/^[A-Za-z0-9_.:-]{1,128}$/.test(value)) throw new McpError("CONTROL_HEADER", "Unsupported Control header");
          headers["idempotency-key"] = value;
        }
        if (request.body !== undefined) validateJson(request.body);
        const body = request.body === undefined ? undefined : JSON.stringify(request.body);
        if (body !== undefined && Buffer.byteLength(body) > requestLimit) throw new McpError("BODY_LIMIT", "Control request exceeds configured byte limit", 413);
        if (body !== undefined) headers["content-type"] = "application/json";
        sent = true;
        const response = await fetch(new URL(path, base), { method: request.method, headers, ...(body === undefined ? {} : { body }), signal: controller.signal, redirect: "manual" });
        // Redirects never receive a second request or a downstream credential.
        if (response.status >= 300 && response.status < 400) { await response.body?.cancel(); throw new ControlError("CONTROL_REDIRECT", "Control redirects are forbidden", 502, request.method !== "GET", reconcile); }
        definiteRejection = response.status >= 400 && response.status < 500;
        const data = await readReply(response);
        if (!response.ok) {
          // Keep only machine codes and receipt ids, never private error text/configuration.
          const detail = data && typeof data === "object" && !Array.isArray(data) && data.detail && typeof data.detail === "object" && !Array.isArray(data.detail) ? data.detail : undefined;
          const backendCode = detail && typeof detail.code === "string" && /^[A-Za-z0-9_.:-]{1,100}$/.test(detail.code) ? detail.code : undefined;
          const jobId = detail && typeof detail.job_id === "string" && /^[A-Za-z0-9_.:-]{1,200}$/.test(detail.job_id) ? detail.job_id : undefined;
          const unknown = request.method !== "GET" && (response.status >= 500 || detail?.outcome_unknown === true);
          throw new ControlError("CONTROL_HTTP", `Control rejected the request (HTTP ${response.status})`, response.status, unknown, { ...reconcile, ...(jobId ? { job_id: jobId } : {}) }, backendCode);
        }
        return redact(data) as T;
      } catch (error) {
        if (error instanceof ControlError) throw error;
        if (sent && request.method !== "GET" && !definiteRejection) throw new ControlError("OUTCOME_UNKNOWN", "Control reply was lost or invalid. Reconcile the saved plan or job before retrying a write.", 502, true, reconcile);
        if (error instanceof McpError) throw error;
        throw new ControlError(controller.signal.aborted ? "CONTROL_TIMEOUT" : "CONTROL_UNAVAILABLE", controller.signal.aborted ? "Control request cancelled or deadline elapsed" : "Control request failed", 502);
      } finally {
        clearTimeout(timer);
        operation.signal.removeEventListener("abort", abort);
        context.signal.removeEventListener("abort", abort);
      }
    },
  };
  context.provide("console.mcp.control", client);
}
export default { activate };
