/** Public MCP plugin contracts. No Console business implementation or protocol SDK dependency. */
export const MCP_CONTRACT_VERSION = "1.0.0";
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };
export type JsonSchema = JsonObject & { type: "object" };

/** A snapshot of a verified private service credential, never its secret. */
export interface Principal {
  readonly credentialId: string;
  readonly subject: string;
  readonly role: string;
  readonly scopes: readonly string[];
  readonly serviceIds?: readonly string[];
  readonly nodeIds?: readonly string[];
}
export interface AuthenticationInput {
  readonly transport: "http" | "stdio";
  readonly authorization?: string;
  /** Trusted local launcher selection for stdio; HTTP must authenticate a Bearer token. */
  readonly credentialId?: string;
}
export interface ToolAnnotations {
  readOnlyHint?: boolean;
  destructiveHint?: boolean;
  idempotentHint?: boolean;
  openWorldHint?: boolean;
}
export interface ToolMetadata {
  readonly name: string;
  readonly title?: string;
  readonly description: string;
  readonly inputSchema: JsonSchema;
  readonly outputSchema?: JsonSchema;
  readonly annotations?: ToolAnnotations;
  /** All scopes are required both for discovery and invocation. */
  readonly requiredScopes: readonly string[];
}
export interface ToolResult {
  content: { type: "text"; text: string }[];
  structuredContent?: JsonObject;
  isError?: boolean;
}
export interface ControlRequest {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** Relative, allowlisted Control API path, never an arbitrary URL. */
  path: string;
  body?: JsonValue;
  headers?: Readonly<Record<string, string>>;
}
export interface OperationContext {
  readonly principal: Principal;
  readonly signal: AbortSignal;
  readonly requestId: string;
  /** Unix epoch milliseconds. */
  readonly deadline: number;
}
export interface ControlClient {
  request<T extends JsonValue = JsonValue>(context: OperationContext, request: ControlRequest): Promise<T>;
}
export interface ToolContext extends OperationContext { readonly control: ControlClient }
export interface McpTool extends ToolMetadata {
  execute(context: ToolContext, args: JsonObject): Promise<ToolResult>;
}
export interface Toolset { readonly tools: readonly McpTool[] }
/** Private service credentials in this reference implementation are not OAuth. */
export interface AuthProvider {
  authenticate(input: AuthenticationInput): Promise<Principal>;
  /** Revalidate current registry state and enforce scopes/target restrictions on every call. */
  authorize(principal: Principal, tool: ToolMetadata, args: JsonObject): Promise<void>;
  /** Return only the distinct downstream secret after current registry validation. Never forward MCP tokens. */
  backendToken(principal: Principal): Promise<string>;
}
export interface McpServerRuntime { address(): { transport: "stdio" | "http"; host?: string; port?: number; path?: string } | undefined }
export interface McpContractMap {
  "console.mcp.toolsets": Toolset;
  "console.mcp.control": ControlClient;
  "console.mcp.auth": AuthProvider;
  "console.mcp.server": McpServerRuntime;
}
export class McpError extends Error {
  constructor(readonly code: string, message: string, readonly status = 400) { super(message); this.name = "McpError"; }
}
