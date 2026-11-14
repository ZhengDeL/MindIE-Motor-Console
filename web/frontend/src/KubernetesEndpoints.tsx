import { useState } from "react";
import { api, download, isUnsubmittedDraft, when, type Service } from "./api";
import { Download, RefreshCw } from "./icons";
import { knownText } from "./resourcePresentation";
import { observationIsFresh, observationTime, useObservationClock } from "./resourceObservation";
import { usePollingQuery } from "./usePollingQuery";
import { useRefreshPreference } from "./refreshPreferences";
import { Button, Copy, Empty, ErrorBox, InfoBox, Spinner } from "./ui";
import { useRuntimeAccess } from "./consolePlugins";

type Endpoint = {
  kind: "inference" | "management" | "observability";
  type: "NodePort" | "LoadBalancer";
  url: string;
  ready: boolean;
  port_name: string;
  service_name: string;
  service_uid: string;
  node_ip?: string;
};
type Endpoints = {
  service_id: string;
  runtime: "kubernetes";
  observed_at: string | null;
  expires_at: string | null;
  source: "kubernetes_services";
  status: "ready" | "partial" | "unpublished" | "unavailable";
  items: Endpoint[];
  warnings: string[];
};
const purpose = { inference: "推理接口", management: "管理接口", observability: "观测接口" };
function validUrl(item: Endpoint): boolean {
  try {
    const value = new URL(item.url);
    return ["http:", "https:"].includes(value.protocol) && !!value.hostname &&
      !value.username && !value.password && !value.search && !value.hash;
  } catch { return false; }
}
function validate(value: Endpoints, serviceId: string): Endpoints {
  if (!value || value.service_id !== serviceId || value.runtime !== "kubernetes" || value.source !== "kubernetes_services" ||
    !["ready", "partial", "unpublished", "unavailable"].includes(value.status) || !Array.isArray(value.items) || !Array.isArray(value.warnings) ||
    value.warnings.some((warning) => typeof warning !== "string") || value.items.some((item) => !item ||
      !["inference", "management", "observability"].includes(item.kind) || !["NodePort", "LoadBalancer"].includes(item.type) ||
      typeof item.ready !== "boolean" || !knownText(item.service_name) || !knownText(item.service_uid) || !validUrl(item)))
    throw Error("访问入口响应不符合约定，未展示地址。请核对平台与 Agent 版本。");
  return value;
}

/** Published Service addresses are independent from native config and internal inference routing. */
export default function KubernetesEndpoints({ service }: { service: Service }) {
  const runtimeAccess = useRuntimeAccess(service.runtime);
  const [value, setValue] = useState<Endpoints | null>(null);
  const [error, setError] = useState("");
  const [interval] = useRefreshPreference();
  const now = useObservationClock();
  const draft = isUnsubmittedDraft(service);
  const query = usePollingQuery(`kubernetes-endpoints:${service.id}:${service.config_revision}:${service.deployed_digest}:${service.last_job_id}:${draft}`,
    async (signal) => validate(await api<Endpoints>(`/services/${encodeURIComponent(service.id)}/endpoints`, { signal }), service.id), {
      enabled: runtimeAccess.enabled && service.runtime === "kubernetes" && !draft,
      intervalMs: interval,
      maxIntervalMs: 10_000,
      invalidateOn: [`/services/${service.id}`],
      onSuccess: (response) => { setValue(response); setError(""); },
      onError: (failure) => { setValue(null); setError(failure.message); },
    });
  const fresh = query.hasSuccess && value !== null && value.service_id === service.id && observationIsFresh(value.observed_at, value.expires_at, now);
  const expiry = observationTime(value?.expires_at);
  const expired = query.hasSuccess && expiry !== null && expiry <= now;
  const items = fresh && value && ["ready", "partial"].includes(value.status)
    ? value.items.filter((item) => item.type === "LoadBalancer" || item.ready) : [];
  if (!runtimeAccess.enabled) return <InfoBox>{runtimeAccess.reason}</InfoBox>;
  return <section className="panel kubernetes-endpoints-panel">
    <div className="panel-head"><h2>访问入口</h2><Button kind="text" title="刷新 Kubernetes 访问入口" disabled={draft} loading={query.refreshing} onClick={() => void query.refresh()}><RefreshCw size={14} /></Button></div>
    {error && <ErrorBox message={error} />}
    {!draft && !query.hasSuccess && !error ? <Spinner label="正在核对 Kubernetes 访问入口" /> : items.length ? <>
      {items.map((item, index) => <div className="settings-content" key={`${item.service_uid}:${item.port_name}:${item.url}:${index}`}>
        <div><strong>{purpose[item.kind]}</strong> <span className="tag">{item.type}</span></div>
        <div className="endpoint-box"><code className="break-word">{item.url}</code><Copy value={item.url} label={`复制${purpose[item.kind]}地址`} /></div>
        <p className="small-caption break-word">{item.service_name}{knownText(item.port_name) ? ` · ${item.port_name}` : ""}</p>
        <p className="small-caption">{item.type === "LoadBalancer" ? "负载均衡入口，连通性未验证。" : "Service 发布配置与目标节点已核验；客户端连通性仍取决于网络访问策略。"}</p>
      </div>)}
      <div className="settings-content"><Button onClick={() => download({ service_id: service.id, source: value!.source, observed_at: value!.observed_at, expires_at: value!.expires_at, items, warnings: value!.warnings }, `${service.name}-connections.json`)}><Download size={14} />导出连接信息</Button></div>
    </> : <Empty title={draft ? "尚未提交部署" : expired ? "访问入口观测已过期" : value && !fresh ? "访问入口观测时效未确认" : "外部访问入口待确认"}
      description={draft ? "提交 Kubernetes 部署后核对受管 Service 发布信息。" : value && !fresh ? "当前未展示旧地址，请刷新后核对发布状态。" : "尚未确认可用于集群外访问的受管 Service；不使用 Agent 地址与内部端口拼接入口。"} />}
    {fresh && value?.warnings.map((warning, index) => <InfoBox key={index}>{warning}</InfoBox>)}
    {fresh && value?.observed_at && <p className="panel-note">核对时间：{when(value.observed_at)}</p>}
    <p className="panel-note">自动模式最长间隔 10 秒核对；手动模式按需刷新。</p>
    <p className="panel-note">平台推理调试经执行 Agent 访问实际 Coordinator Pod；此处地址仅用于展示和复制，不替换原生配置或内部请求路径。</p>
  </section>;
}
