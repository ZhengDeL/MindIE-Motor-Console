import { spawn } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import type { IncomingHttpHeaders, IncomingMessage, OutgoingHttpHeaders, ServerResponse } from "node:http";
import { isAbsolute } from "node:path";
import { connect } from "node:net";
import { Transform } from "node:stream";
import { setTimeout as delay } from "node:timers/promises";
import type { JsonObject, Plugin, RequestContext } from "@mindie/console-platform/sdk";

// Runtime dependencies stay inside this package or Node builtins, so an imported artifact can move.
class PluginError extends Error {
  constructor(readonly code: string, message: string) { super(message); this.name = "PluginError"; }
}
function objectSetting(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new PluginError("SETTINGS", `${label} must be an object`);
  return value as JsonObject;
}
function stringSetting(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim() || /[\x00-\x1f\x7f]/.test(value)) throw new PluginError("SETTINGS", `${label} must be a nonempty string without controls`);
  return value;
}
function integerSetting(value: unknown, label: string, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) throw new PluginError("SETTINGS", `${label} must be an integer in ${minimum}..${maximum}`);
  return value;
}

const hopByHop = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"]);
function headers(source: IncomingHttpHeaders): OutgoingHttpHeaders {
  const blocked = new Set([...hopByHop, ...(source.connection ?? "").split(",").map(value => value.trim().toLowerCase())]);
  const result: OutgoingHttpHeaders = {};
  for (const [key, value] of Object.entries(source)) if (!blocked.has(key) && value !== undefined) result[key] = value;
  return result;
}
function upstreamSetting(value: unknown): URL {
  const url = new URL(stringSetting(value, "bridge.upstream"));
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new PluginError("SETTINGS", "bridge.upstream must be an HTTP(S) origin without credentials/path/query/fragment");
  return url;
}
function checkAllowed(settings: JsonObject, allowed: string[], label: string): void {
  for (const key of Object.keys(settings)) if (!allowed.includes(key)) throw new PluginError("SETTINGS", `${label}: unsupported setting ${key}`);
}
function proxy(upstream: URL, maximumBytes: number, request: IncomingMessage, response: ServerResponse, context: RequestContext): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!request.url?.startsWith("/") || request.url.startsWith("//") || request.url.includes("\\")) { response.writeHead(400).end(); resolve(); return; }
    const forwarded = headers(request.headers);
    // A remote caller cannot override identity supplied by this transport generation.
    for (const key of Object.keys(forwarded)) if (key.startsWith("x-forwarded-")) delete forwarded[key];
    forwarded["x-request-id"] = context.requestId;
    forwarded["x-console-generation"] = context.generation;
    forwarded["x-console-deadline-unix-ms"] = String(context.deadline);
    const contentLength = request.headers["content-length"];
    if (contentLength !== undefined && (!/^\d+$/.test(contentLength) || Number(contentLength) > maximumBytes)) {
      response.writeHead(413, { "connection": "close" }).end(); resolve(); return;
    }
    const send = upstream.protocol === "https:" ? httpsRequest : httpRequest;
    const outgoing = send(upstream, { method: request.method, path: request.url, headers: forwarded, signal: context.signal });
    let settled = false;
    let received: IncomingMessage | undefined;
    const done = (error?: Error) => {
      if (settled) return;
      settled = true;
      request.unpipe(body);
      response.removeListener("finish", finish);
      response.removeListener("close", close);
      if (error) { outgoing.destroy(); received?.destroy(); reject(error); } else resolve();
    };
    const finish = () => done();
    const close = () => done(response.writableFinished ? undefined : new Error("Downstream disconnected; operation outcome may be unknown"));
    let sent = 0;
    const body = new Transform({ transform(chunk: Buffer, _encoding, callback) {
      sent += chunk.length;
      callback(sent > maximumBytes ? new PluginError("BODY_TOO_LARGE", "Request body exceeds configured byte limit") : undefined, chunk);
    } });
    body.on("error", error => {
      if (!response.headersSent) response.writeHead(413, { "connection": "close" }).end();
      done(error);
    });
    request.once("error", error => done(error));
    outgoing.once("error", error => done(error));
    outgoing.once("response", incoming => {
      received = incoming;
      if (response.destroyed) { incoming.destroy(); done(new Error("Downstream closed")); return; }
      const responseHeaders = headers(incoming.headers);
      // Correlation fields are owned by the transport, even when an upstream emits its own values.
      delete responseHeaders["x-console-generation"]; delete responseHeaders["x-request-id"];
      response.writeHead(incoming.statusCode ?? 502, responseHeaders);
      incoming.once("error", error => done(error));
      incoming.once("aborted", () => done(new Error("Upstream response truncated")));
      // Native Motor JSON, SSE, cookies and arbitrary response bytes are never parsed or buffered.
      incoming.pipe(response);
    });
    response.once("finish", finish); response.once("close", close);
    request.pipe(body).pipe(outgoing);
  });
}
async function ready(url: URL, milliseconds: number, child: ChildProcess, signal: AbortSignal): Promise<void> {
  const deadline = performance.now() + milliseconds;
  while (performance.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) throw new PluginError("PROCESS_EXIT", "Bridge process exited before readiness");
    signal.throwIfAborted();
    try {
      await new Promise<void>((resolve, reject) => {
        const req = (url.protocol === "https:" ? httpsRequest : httpRequest)(url, { signal, timeout: 500 }, res => {
          res.resume();
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) resolve(); else reject(new Error("Not ready"));
        });
        req.once("timeout", () => req.destroy(new Error("Readiness timeout"))); req.once("error", reject); req.end();
      });
      return;
    } catch { signal.throwIfAborted(); await delay(100, undefined, { signal }); }
  }
  throw new PluginError("PROCESS_READINESS", "Bridge process did not become healthy within its startup budget");
}
async function requireUnusedPort(url: URL): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const hostname = url.hostname.replace(/^\[|\]$/g, "");
    const socket = connect({ host: hostname, port: Number(url.port || (url.protocol === "https:" ? 443 : 80)) });
    socket.setTimeout(1000);
    socket.once("connect", () => { socket.destroy(); reject(new PluginError("PROCESS_PORT_IN_USE", "Managed bridge upstream port already has a listener")); });
    socket.once("timeout", () => { socket.destroy(); reject(new PluginError("PROCESS_PORT_CHECK", "Could not verify managed bridge upstream port is unused")); });
    socket.once("error", error => { if ((error as NodeJS.ErrnoException).code === "ECONNREFUSED") resolve(); else reject(error); });
  });
}
const plugin: Plugin = {
  activate(ctx) {
    checkAllowed(ctx.settings as JsonObject, ["upstream", "maxRequestBytes", "process"], "bridge");
    const upstream = upstreamSetting(ctx.settings.upstream);
    const maximumBytes = integerSetting(ctx.settings.maxRequestBytes ?? 16777216, "bridge.maxRequestBytes", 1, 1073741824);
    let child: ChildProcess | undefined;
    if (ctx.settings.process !== undefined) {
      const config = objectSetting(ctx.settings.process, "bridge.process");
      checkAllowed(config, ["executable", "args", "cwd", "readyPath", "startupTimeoutMs", "shutdownTimeoutMs"], "bridge.process");
      const executable = stringSetting(config.executable, "bridge.process.executable");
      const cwd = stringSetting(config.cwd, "bridge.process.cwd");
      if (!isAbsolute(executable) || !isAbsolute(cwd)) throw new PluginError("SETTINGS", "Bridge executable and cwd must be explicit absolute paths");
      if (!Array.isArray(config.args) || config.args.some(value => typeof value !== "string" || value.includes("\0"))) throw new PluginError("SETTINGS", "Bridge process.args must be fixed string argv");
      const args = config.args as string[];
      const readyPath = stringSetting(config.readyPath ?? "/ready", "bridge.process.readyPath");
      if (!readyPath.startsWith("/") || readyPath.startsWith("//") || readyPath.includes("\\")) throw new PluginError("SETTINGS", "Readiness path must be an origin-relative HTTP path");
      const readyUrl = new URL(readyPath, upstream);
      const startupTimeout = integerSetting(config.startupTimeoutMs ?? 20000, "bridge.process.startupTimeoutMs", 100, 25000);
      const shutdownTimeout = integerSetting(config.shutdownTimeoutMs ?? 3000, "bridge.process.shutdownTimeoutMs", 100, 8000);
      ctx.onStart(async signal => {
        if (!["127.0.0.1", "localhost", "[::1]"].includes(upstream.hostname)) throw new PluginError("SETTINGS", "Managed bridge processes require a loopback upstream");
        await requireUnusedPort(upstream);
        signal.throwIfAborted();
        child = spawn(executable, args, { cwd, shell: false, stdio: ["ignore", "inherit", "inherit"], windowsHide: true });
        const spawned = new Promise<void>((resolve, reject) => { child!.once("spawn", resolve); child!.once("error", reject); });
        // Retain an error listener for the full owned child lifetime.
        child.on("error", () => { if (ctx.running) ctx.fail(new Error("Managed bridge process failed")); });
        child.on("exit", () => { if (ctx.running) ctx.fail(new Error("Managed bridge process exited; in-flight external operations may need reconciliation")); });
        await spawned;
        await ready(readyUrl, startupTimeout, child, signal);
      });
      ctx.onStop(async () => {
        if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
        const exited = new Promise<void>(resolve => child!.once("exit", () => resolve()));
        child.kill("SIGTERM");
        const controller = new AbortController();
        try {
          await Promise.race([exited, delay(shutdownTimeout, undefined, { signal: controller.signal }).then(() => { if (child?.exitCode === null && child.signalCode === null) child.kill("SIGKILL"); })]);
        } finally { controller.abort(); }
        await exited;
      });
    }
    ctx.provide("console.http.handler", { handle: (request, response, context) => proxy(upstream, maximumBytes, request, response, context) });
  },
};
export default plugin;
