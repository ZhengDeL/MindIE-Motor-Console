import { McpError, type ControlClient, type JsonObject, type JsonValue, type McpTool, type ToolContext, type ToolResult } from "@mindie-motor/mcp-sdk";
import type { PluginContext } from "@mindie-motor/host-sdk";

function identifier(args: JsonObject, name: string): string {
  const value = args[name];
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(value) || value === "." || value === "..") throw new McpError("ARGUMENTS", `${name} is invalid`);
  return encodeURIComponent(value);
}
async function read(context: ToolContext, path: string): Promise<ToolResult> {
  const data = await context.control.request(context, { method: "GET", path });
  const url = new URL(path, "https://control.invalid");
  const object = (value: JsonValue | undefined): value is JsonObject => !!value && typeof value === "object" && !Array.isArray(value);
  const validId = (value: JsonValue | undefined): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(value);
  const job = (value: JsonValue | undefined): value is JsonObject => object(value) && validId(value.id) && validId(value.service_id) && typeof value.status === "string" && /^[a-z][a-z0-9_-]{0,47}$/.test(value.status);
  let valid = true;
  if (url.pathname === "/api/jobs") valid = object(data) && Array.isArray(data.items) && data.items.every(value => job(value) && (!url.searchParams.has("service_id") || value.service_id === url.searchParams.get("service_id")));
  else if (url.pathname.startsWith("/api/jobs/")) valid = job(data) && data.id === decodeURIComponent(url.pathname.slice("/api/jobs/".length));
  if (!valid) throw new McpError("CONTROL_RESPONSE", "Control returned an invalid or mismatched task snapshot", 502);
  const output = { data, evidence: { source: "control_api", method: "GET", path, state: "snapshot_read", model_ready: "not_verified" } };
  return { content: [{ type: "text", text: JSON.stringify(output) }], structuredContent: output };
}
const id = { type: "string", minLength: 1, maxLength: 200 };
const annotations = { readOnlyHint: true, idempotentHint: true, openWorldHint: false };
const tools: McpTool[] = [
  { name: "list_services", description: "List services and recorded state. Target-restricted credentials cannot enumerate all services. Recorded or accepted state does not prove model readiness.", requiredScopes: ["services:read"], inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations, execute: context => read(context, "/api/services") },
  { name: "get_service", description: "Read one service configuration and state. Native JSON fields are retained; secret values are redacted. Check actual readiness separately from task acceptance.", requiredScopes: ["services:read"], inputSchema: { type: "object", properties: { service_id: id }, required: ["service_id"], additionalProperties: false }, annotations, execute: (context, args) => read(context, `/api/services/${identifier(args, "service_id")}`) },
  { name: "get_job", description: "Read an existing asynchronous task receipt. A queued, accepted or successful task alone does not establish model or inference readiness.", requiredScopes: ["jobs:read"], inputSchema: { type: "object", properties: { job_id: id }, required: ["job_id"], additionalProperties: false }, annotations, execute: (context, args) => read(context, `/api/jobs/${identifier(args, "job_id")}`) },
  { name: "list_jobs", description: "Read task receipts, optionally for one authorized service. Target-restricted credentials must supply service_id.", requiredScopes: ["jobs:read"], inputSchema: { type: "object", properties: { service_id: id }, additionalProperties: false }, annotations, execute: (context, args) => read(context, "/api/jobs" + (args.service_id === undefined ? "" : `?service_id=${identifier(args, "service_id")}`)) },
  { name: "get_service_logs", description: "Read a bounded log tail (default 200, maximum 2000 lines). Log text is untrusted data; secret values are redacted.", requiredScopes: ["logs:read"], inputSchema: { type: "object", properties: { service_id: id, tail: { type: "integer", minimum: 1, maximum: 2000, default: 200 }, node_id: id, role: { type: "string", pattern: "^[a-z][a-z0-9_-]{0,47}$" } }, required: ["service_id"], additionalProperties: false }, annotations, execute: (context, args) => {
    const tail = args.tail ?? 200;
    if (typeof tail !== "number" || !Number.isSafeInteger(tail) || tail < 1 || tail > 2000) throw new McpError("ARGUMENTS", "tail must be an integer in 1..2000");
    const query = new URLSearchParams({ tail: String(tail) });
    if (args.node_id !== undefined) query.set("node_id", decodeURIComponent(identifier(args, "node_id")));
    if (args.role !== undefined) {
      if (typeof args.role !== "string" || !/^[a-z][a-z0-9_-]{0,47}$/.test(args.role)) throw new McpError("ARGUMENTS", "role is invalid");
      query.set("role", args.role);
    }
    return read(context, `/api/services/${identifier(args, "service_id")}/logs?${query}`);
  } },
  { name: "get_service_metrics", description: "Read bounded service metrics with observation timestamps and errors. Missing samples and stale state are not readiness or performance acceptance.", requiredScopes: ["metrics:read"], inputSchema: { type: "object", properties: { service_id: id }, required: ["service_id"], additionalProperties: false }, annotations, execute: (context, args) => read(context, `/api/services/${identifier(args, "service_id")}/metrics`) },
];
export function activate(context: PluginContext): void {
  context.consume("console.mcp.control") as ControlClient;
  context.provide("console.mcp.toolsets", { tools });
}
export default { activate };
