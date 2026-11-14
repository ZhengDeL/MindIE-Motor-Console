import { useEffect, useRef, useState } from "react";
import { api, download, downloadFile, post, when } from "./api";
import { Badge, Button, Confirm, Copy, ErrorBox, Field, InfoBox } from "./ui";
import { ExternalLink, RefreshCw } from "./icons";
import { usePollingQuery } from "./usePollingQuery";
import { readNotice } from "./readState";
import { knownText } from "./resourcePresentation";
import { observationTime } from "./resourceObservation";
import { serverNow, subscribeServerClock } from "./serverClock";
import { useUnsavedChanges } from "./unsavedChanges";
import { announceGrafanaConfiguration, grafanaDashboardHref } from "./integrationLinks";
import "./settings-infrastructure.css";
import { usePluginEnabled } from "./consolePlugins";

type Configuration = {
  storage: { backend: string; schema_version: number; mode?: string };
  logging: { directory: string; segment_bytes: number; rotations: number; window_bytes: number; source: string; change_requires_restart: boolean };
  paths?: { data: string; workspace: string };
};
type ConnectionStatus = { state: string; checked_at?: string | null; expires_at?: string | null; observed_at?: string; ttl_seconds?: number };
type Extension = { id: string; name: string; kind: string; version: string; enabled: boolean; state: string; detail?: string; capabilities?: string[];
  scope?: string; state_label?: string; external_system_verified?: boolean; connection_status?: ConnectionStatus };
