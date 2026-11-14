import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { ArrowRight, ExternalLink, ShieldCheck } from "./icons";
import { roleLabel, when } from "./api";
import type { AuthStatus, Meta, NodeInfo } from "./api";
import { Button, Copy } from "./ui";
import AppearanceSettings from "./AppearanceSettings";
import BrandingSettings from "./BrandingSettings";
import { recordText, recordTime } from "./operationRecords";
import { documentationUrl } from "./documentation";
import { REFRESH_OPTIONS, useRefreshPreference } from "./refreshPreferences";
import InfrastructureSettings from "./InfrastructureSettings";
import { readCount, type ReadState } from "./readState";
import PluginSettings from "./PluginSettings";
import { usePluginEnabled, type PluginCatalog } from "./consolePlugins";
import PluginPackagesPanel from "./PluginPackages";
import UiPluginPackages from "./UiPluginPackages";

function DefaultSettingsPage({
  meta,
  nodes,
  nodesRead,
  auth,
  onNodes,
  onPassword,
  onAccounts,
  pluginCatalog,
  pluginError,
  pluginsRefreshing,
  onPluginRefresh,
}: {
  meta: Meta | null;
  nodes: NodeInfo[];
  nodesRead: ReadState;
  auth: AuthStatus | null;
  onNodes?: () => void;
  onPassword: () => void;
  onAccounts?: () => void;
  pluginCatalog: PluginCatalog | null;
  pluginError: string;
  pluginsRefreshing: boolean;
  onPluginRefresh: () => void;
}) {
  const brandingEnabled = usePluginEnabled("branding");
  const user = auth?.user;
  const username = recordText(user?.username);
  const displayName = recordText(user?.display_name);
  const expires = recordTime(auth?.expires_at);
  const version = recordText(meta?.version);
  const commit = recordText(meta?.motor_commit);
  const docsUrl = documentationUrl(meta?.documentation_url);
  const [refreshInterval, setRefreshInterval] = useRefreshPreference();
  const runtimes = meta?.runtimes
    ?.filter((runtime) => runtime === "docker" || runtime === "kubernetes")
    .map((runtime) => (runtime === "docker" ? "Docker" : "Kubernetes"));
  return (
    <UiElement as="div" className="settings-grid">
      <AppearanceSettings />
      {(auth?.user?.role === "admin" || meta?.mode === "demo") && brandingEnabled && <BrandingSettings />}
      {(auth?.user?.role === "admin" || meta?.mode === "demo") && <PluginSettings catalog={pluginCatalog} error={pluginError} refreshing={pluginsRefreshing} onRefresh={onPluginRefresh} />}
      {(auth?.user?.role === "admin" || meta?.mode === "demo") && <PluginPackagesPanel key={auth?.user?.id ?? "demo"}
        identity={auth?.user?.id ?? "demo"} canManage={meta?.mode === "control" && auth?.user?.role === "admin"} onCatalogRefresh={onPluginRefresh} />}
      {(auth?.user?.role === "admin" || meta?.mode === "demo") && <UiPluginPackages key={`ui:${auth?.user?.id ?? "demo"}`}
        identity={auth?.user?.id ?? "demo"} canManage={meta?.mode === "control" && auth?.user?.role === "admin"} />}
      <InfrastructureSettings canAdminister={auth?.user?.role === "admin" || meta?.mode === "demo"} />
      <UiRegion name="layout.panel" as="section" className="panel">
        <UiElement as="div" className="panel-head"><UiElement as="h2"><UiText>{"数据刷新"}</UiText></UiElement></UiElement>
        <UiElement as="div" className="settings-content">
          <UiElement as="label" className="field">
            <UiElement as="span" className="field-label"><UiText>{"列表与概览自动刷新"}</UiText></UiElement>
            <UiElement as="select" aria-label="列表与概览自动刷新" value={refreshInterval} onChange={(event) => setRefreshInterval(Number(event.target.value))}>
              {REFRESH_OPTIONS.map(([interval, label]) => <UiElement as="option" key={interval} value={interval}>{label}</UiElement>)}
            </UiElement>
          </UiElement>
          <UiElement as="p" className="settings-context-note"><UiText>{"每项查询完成后再等待所选间隔；隐藏页面时暂停，返回后更新。手动刷新会合并正在执行的同一查询，保留筛选、滚动和未保存内容。"}</UiText></UiElement>
          <UiElement as="p" className="small-caption"><UiText>{"仅当前浏览器生效。日志跟随和任务进度使用各自的间隔；会话有效性独立检查。"}</UiText></UiElement>
        </UiElement>
      </UiRegion>
      <UiRegion name="layout.panel" as="section" className="panel">
        <UiElement as="div" className="panel-head">
          <UiElement as="h2"><UiText>{"当前会话"}</UiText></UiElement>
        </UiElement>
        <UiElement as="div" className="settings-content">
          <UiElement as="dl" className="description-list">
            {meta?.mode && (
              <>
                <UiElement as="dt"><UiText>{"运行环境"}</UiText></UiElement>
                <UiElement as="dd">
                  {meta.mode === "demo"
                    ? "演示环境 · 操作均为模拟"
                    : "控制台管理环境"}
                </UiElement>
              </>
            )}
            {username ? (
              <>
                <UiElement as="dt"><UiText>{"登录账号"}</UiText></UiElement>
                <UiElement as="dd">{username}</UiElement>
              </>
            ) : (
              meta?.mode === "demo" && (
                <>
                  <UiElement as="dt"><UiText>{"访问方式"}</UiText></UiElement>
                  <UiElement as="dd"><UiText>{"免登录演示会话"}</UiText></UiElement>
                </>
              )
            )}
            {displayName && displayName !== username && (
              <>
                <UiElement as="dt"><UiText>{"显示名称"}</UiText></UiElement>
                <UiElement as="dd">{displayName}</UiElement>
              </>
            )}
            {user && (
              <>
                <UiElement as="dt"><UiText>{"角色"}</UiText></UiElement>
                <UiElement as="dd">{roleLabel(user.role)}</UiElement>
              </>
            )}
            {expires && (
              <>
                <UiElement as="dt"><UiText>{"会话到期"}</UiText></UiElement>
                <UiElement as="dd">{when(expires)}</UiElement>
              </>
            )}
          </UiElement>
          <UiElement as="div" className="settings-actions">
            {user && user.auth_source !== "oidc" && (
              <Button actionId="action.SettingsPage.001" onClick={onPassword}><UiText>{"修改个人密码"}</UiText></Button>
            )}
            {onAccounts && (
              <Button actionId="action.SettingsPage.002" onClick={onAccounts}>
                <UiText>{"账号管理"}</UiText>
                <ArrowRight size={14} />
              </Button>
            )}
          </UiElement>
        </UiElement>
      </UiRegion>
      {onNodes && <UiRegion name="layout.panel" as="section" className="panel">
        <UiElement as="div" className="panel-head">
          <UiElement as="h2"><UiText>{"执行连接"}</UiText></UiElement>
        </UiElement>
        <UiElement as="div" className="settings-content">
          <UiElement as="dl" className="description-list">
            <UiElement as="dt"><UiText>{"已登记 Agent"}</UiText></UiElement>
            <UiElement as="dd" role="status">{readCount(nodesRead, nodes.length, "个")}</UiElement>
            {!!runtimes?.length && (
              <>
                <UiElement as="dt"><UiText>{"支持的部署方式"}</UiText></UiElement>
                <UiElement as="dd">{runtimes.join(" / ")}</UiElement>
              </>
            )}
          </UiElement>
          <UiElement as="p" className="settings-context-note">
            <UiText>{"Agent 负责管理连接与执行任务。集群节点和硬件资源可在节点管理中查看。"}</UiText>
          </UiElement>
          <Button actionId="action.SettingsPage.003" onClick={onNodes}>
            <UiText>{"查看节点"}</UiText>
            <ArrowRight size={14} />
          </Button>
        </UiElement>
      </UiRegion>}
      <UiRegion name="layout.panel" as="section" className="panel version-panel">
        <UiElement as="div" className="panel-head">
          <UiElement as="h2"><UiText>{"平台与引擎"}</UiText></UiElement>
          <ShieldCheck size={17} />
        </UiElement>
        <UiElement as="div" className="settings-content">
          <UiElement as="dl" className="description-list">
            {version && (
              <>
                <UiElement as="dt"><UiText>{"平台版本"}</UiText></UiElement>
                <UiElement as="dd">{version}</UiElement>
              </>
            )}
            {meta?.capabilities && (
              <>
                <UiElement as="dt"><UiText>{"官方能力目录"}</UiText></UiElement>
                <UiElement as="dd">{meta.capabilities.length} <UiText>{"项"}</UiText></UiElement>
              </>
            )}
          </UiElement>
          <UiElement as="details" className="settings-technical-detail">
            <UiElement as="summary"><UiText>{"适配信息"}</UiText></UiElement>
            {commit && (
              <UiElement as="dl" className="description-list">
                <UiElement as="dt"><UiText>{"引擎源码版本"}</UiText></UiElement>
                <UiElement as="dd">
                  <UiElement as="code" className="break-word">{commit}</UiElement>
                  <Copy value={commit} label="复制引擎源码版本" />
                </UiElement>
              </UiElement>
            )}
            {docsUrl && <UiElement as="a"
              className="text-link"
              href={docsUrl}
              target="_blank"
              rel="noreferrer"
            >
              <UiText>{"使用文档"}</UiText>
              <ExternalLink size={13} />
            </UiElement>}
          </UiElement>
        </UiElement>
      </UiRegion>
    </UiElement>
  );
}

const SettingsPage = withUiSlot("component.SettingsPage", DefaultSettingsPage);
export default SettingsPage;

// Reference package implementation exports.
export { DefaultSettingsPage };
