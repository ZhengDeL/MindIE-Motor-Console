import { constants } from "node:fs";
import type { Stats } from "node:fs";
import { lstat, open } from "node:fs/promises";
import { createHash, timingSafeEqual } from "node:crypto";
import { dirname, resolve } from "node:path";
import { parseTree } from "jsonc-parser";
import type { Node as JsonNode, ParseError } from "jsonc-parser";
import type { Plugin } from "@mindie-motor/host-sdk";
import { McpError } from "../../contracts.js";
import type { AuthProvider, JsonObject, Principal, ToolMetadata } from "../../contracts.js";

interface Credential {
  id: string; subject: string; role: string; token_sha256: string; expires_at: string; scopes: string[];
  backend_token_file: string; service_ids?: string[]; node_ids?: string[]; revoked_at?: string;
}
const writeScopes = new Set(["configuration:write", "deployment:plan", "deployment:apply"]);
const forbidden = (message = "Credential is missing, expired or revoked") => new McpError("UNAUTHENTICATED", message, 401);
function text(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim() || value.length > 4096 || /[\x00-\x1f\x7f]/.test(value)) throw new Error(`${label} must be a nonempty string without controls`);
  return value;
}
function strings(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.length > 4096 || value.some(item => typeof item !== "string")) throw new Error(`${label} must be a string array`);
  const result = value.map(item => text(item, label));
  if (new Set(result).size !== result.length) throw new Error(`${label} must be unique`);
  return result;
}
function timestamp(value: unknown): string {
  const date = text(value, "timestamp");
  const parts = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d{1,6})?(Z|[+-]\d\d:\d\d)$/.exec(date);
  if (!parts || !Number.isFinite(Date.parse(date))) throw new Error("Credential timestamps must be timezone-qualified ISO dates");
  const [year, month, day, hour, minute, second] = parts.slice(1, 7).map(Number);
  const days = new Date(Date.UTC(year!, month!, 0)).getUTCDate();
  if (month! < 1 || month! > 12 || day! < 1 || day! > days || hour! > 23 || minute! > 59 || second! > 59) throw new Error("Invalid credential timestamp");
  return date;
}
function sameIdentity(left: Stats, right: Stats): boolean {
  return left.dev === right.dev && left.ino === right.ino && left.mode === right.mode && left.ctimeMs === right.ctimeMs;
}
async function directoryChain(file: string): Promise<{ path: string; info: Stats }[]> {
  const paths: string[] = [];
  for (let path = dirname(resolve(file)); ; path = dirname(path)) {
    paths.push(path); if (dirname(path) === path) break;
  }
  const result: { path: string; info: Stats }[] = [];
  for (const path of paths.reverse()) {
    const info = await lstat(path);
    if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("Private credential directory chain contains a link or non-directory");
    result.push({ path, info });
  }
  return result;
}
async function verifyDirectoryChain(chain: readonly { path: string; info: Stats }[]): Promise<void> {
  for (const { path, info } of chain) {
    const current = await lstat(path);
    if (!current.isDirectory() || current.isSymbolicLink() || info.dev !== current.dev || info.ino !== current.ino || info.mode !== current.mode || info.birthtimeMs !== current.birthtimeMs) throw new Error("Private credential directory chain changed while reading");
  }
}
/** Bound reads; reject links in every component and recheck directory/file identities across the read. */
async function readPrivateFile(file: string, maximum: number): Promise<string> {
  const directories = await directoryChain(file);
  const before = await lstat(file);
  if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size > maximum) throw new Error("Private credential file must be a bounded regular file without links");
  if (process.platform !== "win32" && (before.mode & 0o077)) throw new Error("Private credential files must not grant group or other access");
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || !sameIdentity(opened, before) || opened.nlink !== 1 || opened.size !== before.size || opened.mtimeMs !== before.mtimeMs) throw new Error("Credential file changed while opening");
    await verifyDirectoryChain(directories);
    if (!sameIdentity(opened, await lstat(file))) throw new Error("Credential path changed while opening");
    const bytes = Buffer.alloc(maximum + 1);
    let total = 0;
    while (total <= maximum) {
      const { bytesRead } = await handle.read(bytes, total, bytes.length - total, null);
      if (!bytesRead) break;
      total += bytesRead;
    }
    const after = await handle.stat();
    const current = await lstat(file);
    if (total > maximum || total !== opened.size || !after.isFile() || after.nlink !== 1 || !sameIdentity(opened, after) || after.mtimeMs !== opened.mtimeMs || after.size !== opened.size || !current.isFile() || current.isSymbolicLink() || current.nlink !== 1 || !sameIdentity(opened, current) || current.mtimeMs !== opened.mtimeMs || current.size !== opened.size) throw new Error("Credential file changed while reading");
    await verifyDirectoryChain(directories);
    return bytes.subarray(0, total).toString("utf8");
  } finally { await handle.close(); }
}
function parseRegistryJson(source: string): unknown {
  const errors: ParseError[] = [];
  const tree = parseTree(source, errors, { disallowComments: true, allowTrailingComma: false, allowEmptyContent: false });
  if (!tree || errors.length) throw new Error("Invalid strict JSON registry");
  const visit = (node: JsonNode, depth = 0): void => {
    if (depth > 64) throw new Error("Registry JSON exceeds the nesting limit");
    if (node.type === "object") {
      const keys = new Set<string>();
      for (const property of node.children ?? []) {
        const key: unknown = property.children?.[0]?.value;
        if (typeof key !== "string" || keys.has(key)) throw new Error("Duplicate registry JSON key");
        keys.add(key);
      }
    }
    for (const child of node.children ?? []) visit(child, depth + 1);
  };
  visit(tree);
  return JSON.parse(source) as unknown;
}
async function registry(file: string): Promise<Credential[]> {
  try {
    const raw: unknown = parseRegistryJson(await readPrivateFile(file, 1024 * 1024));
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("Invalid registry");
    const doc = raw as Record<string, unknown>;
    if (doc.version !== 1 || !Array.isArray(doc.credentials) || doc.credentials.length > 4096 || Object.keys(doc).some(key => !["version", "credentials"].includes(key))) throw new Error("Invalid registry version or credentials");
    const ids = new Set<string>(); const hashes = new Set<string>();
    return doc.credentials.map((value: unknown) => {
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid credential");
      const item = value as Record<string, unknown>;
      if (Object.keys(item).some(key => !["id", "subject", "role", "token_sha256", "expires_at", "scopes", "backend_token_file", "service_ids", "node_ids", "revoked_at"].includes(key))) throw new Error("Unsupported credential field");
      const credential: Credential = {
        id: text(item.id, "id"), subject: text(item.subject, "subject"), role: text(item.role, "role"),
        token_sha256: text(item.token_sha256, "token_sha256"), expires_at: timestamp(item.expires_at),
        scopes: strings(item.scopes, "scopes"), backend_token_file: resolve(dirname(file), text(item.backend_token_file, "backend_token_file")),
      };
      if (!["viewer", "operator"].includes(credential.role) || !/^[a-f0-9]{64}$/.test(credential.token_sha256) || ids.has(credential.id) || hashes.has(credential.token_sha256)) throw new Error("Invalid or duplicate credential identity");
      if (credential.role === "viewer" && credential.scopes.some(scope => writeScopes.has(scope))) throw new Error("Viewer cannot receive mutation scopes");
      ids.add(credential.id); hashes.add(credential.token_sha256);
      if (item.service_ids !== undefined) credential.service_ids = strings(item.service_ids, "service_ids");
      if (item.node_ids !== undefined) credential.node_ids = strings(item.node_ids, "node_ids");
      if (item.revoked_at !== undefined) credential.revoked_at = timestamp(item.revoked_at);
      return credential;
    });
  } catch { throw new McpError("IDENTITY_UNAVAILABLE", "Private service identity registry is unavailable or invalid", 503); }
}
function active(credential: Credential | undefined): Credential {
  if (!credential || credential.revoked_at !== undefined || Date.parse(credential.expires_at) <= Date.now()) throw forbidden();
  return credential;
}
function fingerprint(credential: Credential): string { return createHash("sha256").update(JSON.stringify(credential)).digest("hex"); }
function principalOf(credential: Credential): Principal {
  return Object.freeze({ credentialId: credential.id, subject: credential.subject, role: credential.role,
    scopes: Object.freeze([...credential.scopes]),
    ...(credential.service_ids === undefined ? {} : { serviceIds: Object.freeze([...credential.service_ids]) }),
    ...(credential.node_ids === undefined ? {} : { nodeIds: Object.freeze([...credential.node_ids]) }),
  });
}
function targets(credential: Credential, value: unknown, depth = 0): void {
  if (depth > 64) throw new McpError("FORBIDDEN", "Tool arguments exceed authorization nesting limit", 403);
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) { for (const child of value) targets(credential, child, depth + 1); return; }
  for (const [key, child] of Object.entries(value)) {
    const allowed = key === "service_id" ? credential.service_ids : key === "node_id" ? credential.node_ids : undefined;
    if (allowed && (typeof child !== "string" || !allowed.includes(child))) throw new McpError("FORBIDDEN", "Target is outside this credential's authorization", 403);
    targets(credential, child, depth + 1);
  }
}
const plugin: Plugin = {
  async activate(ctx) {
    for (const key of Object.keys(ctx.settings)) if (!["registryFile", "stdioCredentialId"].includes(key)) throw new Error(`MCP identity: unsupported setting ${key}`);
    const file = resolve(text(ctx.settings.registryFile, "auth.registryFile"));
    const stdioId = ctx.settings.stdioCredentialId === undefined ? undefined : text(ctx.settings.stdioCredentialId, "auth.stdioCredentialId");
    await registry(file); // Invalid registries fail startup before any listener opens.
    const snapshots = new WeakMap<Principal, string>();
    const current = async (principal: Principal): Promise<Credential> => {
      const credential = active((await registry(file)).find(item => item.id === principal.credentialId));
      if (!snapshots.has(principal) || snapshots.get(principal) !== fingerprint(credential)) throw forbidden("Credential changed; authenticate again");
      return credential;
    };
    const provider: AuthProvider = {
      async authenticate(input) {
        const credentials = await registry(file);
        let credential: Credential | undefined;
        if (input.transport === "http") {
          if (!input.authorization || !/^Bearer [\x21-\x7e]{32,4096}$/.test(input.authorization)) throw forbidden();
          const hash = createHash("sha256").update(input.authorization.slice(7)).digest();
          credential = credentials.find(item => timingSafeEqual(hash, Buffer.from(item.token_sha256, "hex")));
        } else {
          if (!stdioId || (input.credentialId !== undefined && input.credentialId !== stdioId)) throw forbidden("A trusted local stdio credential must be configured");
          credential = credentials.find(item => item.id === stdioId);
        }
        const verified = active(credential); const principal = principalOf(verified);
        snapshots.set(principal, fingerprint(verified)); return principal;
      },
      async authorize(principal: Principal, tool: ToolMetadata, args: JsonObject) {
        const credential = await current(principal);
        if (!tool.requiredScopes.every(scope => credential.scopes.includes(scope))) throw new McpError("FORBIDDEN", "Credential lacks a required tool scope", 403);
        targets(credential, args);
      },
      async backendToken(principal) {
        const credential = await current(principal);
        let token: string;
        try { token = (await readPrivateFile(credential.backend_token_file, 4096)).trim(); }
        catch { throw new McpError("IDENTITY_UNAVAILABLE", "Downstream credential file is unavailable or invalid", 503); }
        if (!/^[\x21-\x7e]{32,4096}$/.test(token)) throw new McpError("IDENTITY_UNAVAILABLE", "Downstream credential file is invalid", 503);
        const hash = createHash("sha256").update(token).digest("hex");
        if ((await registry(file)).some(item => hash === item.token_sha256)) throw new McpError("IDENTITY_UNAVAILABLE", "MCP and downstream credentials must be distinct", 503);
        return token;
      },
    };
    ctx.provide("console.mcp.auth", provider);
  },
};
export default plugin;
