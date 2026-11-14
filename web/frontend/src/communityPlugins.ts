import type { PluginAccess, PluginNavigation, PluginPackage, PluginPackages, PluginTable } from '@mindie-motor/ui-sdk';
export type { PluginAccess, PluginNavigation, PluginPackage, PluginPackages, PluginTable } from '@mindie-motor/ui-sdk';
import { parseStrictJson } from "./api";

/** Declarative community pages never supply a script URL or executable markup. */

const identifier = /^[a-z][a-z0-9-]{0,63}$/;
export const MAX_PLUGIN_CONFIGURATION_BYTES = 32 * 1024;
export const MAX_PLUGIN_CONFIGURATION_INPUT_CHARACTERS = 4 * MAX_PLUGIN_CONFIGURATION_BYTES;
export function parsePluginConfiguration(input: string): Record<string, unknown> {
  if (input.length > MAX_PLUGIN_CONFIGURATION_INPUT_CHARACTERS)
    throw Error("插件配置文本过长，请减少缩进或空白后重试");
  const value: unknown = parseStrictJson(input);
  if (!record(value)) throw Error("插件配置须为 JSON 对象");
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > MAX_PLUGIN_CONFIGURATION_BYTES)
    throw Error("插件配置的紧凑 JSON UTF-8 编码不能超过 32 KiB（32,768 字节）");
  return value;
}
export function pluginIdentifier(input: unknown): input is string { return typeof input === "string" && identifier.test(input); }
export function pluginPackageSize(bytes: number): string {
  if (!Number.isSafeInteger(bytes) || bytes < 0) return "大小无效";
  if (bytes < 1024) return `${bytes} B`;
  const unit = bytes < 1024 * 1024 ? "KiB" : "MiB";
  const value = bytes / (unit === "KiB" ? 1024 : 1024 * 1024);
  return `${value.toFixed(1)} ${unit}（${bytes.toLocaleString("en-US")} B）`;
}
function text(input: unknown, maximum: number, empty = false): input is string {
  return typeof input === "string" && input.length <= maximum && (empty || !!input.trim()) && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(input);
}
function record(input: unknown): input is Record<string, unknown> { return !!input && typeof input === "object" && !Array.isArray(input); }
function access(input: unknown): input is PluginAccess { return input === "admin" || input === "operator" || input === "viewer"; }
export function pluginPageHash(value: Pick<PluginNavigation, "plugin_id" | "id">): string { return `plugin:${value.plugin_id}:${value.id}`; }
export function parsePluginPageHash(value: string): { plugin_id: string; id: string } | null {
  const parts = value.split(":");
  return parts.length === 3 && parts[0] === "plugin" && pluginIdentifier(parts[1]) && pluginIdentifier(parts[2])
    ? { plugin_id: parts[1], id: parts[2] } : null;
}
export function pluginPagePermitted(item: PluginNavigation, role: PluginAccess | "demo" | null): boolean {
  const ranks: Record<PluginAccess | "demo", number> = { viewer: 0, operator: 1, admin: 2, demo: 2 };
  return role !== null && ranks[role] >= ranks[item.access];
}
export function parsePluginNavigation(input: unknown): PluginNavigation[] {
  if (input === undefined) return [];
  if (!Array.isArray(input) || input.length > 256) throw Error("插件页面清单超出支持范围");
  const ids = new Set<string>();
  return input.map((value) => {
    if (record(value)) value = { ...value, description: value.description === undefined ? "" : value.description,
      group: value.group === undefined ? "插件" : value.group };
    if (!record(value) || !pluginIdentifier(value.plugin_id) || !pluginIdentifier(value.id)
        || !text(value.title, 100) || !text(value.description, 1000, true) || !text(value.group, 40)
        || value.kind !== "status-table" || !access(value.access) || typeof value.endpoint !== "string"
        || value.endpoint.length > 512 || !new RegExp(`^/api/plugins/${value.plugin_id}(?:/[a-zA-Z0-9_.-]+)*$`).test(value.endpoint)
        || value.endpoint.split("/").some((segment) => segment === "." || segment === ".."))
      throw Error("插件页面声明无效，未加载扩展页面");
    const key = pluginPageHash(value as PluginNavigation);
    if (ids.has(key)) throw Error("插件页面标识重复");
    ids.add(key);
    return value as PluginNavigation;
  });
}
export function parsePluginPackages(input: unknown): PluginPackages {
  if (!record(input) || input.schemaVersion !== 1 || !Number.isSafeInteger(input.revision) || Number(input.revision) < 0
      || typeof input.pending_restart !== "boolean" || !Array.isArray(input.items) || input.items.length > 1024)
    throw Error("插件包目录格式或版本无效");
  const digests = new Set<string>();
  const items = input.items.map((value) => {
    if (!record(value) || !pluginIdentifier(value.id) || !text(value.name, 200) || !text(value.version, 100)
        || typeof value.digest !== "string" || !/^[a-f0-9]{64}$/.test(value.digest) || digests.has(value.digest)
        || !text(value.license, 200) || !text(value.author, 200) || !text(value.description, 2000, true)
        || !access(value.access) || typeof value.selected !== "boolean" || typeof value.loaded !== "boolean"
        || value.blocked_reason !== undefined && !text(value.blocked_reason, 2000, true)
        || value.compatible_with_host !== undefined && typeof value.compatible_with_host !== "boolean")
      throw Error("插件包元信息无效或重复");
    digests.add(value.digest);
    return value as PluginPackage;
  });
  return { schemaVersion: 1, revision: Number(input.revision), pending_restart: input.pending_restart, items };
}
export function parsePluginTable(input: unknown): PluginTable {
  if (!record(input) || !Array.isArray(input.columns) || !input.columns.length || input.columns.length > 32
      || !Array.isArray(input.rows) || input.rows.length > 1000) throw Error("插件返回的表格格式或规模不受支持");
  const keys = new Set<string>();
  const columns = input.columns.map((value) => {
    if (!record(value) || typeof value.key !== "string" || !/^[a-zA-Z][a-zA-Z0-9_.-]{0,63}$/.test(value.key)
        || !text(value.label, 100) || keys.has(value.key)) throw Error("插件表格字段无效或重复");
    keys.add(value.key); return value as { key: string; label: string };
  });
  const rows = input.rows.map((value) => {
    if (!record(value) || Object.keys(value).length > 32) throw Error("插件表格行格式无效");
    const result: Record<string, string | number | boolean | null> = {};
    for (const { key } of columns) {
      const cell = Object.hasOwn(value, key) ? value[key] ?? null : null;
      if (!(cell === null || typeof cell === "boolean" || typeof cell === "number" && Number.isFinite(cell)
              && (!Number.isInteger(cell) || Number.isSafeInteger(cell))
            || text(cell, 4096, true))) throw Error("插件表格单元格须为有界纯文本、布尔值或有限数值；超出安全精度的整数请用文本返回");
      result[key] = cell as string | number | boolean | null;
    }
    return result;
  });
  for (const [key, maximum] of [["title", 200], ["summary", 2000], ["updated_at", 100]] as const)
    if (input[key] !== undefined && !text(input[key], maximum, true)) throw Error("插件表格说明无效");
  return { title: String(input.title ?? ""), summary: String(input.summary ?? ""),
    updated_at: String(input.updated_at ?? ""), columns, rows };
}