type GrafanaConfiguration = { enabled: boolean; base_url: string; dashboard_path: string; has_api_token: boolean; revision: number; probe: Record<string, unknown> | null; connection_status?: ConnectionStatus };
type GrafanaDraft = Pick<GrafanaConfiguration, "enabled" | "base_url" | "dashboard_path">;
type ProvisionDownload = { path: string; filename: string };
type ProbeBoundary = { revision: number; probe: Record<string, unknown> | null; connection: ConnectionStatus; deadline: number | null; checked: number | null };
const blank: GrafanaDraft = { enabled: false, base_url: "", dashboard_path: "" };
const bytes = (value: number) => `${(value / 1048576).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} MiB`;
const connectionLabels: Record<string, { label: string; badge: string }> = {
  not_configured: { label: "未配置", badge: "draft" },
  disabled: { label: "未启用", badge: "stopped" },
  pending: { label: "待检查", badge: "pending" },
  passed: { label: "最近连接检查通过", badge: "pass" },
  failed: { label: "最近检查失败", badge: "fail" },
  expired: { label: "检查结果过期", badge: "warning" },
};
function connectionTimes(value: ConnectionStatus) {
  const checked = observationTime(value.checked_at), expires = observationTime(value.expires_at), observed = observationTime(value.observed_at);
  return value.ttl_seconds === 300 && checked !== null && expires !== null && observed !== null &&
    checked <= observed && expires - checked === 300_000 ? { checked, expires, observed } : null;
}
function connectionDeadline(value: ConnectionStatus | undefined, requestedAt: number): number | null {
  if (!value || !["passed", "failed"].includes(value.state)) return null;
  const times = connectionTimes(value);
  // Start at the request boundary, rather than extending freshness by response transit.
  return times ? requestedAt + Math.max(0, Math.min(times.expires - times.observed, times.expires - serverNow())) : null;
}
function connectionView(value: ConnectionStatus | undefined, deadline: number | null | undefined, clock: number) {
  if (!value || !Object.prototype.hasOwnProperty.call(connectionLabels, value.state)) return null;
  if (["passed", "failed"].includes(value.state)) {
    if (typeof deadline !== "number" || !Number.isFinite(deadline)) return null;
    const times = connectionTimes(value);
    if (!times) return null;
    return connectionLabels[clock >= deadline || times.expires <= serverNow() ? "expired" : value.state];
  }
  if (value.state === "expired") {
    const times = connectionTimes(value);
    if (!times || times.observed < times.expires) return null;
  }
  return connectionLabels[value.state];
}
function completedProbe(value: Record<string, unknown>, requestedAt: number): Omit<ProbeBoundary, "revision"> {
  const checked = observationTime(value.checked_at), started = observationTime(value.started_at), observed = serverNow();
  // Only the authenticated server receipt supplies completion time. If its clock
  // cannot yet be reconciled, revoke the old badge and wait for an authoritative read.
  if (value.status !== "available" && value.status !== "unavailable" || checked === null || started === null ||
      checked < started || checked > observed) return { probe: value, connection: { state: "pending" }, deadline: null, checked };
  const connection: ConnectionStatus = { state: observed - checked >= 300_000 ? "expired" : value.status === "available" ? "passed" : "failed",
    checked_at: value.checked_at as string, observed_at: new Date(observed).toISOString(),
    expires_at: new Date(checked + 300_000).toISOString(), ttl_seconds: 300 };
  return { probe: value, connection, deadline: connectionDeadline(connection, requestedAt), checked };
}
function olderProbe(connection: ConnectionStatus | undefined, boundary: ProbeBoundary): boolean {
  const times = connection && connectionTimes(connection);
  if (boundary.checked === null) return !times;
  if (!times || times.checked < boundary.checked) return true;
  const expected = boundary.probe?.status === "available" ? "passed" : boundary.probe?.status === "unavailable" ? "failed" : null;
  return times.checked === boundary.checked && expected !== null && connection?.state !== expected && connection?.state !== "expired";
}
function nativeChannel(extension: Extension) {
  if (extension.external_system_verified !== false) return null;
  return extension.scope === "native-configuration" ? "原生配置通道" :
    extension.scope === "existing-gateway-connection" ? "已有网关接入" : null;
}
export default function InfrastructureSettings({ canAdminister }: { canAdminister: boolean }) {
  const grafanaEnabled = usePluginEnabled("grafana");
  const ccaeEnabled = usePluginEnabled("ccae");
  const gatewaysEnabled = usePluginEnabled("gateways");
  const [configuration, setConfiguration] = useState<Configuration | null>(null);
  const [configurationError, setConfigurationError] = useState("");
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const [extensionError, setExtensionError] = useState("");
  const [remote, setRemote] = useState<GrafanaConfiguration | null>(null);
  const latestRemote = useRef<GrafanaConfiguration | null>(null), probeBoundary = useRef<ProbeBoundary | null>(null), configurationConflict = useRef(false);
  const [draft, setDraft] = useState<GrafanaDraft>(blank);
  // A read received during editing must not advance the draft's write baseline.
  const draftRevision = useRef<number | null>(null);
  const dirty = useRef(false);
  const [edited, setEdited] = useState(false);
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [grafanaError, setGrafanaError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState("");
  useUnsavedChanges(edited || !!busy);
  const [reloadOpen, setReloadOpen] = useState(false);
  const [probe, setProbe] = useState<Record<string, unknown> | null>(null);
  const [lastProvision, setLastProvision] = useState<ProvisionDownload | null>(null);
  const operationFlight = useRef(false), mounted = useRef(true), downloadAbort = useRef<AbortController | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; downloadAbort.current?.abort(new DOMException("页面已离开，取消下载等待", "AbortError")); };
  }, []);
  const extensionsRequestedAt = useRef(0), grafanaRequestedAt = useRef(0);
  const [extensionDeadlines, setExtensionDeadlines] = useState<Record<string, number | null>>({});
  const [grafanaDeadline, setGrafanaDeadline] = useState<number | null>(null);
  const [localClock, setLocalClock] = useState(() => performance.now());
  const clock = Math.max(localClock, performance.now());
  useEffect(() => {
    const refresh = () => setLocalClock(performance.now());
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("pageshow", refresh);
    const unsubscribeClock = subscribeServerClock(refresh);
    return () => { document.removeEventListener("visibilitychange", refresh); window.removeEventListener("pageshow", refresh); unsubscribeClock(); };
  }, []);
  useEffect(() => {
    const now = performance.now();
    const deadlines = [...Object.values(extensionDeadlines), grafanaDeadline]
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value) && value > now);
    if (!deadlines.length) return;
    const timer = window.setTimeout(() => setLocalClock(performance.now()), Math.max(1, Math.min(...deadlines) - now + 1));
    return () => window.clearTimeout(timer);
  }, [extensionDeadlines, grafanaDeadline, localClock]);
  const configQuery = usePollingQuery("system-configuration", (signal) => api<Configuration>("/system/configuration", { signal }), {
    enabled: canAdminister,
    onSuccess: (value) => { setConfiguration(value); setConfigurationError(""); }, onError: (failure) => setConfigurationError(failure.message),
  });
  const extensionsQuery = usePollingQuery("extensions", (signal) => {
    extensionsRequestedAt.current = performance.now();
    return api<{ items: Extension[] }>("/extensions", { signal });
  }, {
    onSuccess: (value) => {
      const boundary = probeBoundary.current;
      const items = value.items.map((item) => item.id === "observability.grafana" && boundary && olderProbe(item.connection_status, boundary)
        ? { ...item, connection_status: boundary.connection } : item);
      setExtensions(items); setExtensionError("");
      setExtensionDeadlines(Object.fromEntries(items.map((item) => [item.id, item.id === "observability.grafana" && boundary && item.connection_status === boundary.connection
        ? boundary.deadline : connectionDeadline(item.connection_status, extensionsRequestedAt.current)])));
    }, onError: (failure) => setExtensionError(failure.message),
  });
  const accept = (value: GrafanaConfiguration, requestedAt = performance.now()) => {
    if (latestRemote.current && value.revision < latestRemote.current.revision) return;
    let deadline = connectionDeadline(value.connection_status, requestedAt);
    const boundary = probeBoundary.current;
    if (boundary && value.revision === boundary.revision && olderProbe(value.connection_status, boundary)) {
      value = { ...value, probe: boundary.probe, connection_status: boundary.connection }; deadline = boundary.deadline;
    } else if (boundary && value.revision > boundary.revision) {
      probeBoundary.current = null;
      extensionsQuery.cancel();
      setExtensions((items) => items.map((item) => item.id === "observability.grafana" ? { ...item, connection_status: value.connection_status ?? { state: "pending" } } : item));
      setExtensionDeadlines((deadlines) => ({ ...deadlines, "observability.grafana": deadline }));
      extensionsQuery.invalidate();
    } else if (boundary && value.revision === boundary.revision) {
      probeBoundary.current = { revision: value.revision, probe: value.probe, connection: value.connection_status ?? { state: "pending" },
        deadline, checked: observationTime(value.probe?.checked_at) };
    }
    latestRemote.current = value; setRemote(value); setProbe(value.probe ?? null);
    setGrafanaDeadline(deadline);
    if (!dirty.current) {
      draftRevision.current = value.revision;
      setDraft({ enabled: value.enabled, base_url: value.base_url, dashboard_path: value.dashboard_path });
      setToken(""); setClearToken(false); setEdited(false);
    }
  };
  const grafanaQuery = usePollingQuery("grafana-configuration", (signal) => {
    grafanaRequestedAt.current = performance.now();
    return api<GrafanaConfiguration>("/extensions/grafana", { signal });
  }, {
    enabled: grafanaEnabled,
    onSuccess: (value) => { accept(value, grafanaRequestedAt.current); if (!configurationConflict.current) setGrafanaError(""); },
    onError: (failure) => { if (!configurationConflict.current) setGrafanaError(failure.message); },
  });
  const change = (values: Partial<GrafanaDraft>) => {
    dirty.current = true; setEdited(true); setFeedback(""); setDraft((previous) => ({ ...previous, ...values }));
  };
  const revokeCheck = () => {
    grafanaQuery.cancel(); extensionsQuery.cancel();
    const current = latestRemote.current;
    if (!current) return;
    const connection = { state: "pending" };
    probeBoundary.current = null;
    latestRemote.current = { ...current, connection_status: connection };
    setRemote(latestRemote.current); setGrafanaDeadline(null);
    setExtensions((items) => items.map((item) => item.id === "observability.grafana" ? { ...item, connection_status: connection } : item));
    setExtensionDeadlines((deadlines) => ({ ...deadlines, "observability.grafana": null }));
  };
  const refreshCheck = () => { grafanaQuery.invalidate(); extensionsQuery.invalidate(); };
  const save = async () => {
    if (!remote || draftRevision.current === null || busy || operationFlight.current) return;
    operationFlight.current = true;
    revokeCheck();
    setBusy("save"); setGrafanaError(""); setFeedback("");
    try {
      const value = await api<GrafanaConfiguration>("/extensions/grafana", {
        method: "PUT", body: JSON.stringify({ ...draft, expected_revision: draftRevision.current,
          ...(token ? { api_token: token } : {}), ...(clearToken ? { clear_api_token: true } : {}) }),
      });
      if (!mounted.current) return;
      configurationConflict.current = false; dirty.current = false; accept(value);
      announceGrafanaConfiguration(value);
      setFeedback("连接配置已保存；是否可用以实际检查结果为准。");
    } catch (reason) {
      if (!mounted.current) return;
      const failure = reason as Error & { status?: number };
      configurationConflict.current = failure.status === 409;
      setGrafanaError(failure.status === 409 ? "配置已被更新，本地输入已保留。请对照部署信息后重新载入，再应用更改。" : failure.message);
    } finally { operationFlight.current = false; if (mounted.current) { setBusy(""); refreshCheck(); } }
  };
  const downloadProvision = async (value: ProvisionDownload) => {
    const controller = new AbortController();
    downloadAbort.current = controller;
    const timer = window.setTimeout(() => controller.abort(new DOMException("ZIP 下载超过 30 秒", "TimeoutError")), 30_000);
    try {
      await downloadFile(value.path, value.filename, { signal: controller.signal });
    } catch (reason) {
      if (controller.signal.aborted && controller.signal.reason?.name === "TimeoutError")
        throw Error("ZIP 下载超过 30 秒。配置已保留，可再次下载上次配置包，无需重复生成。");
      throw reason;
    } finally {
      window.clearTimeout(timer);
      if (downloadAbort.current === controller) downloadAbort.current = null;
    }
  };
  const downloadFeedback = "ZIP 配置包已下载，包含 YAML、目标文件、看板和 SHA256 清单。范围为 Docker Coordinator；K8S、宿主和直接引擎采集目标需另行配置，安装与重载尚未执行。";
  const retryDownload = async () => {
    if (!lastProvision || busy || operationFlight.current) return;
    operationFlight.current = true;
    setBusy("download"); setGrafanaError(""); setFeedback("正在下载上次成功生成的 ZIP 配置包。");
    try {
      await downloadProvision(lastProvision);
      if (mounted.current) setFeedback(downloadFeedback);
    } catch (reason) {
      if (mounted.current) {
        setFeedback("下载未完成；再次下载上次配置包只会重试读取，安装与重载尚未执行。");
        setGrafanaError((reason as Error).message);
      }
    }
    finally { operationFlight.current = false; if (mounted.current) setBusy(""); }
  };
  const perform = async (action: "probe" | "provision") => {
    if (busy || edited || !remote?.enabled || operationFlight.current) return;
    operationFlight.current = true;
    const probeRevision = latestRemote.current?.revision, requestedAt = performance.now();
    if (action === "probe") { configurationConflict.current = false; revokeCheck(); }
    if (action === "provision") setLastProvision(null);
    setBusy(action); setGrafanaError(""); setFeedback("");
    let packageCreated = false;
    try {
      const value = await post<Record<string, unknown>>(`/extensions/grafana/${action}`);
      if (!mounted.current) return;
      if (action === "probe") {
        const current = latestRemote.current;
        if (probeRevision !== undefined && current && current.revision === probeRevision) {
          const observation = completedProbe(value, requestedAt);
          probeBoundary.current = { revision: probeRevision, ...observation };
          latestRemote.current = { ...current, probe: value, connection_status: observation.connection };
          setRemote(latestRemote.current); setProbe(value); setGrafanaDeadline(observation.deadline);
          setExtensions((items) => items.map((item) => item.id === "observability.grafana" ? { ...item, connection_status: observation.connection } : item));
          setExtensionDeadlines((deadlines) => ({ ...deadlines, "observability.grafana": observation.deadline }));
        }
        setFeedback("检查已返回，请查看检查明细。该结果不代表模型已完成业务验收。");
      } else {
        const identifier = value.operation_id;
        if (typeof identifier === "string" && /^grafana_[0-9a-f]{32}$/.test(identifier) &&
            value.download_path === `/extensions/grafana/provisions/${identifier}/download`) {
          const artifact = { path: value.download_path as string, filename: `qinghuan-grafana-${identifier.slice(8)}.zip` };
          setLastProvision(artifact);
          packageCreated = true;
          setFeedback("配置已生成，正在下载 ZIP 配置包；未执行安装。");
          await downloadProvision(artifact);
          if (mounted.current) setFeedback(downloadFeedback);
        } else if (value.operation_id === undefined && value.download_path === undefined) {
          download(value, "qinghuan-grafana-provisioning-manifest.json");
          setFeedback("当前后端仅提供 JSON 生成清单，实际文件仍在控制数据目录 plugins/grafana；尚未下载 ZIP 配置包或执行安装。");
        } else {
          throw Error("配置已生成，但下载入口无法确认。请核对后端版本，未自动重试生成。");
        }
      }
      if (mounted.current && action !== "probe") extensionsQuery.invalidate();
    } catch (reason) {
      if (mounted.current) {
        if (action === "probe" && probeRevision !== undefined && latestRemote.current?.revision === probeRevision) {
          // An HTTP error is not a completed health observation. Do not let an
          // older successful GET turn this uncertain check green again.
          probeBoundary.current = { revision: probeRevision, probe: null, connection: { state: "pending" }, deadline: null, checked: serverNow() };
          setProbe(null);
          configurationConflict.current = (reason as Error & { status?: number }).status === 409;
        }
        if (packageCreated) setFeedback("配置已生成，ZIP 下载未完成；可使用“下载上次配置包”重试读取，不会重新生成。");
        setGrafanaError((reason as Error).message);
      }
    }
    finally { operationFlight.current = false; if (mounted.current) { setBusy(""); if (action === "probe") refreshCheck(); } }
  };
  const reload = () => { configurationConflict.current = false; dirty.current = false; setEdited(false); setToken(""); setClearToken(false); grafanaQuery.invalidate(); };
  const link = grafanaDashboardHref(remote);
  const grafanaStatus = connectionView(remote?.connection_status, grafanaDeadline, clock);
  const visibleExtensions = extensions.filter((extension) => extension.id === "observability.grafana" ? grafanaEnabled
    : extension.id === "native.ccae" ? ccaeEnabled : extension.id === "gateway.existing" ? gatewaysEnabled : true);
  return <>
    {canAdminister && <section className="panel">
      <div className="panel-head"><h2>存储与日志</h2><Button loading={configQuery.refreshing} onClick={() => void configQuery.refresh()}><RefreshCw size={14} />读取配置</Button></div>
      <div className="settings-content">
        {configurationError && <ErrorBox message={configurationError} />}
        {configuration && <>
          <dl className="description-list">
            {knownText(configuration.storage?.backend) && <><dt>控制数据库</dt><dd>{({ sqlite: "SQLite", postgresql: "PostgreSQL", postgres: "PostgreSQL" } as Record<string, string>)[configuration.storage.backend] ?? configuration.storage.backend}</dd></>}
            {Number.isSafeInteger(configuration.storage?.schema_version) && <><dt>数据库结构版本</dt><dd>{configuration.storage.schema_version}</dd></>}
            {knownText(configuration.logging?.directory) && <><dt>请求日志目录</dt><dd><code className="break-word">{configuration.logging.directory}</code><Copy value={configuration.logging.directory} /></dd></>}
            {configuration.logging?.segment_bytes > 0 && <><dt>单段日志上限</dt><dd>{bytes(configuration.logging.segment_bytes)}</dd></>}
            {Number.isSafeInteger(configuration.logging?.rotations) && <><dt>轮转备份</dt><dd>{configuration.logging.rotations} 个</dd></>}
            {configuration.logging?.window_bytes > 0 && <><dt>日志窗口上限</dt><dd>{bytes(configuration.logging.window_bytes)}</dd></>}
          </dl>
          <p className="settings-context-note">路径与轮转由部署配置管理，修改后按维护流程重启生效。审计保存在控制数据库；Agent 资源账本、引擎日志与请求日志分别管理。</p>
        </>}
      </div>
    </section>}
    <section className="panel">
      <div className="panel-head"><h2>模块与扩展</h2><Button loading={extensionsQuery.refreshing} onClick={() => void extensionsQuery.refresh()}><RefreshCw size={14} />刷新</Button></div>
      <div className="settings-content">
        {extensionError && <ErrorBox message={extensionError} />}
        {visibleExtensions.map((extension) => {
          const connection = connectionView(extension.connection_status, extensionDeadlines[extension.id], clock);
          const channel = nativeChannel(extension);
          return <div className="settings-extension" key={extension.id}>
            <div><strong>{extension.name}</strong>{knownText(extension.version) && <span className="tag">模块版本 {extension.version}</span>}</div>
            {(connection || channel) && <div className="settings-extension-status">
              {connection && <Badge status={connection.badge}>{connection.label}</Badge>}
              {channel && <><Badge status="stopped">{channel}</Badge><span className="settings-extension-scope">外部系统未验收</span></>}
            </div>}
            {knownText(extension.detail) && <p>{extension.detail}</p>}
          </div>;
        })}
        {!extensionsQuery.hasSuccess && <p className="settings-context-note" role="status">{readNotice({ loaded: false, refreshing: extensionsQuery.refreshing, error: extensionError })}</p>}
        {extensionsQuery.hasSuccess && !visibleExtensions.length && !extensionError && <p className="settings-context-note">当前没有已启用的集成模块。</p>}
      </div>
    </section>
    {grafanaEnabled && <section className="panel">
      <div className="panel-head"><h2>Grafana 观测连接</h2>{link && <a className="text-link" href={link} target="_blank" rel="noopener noreferrer">打开仪表板<ExternalLink size={14} /></a>}</div>
      <div className="settings-content">
        {grafanaError && <ErrorBox message={grafanaError} />}
        {feedback && <InfoBox>{feedback}</InfoBox>}
        <p className="settings-context-note">接入已有 Grafana，保留其独立登录。连接、检查和离线配置生成是不同操作；保存地址不会安装观测栈或产生指标。</p>
        {grafanaStatus && <div className="settings-extension-status" role="status">
          <Badge status={grafanaStatus.badge}>{grafanaStatus.label}</Badge>
          {remote?.connection_status?.checked_at && connectionTimes(remote.connection_status) &&
            <span className="settings-extension-scope">检查完成于 {when(remote.connection_status.checked_at)}</span>}
        </div>}
        <p className="settings-context-note">连接检查结果有效期为 5 分钟，仅检查 Grafana 的健康和只读访问；指标采集、看板数据及模型业务需分别验收。到期只更新显示，不会自动发起外部检查。</p>
        <fieldset className="settings-extension-form" disabled={!canAdminister || !!busy || !remote}>
          <label className="checkbox-label"><input type="checkbox" checked={draft.enabled} onChange={(event) => change({ enabled: event.target.checked })} />启用 Grafana 连接</label>
          <Field label="Grafana 基础地址" hint="需由部署配置允许此来源，建议使用 HTTPS。">
            <input type="url" value={draft.base_url} placeholder="https://grafana.example.com" onChange={(event) => change({ base_url: event.target.value })} />
          </Field>
          <Field label="仪表板路径" hint="同一 Grafana 下的路径；填写 / 打开其首页。"><input value={draft.dashboard_path} placeholder="/d/motor/overview" onChange={(event) => change({ dashboard_path: event.target.value })} /></Field>
          <Field label="检查用 API Token" hint={remote?.has_api_token ? "已配置凭据；留空保留，凭据不回显。" : "可选；仅用于服务端检查，不加入仪表板链接。"}>
            <input type="password" autoComplete="new-password" data-private="true" value={token} disabled={clearToken} onChange={(event) => { change({}); setToken(event.target.value); }} />
          </Field>
          {remote?.has_api_token && <label className="checkbox-label"><input type="checkbox" checked={clearToken} onChange={(event) => { change({}); setClearToken(event.target.checked); setToken(""); }} />清除已保存的 API Token</label>}
        </fieldset>
        <div className="settings-actions settings-extension-actions">
          {canAdminister && <>
            <Button kind="primary" disabled={!edited || !remote} loading={busy === "save"} onClick={() => void save()}>保存连接</Button>
            <Button disabled={!remote || !!busy} onClick={() => edited ? setReloadOpen(true) : reload()}>重新载入</Button>
            <Button disabled={edited || !remote?.enabled || !!busy} loading={busy === "probe"} onClick={() => void perform("probe")}>检查连接</Button>
            <Button disabled={edited || !remote?.enabled || !!busy} loading={busy === "provision"} onClick={() => void perform("provision")}>生成并下载 ZIP 配置包</Button>
            {lastProvision && <Button disabled={!!busy} loading={busy === "download"} onClick={() => void retryDownload()}>下载上次配置包</Button>}
          </>}
        </div>
        {edited && <p className="small-caption">有未保存更改，检查与配置生成将在保存后启用。</p>}
        {probe && <details className="settings-technical-detail"><summary>查看最近检查明细</summary><pre className="json-view">{JSON.stringify(probe, null, 2)}</pre></details>}
      </div>
    </section>}
    {reloadOpen && <Confirm title="重新载入连接配置？" description="本地未保存的更改将被放弃；已保存的服务和模型不受影响。" confirmText="放弃并载入" onClose={() => setReloadOpen(false)} onConfirm={() => { setReloadOpen(false); reload(); }} />}
  </>;
}
