import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { createServer as createTlsServer } from "node:https";
import type { Socket } from "node:net";
import { McpServer, createMcpHandler, fromJsonSchema } from "@modelcontextprotocol/server";
import type { CallToolResult, McpHttpHandler, ServerContext } from "@modelcontextprotocol/server";
import { serveStdio, StdioServerTransport } from "@modelcontextprotocol/server/stdio";
import { hostHeaderValidation, toNodeHandler } from "@modelcontextprotocol/node";
import { integerSetting, stringSetting } from "@mindie-motor/host-sdk";
import type { Plugin } from "@mindie-motor/host-sdk";
import { McpError } from "../../contracts.js";
import type { AuthenticationInput, AuthProvider, ControlClient, JsonObject, McpTool, Principal, Toolset } from "../../contracts.js";

function errorResult(error: unknown, tool?: McpTool, args?: JsonObject): CallToolResult {
  const known = error instanceof McpError;
  const details = error && typeof error === "object" ? error as Record<string, unknown> : {};
  const code = known ? error.code : "TOOL_FAILED";
  const message = known ? error.message : "Tool failed. An external operation may require reconciliation.";
  const data: JsonObject = { error: code, message };
  if (typeof details.backendCode === "string" && /^[a-zA-Z0-9_.:-]{1,96}$/.test(details.backendCode)) data.backend_code = details.backendCode;
  if (details.outcomeUnknown === true || (code === "DEADLINE" && tool?.annotations?.readOnlyHint !== true)) {
    data.outcomeUnknown = true;
    // Only concrete bounded reconciliation fields cross the public result boundary.
    const source = details.reconciliation;
    if (source && typeof source === "object") {
      const original = source as Record<string, unknown>;
      data.reconciliation = Object.fromEntries(["method", "path", "plan_id", "job_id", "service_id"].flatMap(key => typeof original[key] === "string" ? [[key, original[key] as string]] : []));
    } else if (typeof args?.plan_id === "string") data.reconciliation = { plan_id: args.plan_id };
  }
  return { content: [{ type: "text", text: JSON.stringify(data) }], structuredContent: data, isError: true };
}
/** Recover only bounded receipt identifiers from the public structured result shape. */
function receiptOf(value: unknown, args: JsonObject): JsonObject {
  const result: JsonObject = {};
  const object = (candidate: unknown): Record<string, unknown> | undefined => candidate && typeof candidate === "object" && !Array.isArray(candidate) ? candidate as Record<string, unknown> : undefined;
  const structured = object(object(value)?.structuredContent);
  for (const candidate of [args, structured, object(structured?.data)]) {
    if (!candidate) continue;
    for (const key of ["plan_id", "job_id", "service_id"]) {
      const id = candidate[key];
      if (typeof id === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(id)) result[key] = id;
    }
  }
  return result;
}
function strings(value: unknown, name: string, fallback: string[]): string[] {
  if (value === undefined) return fallback;
  if (!Array.isArray(value) || value.some(item => typeof item !== "string" || !item.trim() || /[\x00-\x20\x7f]/.test(item))) throw new McpError("SETTINGS", `${name} must be a string array`);
  return value as string[];
}
function sendError(response: ServerResponse, status: number, message: string): void {
  if (response.writableEnded || response.destroyed) return;
  if (response.headersSent) { response.destroy(); return; }
  response.writeHead(status, { "content-type": "text/plain; charset=utf-8", "connection": "close", "cache-control": "no-store", ...(status === 401 ? { "www-authenticate": 'Bearer realm="console-mcp"' } : {}) }).end(`${message}\n`);
}
/** Apply before every selected tool, including plugins that never call Control. */
function validateArguments(value: unknown, depth = 0): void {
  if (depth > 64) throw new McpError("ARGUMENTS", "Tool arguments exceed the nesting limit");
  if (value === null || typeof value === "boolean" || typeof value === "string") return;
  if (typeof value === "number" && Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value))) return;
  if (Array.isArray(value)) { for (const item of value) validateArguments(item, depth + 1); return; }
  if (value && typeof value === "object" && [Object.prototype, null].includes(Object.getPrototypeOf(value))) { for (const item of Object.values(value)) validateArguments(item, depth + 1); return; }
  throw new McpError("ARGUMENTS", "Tool arguments contain an unsafe integer, nonfinite number or unsupported value");
}
const plugin: Plugin = {
  async activate(ctx) {
    const allowedSettings = ["transport", "host", "port", "path", "allowedHosts", "allowedOrigins", "tlsCertFile", "tlsKeyFile", "requestTimeoutMs", "maxBodyBytes", "maxResultBytes", "maxConcurrentRequests"];
    for (const key of Object.keys(ctx.settings)) if (!allowedSettings.includes(key)) throw new McpError("SETTINGS", `MCP server: unsupported setting ${key}`);
    const transport = stringSetting(ctx.settings.transport, "server.transport");
    if (transport !== "http" && transport !== "stdio") throw new McpError("SETTINGS", "server.transport must be http or stdio");
    const timeoutMs = integerSetting(ctx.settings.requestTimeoutMs ?? 30000, "server.requestTimeoutMs", 100, 120000);
    const bodyLimit = integerSetting(ctx.settings.maxBodyBytes ?? 1048576, "server.maxBodyBytes", 1024, 16777216);
    const resultLimit = integerSetting(ctx.settings.maxResultBytes ?? 4194304, "server.maxResultBytes", 1024, 16777216);
    const concurrencyLimit = integerSetting(ctx.settings.maxConcurrentRequests ?? 32, "server.maxConcurrentRequests", 1, 1024);
    const auth = ctx.consume("console.mcp.auth") as AuthProvider;
    const control = ctx.consume("console.mcp.control") as ControlClient;
    if (!auth || typeof auth.authenticate !== "function" || typeof auth.authorize !== "function" || typeof auth.backendToken !== "function" || !control || typeof control.request !== "function") throw new McpError("CONTRACT", "MCP authentication and Control providers must implement the public contracts");
    const tools: McpTool[] = [];
    const names = new Set<string>();
    for (const toolset of ctx.consumeAll("console.mcp.toolsets") as readonly Toolset[]) {
      if (!toolset || !Array.isArray(toolset.tools)) throw new McpError("CONTRACT", "MCP toolset must supply tools");
      for (const tool of toolset.tools) {
        if (!/^[A-Za-z0-9_.-]{1,128}$/.test(tool.name) || names.has(tool.name) || !tool.description || tool.inputSchema?.type !== "object" || !Array.isArray(tool.requiredScopes) || tool.requiredScopes.some((scope: unknown) => typeof scope !== "string" || !scope) || typeof tool.execute !== "function") throw new McpError("CONTRACT", "MCP tool metadata is invalid or duplicate");
        names.add(tool.name); tools.push(tool);
      }
    }
    // Compile the plugin's JSON Schemas once using the official SDK's validator adapter.
    const prepared = tools.map(tool => ({ tool, input: fromJsonSchema<JsonObject>(tool.inputSchema), ...(tool.outputSchema ? { output: fromJsonSchema<JsonObject>(tool.outputSchema) } : {}) }));
    const activeCalls = new Set<AbortController>();
    let calls = 0;
    const buildServer = (source: AuthenticationInput, httpSignal?: AbortSignal, httpDeadline?: number): McpServer => {
      const server = new McpServer({ name: "mindie-motor-console", version: "0.1.0" }, { capabilities: { tools: {} } });
      for (const { tool, input, output } of prepared) {
        server.registerTool(tool.name, { description: tool.description, inputSchema: input,
          ...(tool.title ? { title: tool.title } : {}), ...(output ? { outputSchema: output } : {}),
          ...(tool.annotations ? { annotations: tool.annotations } : {}),
        }, async (args: JsonObject, request: ServerContext): Promise<CallToolResult> => {
          if (!ctx.running || ctx.signal.aborted) return errorResult(new McpError("UNAVAILABLE", "MCP generation is draining", 503));
          if (calls >= concurrencyLimit) return errorResult(new McpError("BUSY", "MCP concurrent tool limit reached", 429));
          calls += 1;
          const controller = new AbortController(); activeCalls.add(controller);
          const signal = AbortSignal.any([ctx.signal, request.mcpReq.signal, controller.signal, ...(httpSignal ? [httpSignal] : [])]);
          const deadline = Math.min(Date.now() + timeoutMs, httpDeadline ?? Number.MAX_SAFE_INTEGER);
          let timer: NodeJS.Timeout | undefined;
          let completed = false;
          let completedResult: unknown;
          try {
            const pending = (async () => {
              validateArguments(args);
              const principal = await auth.authenticate(source);
              signal.throwIfAborted();
              await auth.authorize(principal, tool, args);
              signal.throwIfAborted();
              return tool.execute({ principal, control, signal, deadline, requestId: randomUUID() }, args);
            })();
            // An uncooperative plugin keeps its concurrency slot until its actual work settles.
            void pending.then(() => { activeCalls.delete(controller); calls -= 1; }, () => { activeCalls.delete(controller); calls -= 1; });
            const deadlineError = new McpError("DEADLINE", "Tool deadline elapsed; reconcile a submitted write before retrying", 504);
            const expired = new Promise<never>((_, reject) => { timer = setTimeout(() => { controller.abort(deadlineError); reject(deadlineError); }, Math.max(1, deadline - Date.now())); });
            const result = await Promise.race([pending, expired]);
            completed = true; completedResult = result;
            if (!result || !Array.isArray(result.content) || result.content.some(item => item.type !== "text" || typeof item.text !== "string")) throw new McpError("CONTRACT", "Tool returned an invalid result", 502);
            if (Buffer.byteLength(JSON.stringify(result)) > resultLimit) throw new McpError("RESULT_LIMIT", "Tool result exceeds configured byte limit", 502);
            return { ...result };
          } catch (error) {
            if (completed && tool.annotations?.readOnlyHint !== true) {
              const deliveryError = error instanceof McpError ? error : new McpError("RESULT_INVALID", "Tool result could not be delivered; reconcile the operation before retrying", 502);
              return errorResult(Object.assign(deliveryError, { outcomeUnknown: true, reconciliation: receiptOf(completedResult, args) }), tool, args);
            }
            return errorResult(error, tool, args);
          }
          finally { if (timer) clearTimeout(timer); }
        });
      }
      // stdio uses a connection-scoped SDK instance; discovery still reauthenticates each request.
      server.server.setRequestHandler("tools/list", async () => {
        const principal: Principal = await auth.authenticate(source);
        return { tools: tools.filter(tool => tool.requiredScopes.every(scope => principal.scopes.includes(scope))).map(tool => ({
          name: tool.name, description: tool.description, inputSchema: tool.inputSchema,
          ...(tool.title ? { title: tool.title } : {}), ...(tool.outputSchema ? { outputSchema: tool.outputSchema } : {}),
          ...(tool.annotations ? { annotations: tool.annotations } : {}),
        })) };
      });
      return server;
    };
    if (transport === "stdio") {
      // Validate launcher identity before opening protocol stdio. Calls and discovery validate it again.
      await auth.authenticate({ transport: "stdio" });
      let handle: ReturnType<typeof serveStdio> | undefined;
      ctx.provide("console.mcp.server", { address: () => handle ? { transport: "stdio" } : undefined });
      ctx.onStart(() => {
        handle = serveStdio(() => {
          const server = buildServer({ transport: "stdio" });
          server.server.onclose = () => { if (!ctx.signal.aborted) ctx.fail(new Error("MCP stdio connection closed")); };
          return server;
        }, { transport: new StdioServerTransport(process.stdin, process.stdout, { maxBufferSize: bodyLimit }), maxSubscriptions: 1, onerror: () => ctx.log("warn", "MCP stdio request rejected or transport closed") });
        ctx.log("info", "MCP stdio transport started; stdout is reserved for protocol messages");
      });
      ctx.onStop(async () => { for (const call of activeCalls) call.abort(); await handle?.close(); });
      return;
    }
    const host = stringSetting(ctx.settings.host ?? "127.0.0.1", "server.host");
    const port = integerSetting(ctx.settings.port ?? 8010, "server.port", 0, 65535);
    const path = stringSetting(ctx.settings.path ?? "/mcp", "server.path");
    if (!/^\/[A-Za-z0-9/_-]*$/.test(path)) throw new McpError("SETTINGS", "server.path must be an absolute path without query or fragments");
    const allowedHosts = strings(ctx.settings.allowedHosts, "server.allowedHosts", [host, ...(host === "127.0.0.1" || host === "::1" ? ["localhost"] : [])]);
    if (!allowedHosts.length || allowedHosts.some(value => value.includes("*") || value.includes("/"))) throw new McpError("SETTINGS", "server.allowedHosts must list explicit hostnames");
    const origins = strings(ctx.settings.allowedOrigins, "server.allowedOrigins", []);
    for (const origin of origins) { const url = new URL(origin); if (!["http:", "https:"].includes(url.protocol) || url.origin !== origin) throw new McpError("SETTINGS", "server.allowedOrigins must contain exact HTTP(S) origins"); }
    const certFile = ctx.settings.tlsCertFile === undefined ? undefined : stringSetting(ctx.settings.tlsCertFile, "server.tlsCertFile");
    const keyFile = ctx.settings.tlsKeyFile === undefined ? undefined : stringSetting(ctx.settings.tlsKeyFile, "server.tlsKeyFile");
    if (Boolean(certFile) !== Boolean(keyFile) || (!certFile && !["127.0.0.1", "::1"].includes(host))) throw new McpError("SETTINGS", "HTTP requires TLS, or an explicit literal loopback development bind");
    if (!certFile && allowedHosts.some(value => !["localhost", "127.0.0.1", "::1", "[::1]"].includes(value))) throw new McpError("SETTINGS", "Loopback development HTTP accepts only loopback Host names");
    const validateHost = hostHeaderValidation(allowedHosts);
    const sockets = new Set<Socket>();
    const exchanges = new Set<AbortController>();
    const handlers = new Set<McpHttpHandler>();
    const receive = (request: IncomingMessage, response: ServerResponse): void => {
      if (!ctx.running) { sendError(response, 503, "MCP generation is not running"); return; }
      if (!validateHost(request, response)) return;
      if (request.headers.origin !== undefined && (typeof request.headers.origin !== "string" || !origins.includes(request.headers.origin))) { sendError(response, 403, "Origin is not allowed"); return; }
      if (request.url !== path) { sendError(response, 404, "MCP endpoint not found"); return; }
      if (request.method !== "POST") { sendError(response, 405, "MCP endpoint accepts POST requests"); return; }
      if (exchanges.size >= concurrencyLimit) { sendError(response, 429, "MCP concurrent request limit reached"); return; }
      const controller = new AbortController(); exchanges.add(controller);
      const deadline = Date.now() + timeoutMs;
      const timer = setTimeout(() => { controller.abort(); sendError(response, 504, "MCP request deadline elapsed; reconcile submitted writes before retrying"); }, timeoutMs);
      const disconnected = () => { if (!response.writableFinished) controller.abort(); };
      request.once("aborted", disconnected); response.once("close", disconnected);
      let handler: McpHttpHandler | undefined;
      void (async () => {
        if (request.rawHeaders.filter((value, index) => index % 2 === 0 && value.toLowerCase() === "authorization").length !== 1) throw new McpError("UNAUTHENTICATED", "One Bearer authorization header is required", 401);
        const source: AuthenticationInput = { transport: "http", ...(request.headers.authorization ? { authorization: request.headers.authorization } : {}) };
        await auth.authenticate(source); controller.signal.throwIfAborted();
        // Leave a small response budget so bounded tool deadlines can return a structured error.
        const toolDeadline = deadline - Math.min(50, Math.floor(timeoutMs / 10));
        handler = createMcpHandler(() => buildServer(source, controller.signal, toolDeadline), { maxRequestBodySize: bodyLimit, maxSubscriptions: 1, responseMode: "auto", onerror: () => ctx.log("warn", "MCP request rejected by protocol SDK") });
        handlers.add(handler);
        const selected = handler;
        const adapter = toNodeHandler({ fetch: webRequest => selected.fetch(new Request(webRequest, { signal: controller.signal })) }, { maxRequestBodySize: bodyLimit, onerror: () => ctx.log("warn", "MCP HTTP adapter failed") });
        response.setHeader("cache-control", "no-store");
        response.setHeader("x-request-id", randomUUID());
        await adapter(request as IncomingMessage & { method: string; url: string }, response);
      })().catch(error => sendError(response, error instanceof McpError ? error.status : 500, error instanceof McpError ? error.message : "MCP request failed"))
        .finally(async () => { clearTimeout(timer); exchanges.delete(controller); request.removeListener("aborted", disconnected); response.removeListener("close", disconnected); if (handler) { handlers.delete(handler); await handler.close(); } });
    };
    const server = certFile && keyFile ? createTlsServer({ cert: await readFile(certFile), key: await readFile(keyFile), minVersion: "TLSv1.2" }, receive) : createServer(receive);
    server.headersTimeout = Math.min(timeoutMs, 10000); server.requestTimeout = timeoutMs; server.keepAliveTimeout = 1000; server.maxHeadersCount = 64;
    server.on("connection", socket => { sockets.add(socket); socket.once("close", () => sockets.delete(socket)); });
    server.on("clientError", (_error, socket) => { if (socket.writable) socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n"); });
    server.on("error", () => { if (ctx.running) ctx.fail(new Error("MCP HTTP listener failed")); });
    ctx.provide("console.mcp.server", { address: () => { const value = server.address(); return value && typeof value !== "string" ? { transport: "http", host: value.address, port: value.port, path } : undefined; } });
    ctx.onStart(() => new Promise<void>((resolve, reject) => {
      const error = (reason: Error) => { server.removeListener("listening", listening); reject(reason); };
      const listening = () => { server.removeListener("error", error); ctx.log("info", `MCP ${certFile ? "HTTPS" : "loopback development HTTP"} transport listening on ${host}:${(server.address() as { port: number }).port}${path}`); resolve(); };
      server.once("error", error); server.once("listening", listening); server.listen(port, host);
    }));
    ctx.onStop(async () => {
      for (const exchange of exchanges) exchange.abort(); for (const call of activeCalls) call.abort();
      const closed = new Promise<void>(resolve => { server.close(() => resolve()); });
      for (const socket of sockets) socket.destroy();
      await Promise.allSettled([...handlers].map(handler => handler.close())); await closed;
    });
  },
};
export default plugin;
