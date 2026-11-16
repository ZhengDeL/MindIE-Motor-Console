# `@mindie-motor/mcp-sdk`

Public TypeScript contracts for independently replaceable Console MCP plugins. This package contains JSON types, plugin-facing interfaces and `McpError`; it imports no Web, Python, database, native Motor or MCP protocol implementation. The reference protocol server uses the official MCP SDK separately.

The existing `@mindie-motor/host-sdk` supplies `Plugin`, `PluginContext` and lifecycle hooks. The MCP layer adds four versioned contracts:

| Contract | Cardinality | Value |
| --- | --- | --- |
| `console.mcp.server` 1.0.0 | one | `McpServerRuntime` |
| `console.mcp.auth` 1.0.0 | one | `AuthProvider` |
| `console.mcp.control` 1.0.0 | one | `ControlClient` |
| `console.mcp.toolsets` 1.0.0 | many | `Toolset` with independent `McpTool[]` |

Each tool declares its input JSON Schema, description, required scopes and optional annotations/output schema. It receives a per-request `ToolContext`: a verified `Principal`, cancellation signal, deadline, request id and selected Control client. Return `ToolResult` with text content and optional JSON-object `structuredContent`; a receipt must distinguish a saved configuration or accepted job from actual runtime readiness. Annotations are client hints, not authorization.

`ControlClient.request(context, {method,path,body?,headers?})` accepts an allowlisted relative API path. Pass the supplied request context unchanged. Do not create a Principal from tool arguments, reuse a global identity or forward an MCP token to Control. The reference auth provider validates private registry state on every request and binds verified Principal objects to its own authentication snapshots; fabricated objects fail downstream credential lookup. An alternative auth provider must retain equivalent authority checks.

Reference credentials are private static service credentials, not an OAuth issuer. Authentication, per-tool authorization and a distinct per-subject downstream token are separate responsibilities. Control and gateway independently verify role/scopes/target grants. Request cancellation and deadlines apply to all selected tools, including external toolsets.

Build with `npm ci --ignore-scripts && npm run build`. Package exports contain only `dist`; consumers use the published package or an independently packed local SDK and do not import Console internals. See the [plugin guide](../../mcp/PLUGIN_DEVELOPMENT.md) and [independent service-summary example](../../mcp/examples/service-summary-toolset/README.md) for manifest, composition, import and replacement instructions. Tests and operational credentials stay outside distributable source.
