import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import type { ConsolePage } from "./consoleNavigation";
import { parsePluginNavigation, type PluginNavigation } from "./communityPlugins";

export type ConsolePlugin = {
  id: string; name: string; version: string; api_version: number; kind: string;
  enabled: boolean; required: boolean; description: string; dependencies: string[];
  capabilities: string[]; pages: string[]; runtimes: string[]; restart_required: boolean;
};
export type PluginCatalog = { schemaVersion: 1; digest: string; active_storage_plugin: string; items: ConsolePlugin[]; navigation: PluginNavigation[] };

/** The server selects installed, trusted plugins. No module URL or executable comes from HTTP. */
export function parsePluginCatalog(input: unknown): PluginCatalog {
  if (!input || typeof input !== "object") throw Error("插件清单格式无效");
  const value = input as Record<string, unknown>;
  if (value.schemaVersion !== 1 || typeof value.digest !== "string" || !/^[a-f0-9]{64}$/.test(value.digest)
      || !Array.isArray(value.items) || value.items.length > 256) throw Error("插件清单版本或摘要无效");
  const ids = new Set<string>();
  const array = (candidate: unknown): candidate is string[] => Array.isArray(candidate)
    && candidate.length <= 128 && candidate.every((item) => typeof item === "string" && item.length <= 256);
  const items = value.items.map((candidate) => {
    if (!candidate || typeof candidate !== "object") throw Error("插件条目格式无效");
    const item = candidate as Record<string, unknown>;
    if (typeof item.id !== "string" || !/^[a-z][a-z0-9.-]{0,63}$/.test(item.id) || ids.has(item.id)
        || typeof item.name !== "string" || !item.name || item.name.length > 200
        || typeof item.version !== "string" || item.version.length > 100 || item.api_version !== 1
        || typeof item.kind !== "string" || typeof item.description !== "string"
        || typeof item.enabled !== "boolean" || typeof item.required !== "boolean"
        || typeof item.restart_required !== "boolean" || !array(item.dependencies)
        || !array(item.capabilities) || !array(item.pages) || !array(item.runtimes)) throw Error("插件清单包含无效或重复条目");
    ids.add(item.id);
    return item as ConsolePlugin;
  });
  if (typeof value.active_storage_plugin !== "string" || !items.some((item) => item.id === value.active_storage_plugin
      && item.kind === "storage" && item.enabled)) throw Error("插件清单未声明有效的当前存储驱动");
  const navigation = parsePluginNavigation(value.navigation);
  if (navigation.some((entry) => !items.some((item) => item.id === entry.plugin_id && item.enabled)))
    throw Error("插件页面没有已启用的提供者");
  return { schemaVersion: 1, digest: value.digest, active_storage_plugin: value.active_storage_plugin, items, navigation };
}

export const PluginContext = createContext<PluginCatalog | null>(null);
export function pluginEnabled(catalog: PluginCatalog | null, id: string): boolean {
  return catalog?.items.some((item) => item.id === id && item.enabled) === true;
}
export function usePluginEnabled(id: string): boolean {
  return pluginEnabled(useContext(PluginContext), id);
}
export function runtimeEnabled(catalog: PluginCatalog | null, runtime: string): boolean {
  const id = runtime === "docker" ? "motor-docker" : runtime === "kubernetes" ? "motor-kubernetes" : "";
  return !!id && pluginEnabled(catalog, "services") && pluginEnabled(catalog, id);
}
export function runtimeReadOnlyReason(catalog: PluginCatalog | null, runtime: string): string {
  if (runtimeEnabled(catalog, runtime)) return "";
  if (!catalog) return "尚未确认运行时插件状态，已暂停执行和观测请求；已保存配置与本地输入保持不变。";
  const unavailable = !pluginEnabled(catalog, "services") ? "推理服务插件未启用" : runtime === "docker"
    ? "Docker 运行时插件未启用" : runtime === "kubernetes" ? "Kubernetes 运行时插件未启用" : "此服务运行时不可用";
  return `${unavailable}。本服务仅可查看已保存配置，执行与在线观测已暂停；这不表示模型已经停止。`;
}
export function useRuntimeAccess(runtime: string): { enabled: boolean; reason: string } {
  const catalog = useContext(PluginContext);
  return { enabled: runtimeEnabled(catalog, runtime), reason: runtimeReadOnlyReason(catalog, runtime) };
}
export function pageEnabled(catalog: PluginCatalog | null, page: ConsolePage): boolean {
  // Settings is the stable recovery surface even when plugin discovery fails.
  return page === "settings" || catalog?.items.some((item) => item.enabled && item.pages.includes(page)) === true;
}

/** Compile-time exhaustive page contributions; existing feature components retain their own state. */
export type ConsolePageViews = Readonly<Record<ConsolePage, () => ReactNode>>;
export function renderPageContribution(page: ConsolePage, catalog: PluginCatalog | null,
  views: ConsolePageViews): ReactNode {
  return pageEnabled(catalog, page) ? views[page]() : null;
}
