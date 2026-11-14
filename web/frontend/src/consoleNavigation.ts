import {
  NavAccounts, NavAudit, NavCapabilities, NavGateways, NavImages,
  NavMaintenance, NavNodes, NavOverview, NavRuntimeLogs, NavServices,
  NavSettings, NavSystemLogs, NavTemplates,
} from "./icons";
import type { ConsoleIcon } from "./icons";
import { pageEnabled, type PluginCatalog } from "./consolePlugins";

export type ConsolePage = "overview" | "services" | "templates" | "nodes" | "images"
  | "gateways" | "logs" | "features" | "audit" | "systemlogs"
  | "accounts" | "maintenance" | "settings";
export type NavigationItem = {
  id: ConsolePage; label: string; description: string; icon: ConsoleIcon;
  access?: "admin" | "audit";
};
export type NavigationGroup = { label: string; items: readonly NavigationItem[] };
export type NavigationPermissions = { isAdmin: boolean; canAdminister: boolean };

export const navGroups: readonly NavigationGroup[] = [
  { label: "服务管理", items: [
    { id: "overview", label: "集群概览", description: "服务状态、节点资源与近期操作。", icon: NavOverview },
    { id: "services", label: "推理服务", description: "查看服务、编辑配置并跟踪部署任务。", icon: NavServices },
    { id: "gateways", label: "网关接入", description: "接入已有网关，核对模型发现结果与推理服务绑定。", icon: NavGateways },
    { id: "templates", label: "模板管理", description: "管理官方模板与自定义配置快照，用于创建推理服务。", icon: NavTemplates },
    { id: "features", label: "功能配置", description: "官方能力、适用运行时与原生配置入口。", icon: NavCapabilities },
  ] },
  { label: "资源管理", items: [
    { id: "nodes", label: "节点管理", description: "管理执行 Agent，并查看它所连接的 Kubernetes 集群节点。", icon: NavNodes },
    { id: "images", label: "节点镜像", description: "查看 Docker 节点的本地镜像库存与架构；Kubernetes 镜像由集群与镜像仓库管理。", icon: NavImages },
  ] },
  { label: "运维与治理", items: [
    { id: "logs", label: "运行日志", description: "查询各服务当前运行容器或工作负载的日志。", icon: NavRuntimeLogs },
    { id: "systemlogs", label: "系统日志", description: "控制台持久结构化日志与请求关联查询。", icon: NavSystemLogs, access: "admin" },
    { id: "audit", label: "操作审计", description: "查询控制台操作记录及执行结果。", icon: NavAudit, access: "audit" },
  ] },
  { label: "系统管理", items: [
    { id: "accounts", label: "账号管理", description: "管理本地账号、角色与访问状态。", icon: NavAccounts, access: "admin" },
    { id: "maintenance", label: "系统维护", description: "平台版本、受信发布计划与数据库备份。", icon: NavMaintenance, access: "admin" },
    { id: "settings", label: "系统设置", description: "界面外观、当前会话、节点连接与系统版本。", icon: NavSettings },
  ] },
];
const items = navGroups.flatMap((group) => group.items);
export const titles = Object.fromEntries(items.map((item) => [item.id, item.label])) as Record<ConsolePage, string>;
export const descriptions = Object.fromEntries(items.map((item) => [item.id, item.description])) as Record<ConsolePage, string>;
export function isConsolePage(value: string): value is ConsolePage {
  return Object.hasOwn(titles, value);
}
export function visibleNavigation(permissions: NavigationPermissions, plugins: PluginCatalog | null): NavigationGroup[] {
  return navGroups.map((group) => ({ ...group, items: group.items.filter((item) =>
    pageEnabled(plugins, item.id) && (item.access === "admin" ? permissions.isAdmin : item.access === "audit" ? permissions.canAdminister : true),
  ) })).filter((group) => group.items.length > 0);
}
export function pageGroup(page: ConsolePage): string {
  return navGroups.find((group) => group.items.some((item) => item.id === page))?.label ?? "控制台";
}

export function matchesServiceStatus(status: string, filter: string): boolean {
  return filter === "all" || (filter === "error" ? ["error", "failed"].includes(status) : status === filter);
}
