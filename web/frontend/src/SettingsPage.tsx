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

export default function SettingsPage({
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
    <div className="settings-grid">
      <AppearanceSettings />
      {(auth?.user?.role === "admin" || meta?.mode === "demo") && brandingEnabled && <BrandingSettings />}
      {(auth?.user?.role === "admin" || meta?.mode === "demo") && <PluginSettings catalog={pluginCatalog} error={pluginError} refreshing={pluginsRefreshing} onRefresh={onPluginRefresh} />}
      {(auth?.user?.role === "admin" || meta?.mode === "demo") && <PluginPackagesPanel key={auth?.user?.id ?? "demo"}
        identity={auth?.user?.id ?? "demo"} canManage={meta?.mode === "control" && auth?.user?.role === "admin"} onCatalogRefresh={onPluginRefresh} />}
      <InfrastructureSettings canAdminister={auth?.user?.role === "admin" || meta?.mode === "demo"} />
      <section className="panel">
        <div className="panel-head"><h2>数据刷新</h2></div>
        <div className="settings-content">
          <label className="field">
            <span className="field-label">列表与概览自动刷新</span>
            <select aria-label="列表与概览自动刷新" value={refreshInterval} onChange={(event) => setRefreshInterval(Number(event.target.value))}>
              {REFRESH_OPTIONS.map(([interval, label]) => <option key={interval} value={interval}>{label}</option>)}
            </select>
          </label>
          <p className="settings-context-note">每项查询完成后再等待所选间隔；隐藏页面时暂停，返回后更新。手动刷新会合并正在执行的同一查询，保留筛选、滚动和未保存内容。</p>
          <p className="small-caption">仅当前浏览器生效。日志跟随和任务进度使用各自的间隔；会话有效性独立检查。</p>
        </div>
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>当前会话</h2>
        </div>
        <div className="settings-content">
          <dl className="description-list">
            {meta?.mode && (
              <>
                <dt>运行环境</dt>
                <dd>
                  {meta.mode === "demo"
                    ? "演示环境 · 操作均为模拟"
                    : "控制台管理环境"}
                </dd>
              </>
            )}
            {username ? (
              <>
                <dt>登录账号</dt>
                <dd>{username}</dd>
              </>
            ) : (
              meta?.mode === "demo" && (
                <>
                  <dt>访问方式</dt>
                  <dd>免登录演示会话</dd>
                </>
              )
            )}
            {displayName && displayName !== username && (
              <>
                <dt>显示名称</dt>
                <dd>{displayName}</dd>
              </>
            )}
            {user && (
              <>
                <dt>角色</dt>
                <dd>{roleLabel(user.role)}</dd>
              </>
            )}
            {expires && (
              <>
                <dt>会话到期</dt>
                <dd>{when(expires)}</dd>
              </>
            )}
          </dl>
          <div className="settings-actions">
            {user && user.auth_source !== "oidc" && (
              <Button onClick={onPassword}>修改个人密码</Button>
            )}
            {onAccounts && (
              <Button onClick={onAccounts}>
                账号管理
                <ArrowRight size={14} />
              </Button>
            )}
          </div>
        </div>
      </section>
      {onNodes && <section className="panel">
        <div className="panel-head">
          <h2>执行连接</h2>
        </div>
        <div className="settings-content">
          <dl className="description-list">
            <dt>已登记 Agent</dt>
            <dd role="status">{readCount(nodesRead, nodes.length, "个")}</dd>
            {!!runtimes?.length && (
              <>
                <dt>支持的部署方式</dt>
                <dd>{runtimes.join(" / ")}</dd>
              </>
            )}
          </dl>
          <p className="settings-context-note">
            Agent 负责管理连接与执行任务。集群节点和硬件资源可在节点管理中查看。
          </p>
          <Button onClick={onNodes}>
            查看节点
            <ArrowRight size={14} />
          </Button>
        </div>
      </section>}
      <section className="panel version-panel">
        <div className="panel-head">
          <h2>平台与引擎</h2>
          <ShieldCheck size={17} />
        </div>
        <div className="settings-content">
          <dl className="description-list">
            {version && (
              <>
                <dt>平台版本</dt>
                <dd>{version}</dd>
              </>
            )}
            {meta?.capabilities && (
              <>
                <dt>官方能力目录</dt>
                <dd>{meta.capabilities.length} 项</dd>
              </>
            )}
          </dl>
          <details className="settings-technical-detail">
            <summary>适配信息</summary>
            {commit && (
              <dl className="description-list">
                <dt>引擎源码版本</dt>
                <dd>
                  <code className="break-word">{commit}</code>
                  <Copy value={commit} label="复制引擎源码版本" />
                </dd>
              </dl>
            )}
            {docsUrl && <a
              className="text-link"
              href={docsUrl}
              target="_blank"
              rel="noreferrer"
            >
              使用文档
              <ExternalLink size={13} />
            </a>}
          </details>
        </div>
      </section>
    </div>
  );
}
