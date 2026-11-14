import { PRODUCT_ID } from "./brand";

export type GrafanaNavigationConfiguration = { enabled: boolean; base_url: string; dashboard_path: string; revision: number };
export const GRAFANA_CONFIGURATION_SAVED = `${PRODUCT_ID}:grafana-configuration-saved`;

/** A configured destination is a navigation link, independent of probe freshness. */
export function grafanaDashboardHref(config: unknown): string {
  if (!config || typeof config !== "object") return "";
  const value = config as Partial<GrafanaNavigationConfiguration>;
  if (value.enabled !== true || typeof value.base_url !== "string" || !value.base_url ||
      typeof value.dashboard_path !== "string" || /[\\\s\u0000-\u001f\u007f]/.test(value.base_url)) return "";
  try {
    const base = new URL(value.base_url), path = value.dashboard_path;
    if ((path && !path.startsWith("/")) || path.startsWith("//") || /[\\\u0000-\u0020\u007f]/.test(path)) return "";
    const target = new URL(path.replace(/^\//, ""), base.href.replace(/\/?$/, "/"));
    return ["http:", "https:"].includes(target.protocol) && !base.username && !base.password &&
      !base.search && !base.hash && target.origin === base.origin &&
      target.pathname.startsWith(base.pathname.replace(/\/?$/, "/")) ? target.href : "";
  } catch { return ""; }
}

/** Publish only the safe navigation projection, never a Token or probe body. */
export function announceGrafanaConfiguration(value: GrafanaNavigationConfiguration) {
  window.dispatchEvent(new CustomEvent(GRAFANA_CONFIGURATION_SAVED, { detail: {
    enabled: value.enabled, base_url: value.base_url, dashboard_path: value.dashboard_path, revision: value.revision,
  } }));
}
