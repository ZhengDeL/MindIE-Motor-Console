import { McpError, type ControlClient, type JsonObject, type JsonValue, type McpTool, type ToolContext, type ToolResult } from "@mindie-motor/mcp-sdk";
import type { PluginContext } from "@mindie-motor/host-sdk";

function text(args: JsonObject, field: string, pattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/): string {
  const value = args[field];
  if (typeof value !== "string" || !pattern.test(value)) throw new McpError("ARGUMENTS", `${field} is invalid`);
  return value;
}
const planPattern = /^mcpplan_[0-9a-f]{32}$/;
const actionPattern = /^(deploy|stop|restart|rebuild|scale|undeploy)$/;
function object(value: JsonValue | undefined): value is JsonObject { return !!value && typeof value === "object" && !Array.isArray(value); }
function validId(value: JsonValue | undefined): value is string { return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(value); }
function validPlan(data: JsonValue, body: JsonObject | undefined, requestedPlan: string | undefined): data is JsonObject {
  return object(data) && typeof data.plan_id === "string" && planPattern.test(data.plan_id)
    && typeof data.plan_digest === "string" && /^[0-9a-f]{64}$/.test(data.plan_digest)
    && validId(data.service_id) && typeof data.action === "string" && actionPattern.test(data.action)
    && typeof data.expires_at === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(data.expires_at) && Number.isFinite(Date.parse(data.expires_at))
    && object(data.plan) && typeof data.plan.valid === "boolean"
    && (body === undefined || data.service_id === body.service_id && data.action === body.action)
    && (requestedPlan === undefined || data.plan_id === requestedPlan)
    && (data.status === undefined || data.status === "planned" || data.status === "accepted")
    && (data.job_id === undefined || validId(data.job_id)) && (data.status !== "accepted" || validId(data.job_id));
}
async function request(context: ToolContext, method: "GET" | "POST", path: string, body?: JsonObject): Promise<ToolResult> {
  const data = await context.control.request(context, { method, path, ...(body ? { body } : {}) });
  const applying = path.endsWith("/apply");
  const expectedPlan = method === "GET" ? decodeURIComponent(path.slice(path.lastIndexOf("/") + 1)) : undefined;
  const valid = applying ? object(data) && validId(data.job_id) && typeof data.replayed === "boolean" : validPlan(data, body, expectedPlan);
  if (!valid) {
    const planId = body && typeof body.plan_id === "string" && planPattern.test(body.plan_id) ? body.plan_id
      : object(data) && typeof data.plan_id === "string" && planPattern.test(data.plan_id) ? data.plan_id : expectedPlan;
    throw Object.assign(new McpError("CONTROL_RECEIPT", "Control returned an invalid operation receipt. Reconcile the saved plan or job before retrying a write.", 502), {
      outcomeUnknown: method !== "GET", reconciliation: { method, path, ...(planId ? { plan_id: planId } : {}), ...(body && validId(body.service_id) ? { service_id: body.service_id } : {}), ...(object(data) && validId(data.job_id) ? { job_id: data.job_id } : {}) },
    });
  }
  const output = { data, evidence: { source: "control_api", method, path, state: applying ? "job_accepted" : method === "GET" ? "snapshot_read" : "plan_created", model_ready: "not_verified" } };
  return { content: [{ type: "text", text: JSON.stringify(output) }], structuredContent: output };
}
const tools: McpTool[] = [
  { name: "plan_deployment", description: "Record an expiring owner/revision/provider-bound operation plan (deploy, stop, restart, rebuild, scale or undeploy). Requires operator and deployment:plan. This creates no runtime job; inspect the full returned plan before authorized apply. A model's own confirmation is not human approval.", requiredScopes: ["deployment:plan"], inputSchema: { type: "object", properties: { service_id: { type: "string", minLength: 1, maxLength: 200 }, action: { type: "string", enum: ["deploy", "stop", "restart", "rebuild", "scale", "undeploy"] }, counts: { type: "object", properties: { p_instances_num: { type: "integer", minimum: 1, maximum: 16 }, d_instances_num: { type: "integer", minimum: 1, maximum: 16 }, hybrid_instances_num: { type: "integer", minimum: 1, maximum: 16 } }, minProperties: 1, additionalProperties: false } }, required: ["service_id", "action"], additionalProperties: false }, annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }, execute: (context, args) => {
    const action = text(args, "action", actionPattern);
    const body: JsonObject = { service_id: text(args, "service_id"), action };
    if (action === "scale") {
      if (!args.counts || typeof args.counts !== "object" || Array.isArray(args.counts) || !Object.keys(args.counts).length || Object.entries(args.counts).some(([key, value]) => !["p_instances_num", "d_instances_num", "hybrid_instances_num"].includes(key) || typeof value !== "number" || !Number.isSafeInteger(value) || value < 1 || value > 16)) throw new McpError("ARGUMENTS", "scale requires supported integer counts in 1..16");
      body.counts = args.counts;
    } else if (args.counts !== undefined) throw new McpError("ARGUMENTS", "counts is supported only for scale");
    return request(context, "POST", "/api/plugins/mcp/operations/plan", body);
  } },
  { name: "apply_deployment", description: "Submit a previously reviewed operation plan using its exact digest and service confirmation name. Requires operator and deployment:apply. Backend validates owner, revision, targets, provider generation and expiry; the saved plan has a stable idempotency key. Returns a verified-shape job receipt, never proof of model readiness. On outcomeUnknown reconcile plan/job before repeating; no automatic write retry.", requiredScopes: ["deployment:apply"], inputSchema: { type: "object", properties: { plan_id: { type: "string", pattern: "^mcpplan_[0-9a-f]{32}$" }, plan_digest: { type: "string", pattern: "^[0-9a-f]{64}$" }, confirmation_name: { type: "string", minLength: 1, maxLength: 100 } }, required: ["plan_id", "plan_digest", "confirmation_name"], additionalProperties: false }, annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: false }, execute: (context, args) => request(context, "POST", "/api/plugins/mcp/operations/apply", { plan_id: text(args, "plan_id", planPattern), plan_digest: text(args, "plan_digest", /^[0-9a-f]{64}$/), confirmation_name: text(args, "confirmation_name", /^[^\x00-\x1f\x7f]{1,100}$/) }) },
  { name: "get_deployment_plan", description: "Read the Control snapshot of an existing saved operation plan, including its job reference after apply. Requires operator, deployment:plan and the original plan owner. Use after a lost apply reply before any retry; a read does not establish runtime readiness.", requiredScopes: ["deployment:plan"], inputSchema: { type: "object", properties: { plan_id: { type: "string", pattern: "^mcpplan_[0-9a-f]{32}$" } }, required: ["plan_id"], additionalProperties: false }, annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false }, execute: (context, args) => request(context, "GET", `/api/plugins/mcp/operations/plans/${encodeURIComponent(text(args, "plan_id", planPattern))}`) },
];
export function activate(context: PluginContext): void {
  context.consume("console.mcp.control") as ControlClient;
  context.provide("console.mcp.toolsets", { tools });
}
export default { activate };
