import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import type { Socket } from "node:net";
import type { Plugin } from "@mindie/console-platform/sdk";

function stringSetting(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim() || /[\x00-\x1f\x7f]/.test(value)) throw new Error(`${label} must be a nonempty string without controls`);
  return value;
}
function integerSetting(value: unknown, label: string, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) throw new Error(`${label} must be an integer in ${minimum}..${maximum}`);
  return value;
}

const plugin: Plugin = {
  activate(ctx) {
    const handler = ctx.consume("console.http.handler");
    if (!handler || typeof handler.handle !== "function") throw new Error("Selected HTTP handler does not implement handle()");
    const host = stringSetting(ctx.settings.host ?? "127.0.0.1", "http.host");
    const port = integerSetting(ctx.settings.port ?? 8000, "http.port", 0, 65535);
    const timeoutMs = integerSetting(ctx.settings.requestTimeoutMs ?? 120000, "http.requestTimeoutMs", 100, 3600000);
    for (const key of Object.keys(ctx.settings)) if (!["host", "port", "requestTimeoutMs"].includes(key)) throw new Error(`HTTP transport: unsupported setting ${key}`);
    const sockets = new Set<Socket>();
    const requests = new Set<AbortController>();
    const server = createServer((request, response) => {
      if (!ctx.running) { response.writeHead(503, { "connection": "close" }).end(); return; }
      const controller = new AbortController(); requests.add(controller);
      const requestId = typeof request.headers["x-request-id"] === "string" && /^[A-Za-z0-9._:-]{1,128}$/.test(request.headers["x-request-id"]) ? request.headers["x-request-id"] : randomUUID();
      response.setHeader("x-console-generation", ctx.generation);
      response.setHeader("x-request-id", requestId);
      const expectedGeneration = request.headers["x-console-expected-generation"];
      if (expectedGeneration !== undefined && expectedGeneration !== "" && expectedGeneration !== ctx.generation) {
        requests.delete(controller);
        response.writeHead(409, { "content-type": "text/plain; charset=utf-8", "connection": "close" }).end("Provider generation changed; refresh before retrying. No request was forwarded.\n");
        return;
      }
      const now = Date.now();
      const requestedDeadline = request.headers["x-console-deadline-unix-ms"];
      const parsedDeadline = typeof requestedDeadline === "string" && /^\d{13}$/.test(requestedDeadline) ? Number(requestedDeadline) : now + timeoutMs;
      const deadline = Math.min(now + timeoutMs, parsedDeadline);
      if (deadline <= now) { requests.delete(controller); response.writeHead(504, { "connection": "close" }).end("Request deadline already expired.\n"); return; }
      const abort = () => { if (!response.writableFinished) controller.abort(new Error("HTTP client disconnected")); };
      request.once("aborted", abort); response.once("close", abort);
      const timer = setTimeout(() => {
        controller.abort(new Error("Request deadline exceeded; external write outcome may be unknown"));
        if (response.destroyed || response.writableEnded) return;
        if (response.headersSent) response.destroy();
        else response.writeHead(504, { "content-type": "text/plain; charset=utf-8" }).end("Request deadline exceeded; an external write may still require reconciliation.\n");
      }, Math.max(1, deadline - now));
      void Promise.resolve().then(() => handler.handle(request, response, { generation: ctx.generation, requestId, deadline, signal: controller.signal })).catch(() => {
        if (response.destroyed || response.writableEnded) return;
        if (response.headersSent) response.destroy();
        else response.writeHead(502, { "content-type": "text/plain; charset=utf-8" }).end("Selected HTTP provider failed; external write outcome may require reconciliation.\n");
      }).finally(() => {
        clearTimeout(timer); requests.delete(controller);
        request.removeListener("aborted", abort); response.removeListener("close", abort);
      });
    });
    server.headersTimeout = Math.min(timeoutMs, 60000);
    server.requestTimeout = timeoutMs;
    server.keepAliveTimeout = 5000;
    server.on("connection", socket => { sockets.add(socket); socket.once("close", () => sockets.delete(socket)); });
    server.on("clientError", (_error, socket) => { if (socket.writable) socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n"); });
    server.on("upgrade", (_request, socket) => socket.end("HTTP/1.1 501 Not Implemented\r\nConnection: close\r\n\r\n"));
    server.on("connect", (_request, socket) => socket.end("HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n"));
    // Handle listener faults explicitly after startup as well, rather than crashing on an unhandled event.
    server.on("error", () => { if (ctx.running) ctx.fail(new Error("HTTP listener failed")); });
    ctx.provide("console.http.transport", {
      address() { const value = server.address(); return value && typeof value !== "string" ? { host: value.address, port: value.port } : undefined; },
    });
    ctx.onStart(() => new Promise<void>((resolve, reject) => {
      const error = (reason: Error) => { server.removeListener("listening", listening); reject(reason); };
      const listening = () => { server.removeListener("error", error); resolve(); };
      server.once("error", error); server.once("listening", listening); server.listen(port, host);
    }));
    ctx.onStop(async () => {
      for (const request of requests) request.abort(new Error("Host draining; external write outcome may be unknown"));
      const closed = new Promise<void>(resolve => { server.close(() => resolve()); });
      for (const socket of sockets) socket.destroy();
      await closed;
    });
  },
};
export default plugin;
