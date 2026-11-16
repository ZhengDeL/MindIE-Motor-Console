import { McpError, type ControlClient, type JsonObject, type JsonValue, type McpTool, type ToolContext, type ToolResult } from "@mindie-motor/mcp-sdk";
import type { PluginContext } from "@mindie-motor/host-sdk";

const document = { type: "object", additionalProperties: true };
const properties = {
  name: { type: "string", minLength: 1, maxLength: 100 }, runtime: { type: "string", enum: ["docker", "kubernetes"] },
  user_config: document, env: document,
  placements: { type: "array", maxItems: 256, items: document },
  extra_manifests: { type: "array", maxItems: 100, items: document }, motor_profile: { type: "string", maxLength: 96 },
};
function draft(args: JsonObject, updating: boolean): JsonObject {
  const value = args.draft;
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new McpError("ARGUMENTS", "draft must be a complete service draft object");
  for (const field of ["user_config", "env"]) if (!value[field] || typeof value[field] !== "object" || Array.isArray(value[field])) throw new McpError("ARGUMENTS", `${field} must be an object`);
  if (updating && (typeof value.expected_revision !== "number" || !Number.isSafeInteger(value.expected_revision) || value.expected_revision < 1)) throw new McpError("ARGUMENTS", "Update requires the current expected_revision");
  // Pass the complete document. Never reconstruct Motor JSON from known fields.
  return value;
}
function object(value: JsonValue | undefined): value is JsonObject { return !!value && typeof value === "object" && !Array.isArray(value); }
async function write(context: ToolContext, method: "POST" | "PUT", path: string, body: JsonObject): Promise<ToolResult> {
  const data = await context.control.request(context, { method, path, body });
  const targetId = method === "PUT" ? decodeURIComponent(path.slice(path.lastIndexOf("/") + 1)) : undefined;
  if (!object(data) || typeof data.id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(data.id)
    || targetId !== undefined && data.id !== targetId || typeof data.config_revision !== "number" || !Number.isSafeInteger(data.config_revision) || data.config_revision < 1
    || method === "PUT" && data.config_revision !== Number(body.expected_revision) + 1
    || data.name !== (typeof body.name === "string" ? body.name.trim() : body.name) || data.runtime !== (body.runtime ?? "docker")
    || !object(data.user_config) || !object(data.env)) {
    throw Object.assign(new McpError("CONTROL_RECEIPT", "Control returned an invalid configuration receipt. Reconcile the service and revision before retrying the write.", 502), {
      outcomeUnknown: true, reconciliation: { method, path, ...(targetId ? { service_id: targetId } : object(data) && typeof data.id === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(data.id) ? { service_id: data.id } : {}) },
    });
  }
  const output = { data, evidence: { source: "control_api", method, path, state: "configuration_saved", service_id: data.id, config_revision: data.config_revision, runtime_applied: false, model_ready: "not_verified" } };
  return { content: [{ type: "text", text: JSON.stringify(output) }], structuredContent: output };
}
const annotations = { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false };
const tools: McpTool[] = [
  { name: "create_service_draft", description: "Save a complete service draft without deploying it. Requires operator role and configuration:write; service/node target-restricted credentials cannot create drafts. All native user_config/env fields pass unchanged to Control.", requiredScopes: ["configuration:write"], inputSchema: { type: "object", properties: { draft: { type: "object", properties, required: ["name", "user_config", "env"], additionalProperties: false } }, required: ["draft"], additionalProperties: false }, annotations, execute: (context, args) => write(context, "POST", "/api/services", draft(args, false)) },
  { name: "update_service_draft", description: "Save a complete service draft revision without applying it. Requires operator role, configuration:write and current expected_revision. Node target-restricted credentials cannot update placement-bearing drafts. Do not replace redacted secrets with the redaction marker.", requiredScopes: ["configuration:write"], inputSchema: { type: "object", properties: { service_id: { type: "string", minLength: 1, maxLength: 200 }, draft: { type: "object", properties: { ...properties, expected_revision: { type: "integer", minimum: 1 } }, required: ["name", "user_config", "env", "expected_revision"], additionalProperties: false } }, required: ["service_id", "draft"], additionalProperties: false }, annotations, execute: (context, args) => {
    if (typeof args.service_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(args.service_id)) throw new McpError("ARGUMENTS", "service_id is invalid");
    return write(context, "PUT", `/api/services/${encodeURIComponent(args.service_id)}`, draft(args, true));
  } },
];
export function activate(context: PluginContext): void {
  context.consume("console.mcp.control") as ControlClient;
  context.provide("console.mcp.toolsets", { tools });
}
export default { activate };
