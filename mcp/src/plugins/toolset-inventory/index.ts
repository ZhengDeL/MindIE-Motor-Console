import { McpError, type ControlClient, type JsonObject, type McpTool, type ToolContext, type ToolResult } from "@mindie-motor/mcp-sdk";
import type { PluginContext } from "@mindie-motor/host-sdk";

function identifier(args: JsonObject, name: string, path = false): string {
  const value = args[name];
  if (typeof value !== "string" || !value || value.length > 200 || !(path ? /^[A-Za-z0-9_.:/-]+$/ : /^[A-Za-z0-9_.:-]+$/).test(value) || value.split("/").some(part => !part || part === "." || part === "..")) throw new McpError("ARGUMENTS", `${name} is invalid`);
  return path ? value.split("/").map(encodeURIComponent).join("/") : encodeURIComponent(value);
}
async function read(context: ToolContext, path: string): Promise<ToolResult> {
  const data = await context.control.request(context, { method: "GET", path });
  const output = { data };
  return { content: [{ type: "text", text: JSON.stringify(output) }], structuredContent: output };
}
const idSchema = { type: "string", minLength: 1, maxLength: 200 };
const tools: McpTool[] = [
  { name: "list_nodes", description: "List registered nodes and bounded inventory. Target-restricted credentials cannot enumerate all nodes.", requiredScopes: ["nodes:read"], inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false }, execute: context => read(context, "/api/nodes") },
  { name: "get_node_resources", description: "Read one registered node and its stored resource inventory; freshness and errors remain explicit in the response.", requiredScopes: ["nodes:read"], inputSchema: { type: "object", properties: { node_id: idSchema }, required: ["node_id"], additionalProperties: false }, annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false }, execute: (context, args) => read(context, `/api/nodes/${identifier(args, "node_id")}`) },
  { name: "list_templates", description: "List native and saved configuration templates without changing Motor.", requiredScopes: ["templates:read"], inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false }, execute: context => read(context, "/api/templates") },
  { name: "get_template", description: "Read one template, retaining all native JSON fields. Secret values are redacted from tool output.", requiredScopes: ["templates:read"], inputSchema: { type: "object", properties: { template_id: idSchema }, required: ["template_id"], additionalProperties: false }, annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false }, execute: (context, args) => read(context, `/api/templates/${identifier(args, "template_id", true)}`) },
];
export function activate(context: PluginContext): void {
  context.consume("console.mcp.control") as ControlClient;
  context.provide("console.mcp.toolsets", { tools });
}
export default { activate };
