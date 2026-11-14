import { useBranding } from "./brandingState";
import { useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Box,
  Clock3,
  Cpu,
  Layers3,
  Plus,
  RefreshCw,
  Server,
} from "./icons";
import { statusName, when } from "./api";
import type { NodeInfo, Overview, Service } from "./api";
import type { ConsolePage } from "./consoleNavigation";
import type { NpuResourcesState } from "./useNpuResources";
import { isKubernetesAgent, summarizeDockerNpu } from "./npuResources";
import { DockerNpuValue, KubernetesNpuContent } from "./NpuSummary";
import { knownText, resourceNotice } from "./resourcePresentation";
import {
  nodeObservation,
  telemetryObservation,
  useObservationClock,
} from "./resourceObservation";
import { Badge, Button, Empty } from "./ui";
import TrafficTrend from "./TrafficTrend";
import { readNotice, type ReadState } from "./readState";
import "./overview.css";
import { usePluginEnabled } from "./consolePlugins";

type Props = {
  overview: Overview | null;
  services: Service[];
  nodes: NodeInfo[];
  servicesRead: ReadState;
  nodesRead: ReadState;
  npuResources: NpuResourcesState;
  demo: boolean;
  canOperate: boolean;
  canAdminister: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onNavigate: (page: ConsolePage) => void;
  onCreateService: () => void;
  onOpenService: (service: Service) => void;
};
function ReadNotice({ state, inline = false }: { state: ReadState; inline?: boolean }) {
  const message = readNotice(state);
  if (!message) return null;
  return inline
    ? <span className="small-caption" role="status">{state.loaded ? " · " : ""}{message}</span>
    : <p className="overview-empty-note" role="status">{message}</p>;
}
type Attention = {
  id: string;
  title: string;
  detail: string;
  tone: "danger" | "warning";
  action: () => void;
};
const pending = new Set([
  "deploying",
  "starting",
  "stopping",
  "restarting",
  "scaling",
  "removing",
]);
const failed = new Set(["error", "failed", "partial"]);
const connectedAgent = (node: NodeInfo) =>
  ["online", "ready", "healthy"].includes(node.status);
const activityNames: Record<string, string> = {
  "auth.login": "账号登录",
  "auth.logout": "退出登录",
  "auth.oidc_login": "单点登录",
  "auth.login_limited": "登录受限",
  "auth.bootstrap": "初始化管理员",
  "auth.user_create": "创建账号",
  "auth.user_update": "更新账号",
  "auth.user_delete": "删除账号",
  "auth.password_reset": "重置密码",
  "auth.password_change": "修改密码",
  "auth.oidc_provision": "创建单点登录账号",
};

export default function OverviewPage({
  overview,
  services,
  nodes,
  servicesRead,
  nodesRead,
  npuResources,
  demo,
  canOperate,
  canAdminister,
  refreshing,
  onRefresh,
  onNavigate,
  onCreateService,
  onOpenService,
}: Props) {
  const templatesEnabled = usePluginEnabled("templates");
  const auditEnabled = usePluginEnabled("audit");
  const branding = useBranding();
  const [showGuide, setShowGuide] = useState(false);
  const now = useObservationClock();
  const running = services.filter(
    (service) => service.status === "running",
  ).length;
  const processing = services.filter((service) =>
    pending.has(service.status),
  ).length;
  const errors = services.filter((service) => failed.has(service.status));
  const docker = summarizeDockerNpu(nodes, { now, demo });
  const k8sAgents = nodes.filter(isKubernetesAgent).length;
  const connected = nodes.filter(connectedAgent).length;
  const series = (overview?.series ?? []).filter(
    (point) =>
      Number.isFinite(point.requests) &&
      point.requests >= 0 &&
      Number.isFinite(point.latency) &&
      point.latency >= 0,
  );
  const latestSeen = nodes
    .map((node) => node.last_seen)
    .filter((time) => Number.isFinite(Date.parse(time)))
    .sort((a, b) => Date.parse(b) - Date.parse(a))[0];
  const attention: Attention[] = [
    ...errors.map((service) => ({
      id: `service-${service.id}`,
      title: service.name,
      detail: `${statusName(service.status)} · 查看部署与操作记录`,
      tone: "danger" as const,
      action: () => onOpenService(service),
    })),
    ...nodes.flatMap((node) => {
      const observation = nodeObservation(node, now, demo);
      if (!connectedAgent(node) || observation.state !== "fresh") {
        const notice = resourceNotice(node, now, demo);
        return [
          {
            id: `node-${node.id}`,
            title: node.name,
            detail: notice.title,
            tone:
              notice.tone === "danger"
                ? ("danger" as const)
                : ("warning" as const),
            action: () => onNavigate("nodes"),
          },
        ];
      }
      if (
        !demo &&
        node.docker_available !== false &&
        telemetryObservation(node, node.npu_telemetry, now).state !== "fresh"
      )
        return [
          {
            id: `hardware-${node.id}`,
            title: node.name,
            detail: "硬件采样待接入或刷新",
            tone: "warning" as const,
            action: () => onNavigate("nodes"),
          },
        ];
      return [];
    }),
  ];
  const attentionLoaded = servicesRead.loaded && nodesRead.loaded;
  const attentionRead = {
    loaded: attentionLoaded,
    refreshing: servicesRead.refreshing || nodesRead.refreshing,
    error: servicesRead.error || nodesRead.error,
  };
  const shownServices = [...services]
    .sort(
      (a, b) =>
        (Date.parse(b.updated_at) || Date.parse(b.created_at) || 0) -
        (Date.parse(a.updated_at) || Date.parse(a.created_at) || 0),
    );
  const activityDetail = (item: Overview["activities"][number]) => {
    const [target, ...detail] = item.detail.split(" · ");
    const entity =
      services.find((service) => service.id === target)?.name ??
      nodes.find((node) => node.id === target)?.name;
    if (entity) return [entity, ...detail].filter(Boolean).join(" · ");
    if (activityNames[item.title] || /^[a-z]+_[a-f0-9]{16,}/.test(target))
      return "查看完整操作审计";
    return item.detail;
  };
  // Altered MindIE-Motor Console presentation adapted from Portainer 2.45.3 DashboardItem/Widget.
  // See NOTICE.md and /third-party-licenses.txt for the original zlib notice.
  return (
    <div className="overview-page">
      <header className="overview-heading">
        <h1>集群概览</h1>
        <div className="overview-heading-actions">
          <button type="button" className="icon-btn" onClick={onRefresh} disabled={refreshing} aria-label="刷新集群概览">
            <RefreshCw size={18} className={refreshing ? "spin" : ""} />
          </button>
        </div>
      </header>
      {demo && <p className="overview-demo-note">当前为演示环境，数据为模拟数据。</p>}
      <section className="overview-widget" aria-labelledby="overview-info-title">
        <header className="overview-widget-header">
          <h2 id="overview-info-title"><Server size={16} />平台概况</h2>
          <button className="text-link" onClick={() => setShowGuide(value => !value)} aria-expanded={showGuide}>
            <BookOpen size={14} />{showGuide ? "收起指引" : "接入指引"}
          </button>
        </header>
        <dl className="overview-details">
          <div><dt>推理服务</dt><dd>{servicesRead.loaded && <>{services.length} 项已登记<span className="overview-detail-separator">·</span>{running} 项运行中{processing > 0 && <><span className="overview-detail-separator">·</span>{processing} 项处理中</>}</>}<ReadNotice state={servicesRead} inline /></dd></div>
          <div><dt>执行环境</dt><dd>{nodesRead.loaded && <>{nodes.length} 个 Agent<span className="overview-detail-separator">·</span>{connected} 个连接正常{latestSeen && <><span className="overview-detail-separator">·</span><time dateTime={latestSeen}>最近探测 {when(latestSeen)}</time></>}</>}<ReadNotice state={nodesRead} inline /></dd></div>
        </dl>
        {showGuide && <div className="overview-guide" aria-label={`开始使用${branding.name}`}>
          <button onClick={() => onNavigate("nodes")}><Server size={16} /><span><b>接入执行节点</b><small>登记 Agent 并检查设备采样</small></span><ArrowRight size={14} /></button>
          {templatesEnabled && <button onClick={() => onNavigate("templates")}><Layers3 size={16} /><span><b>选择配置模板</b><small>使用官方模板或自己的原生配置</small></span><ArrowRight size={14} /></button>}
          <button onClick={onCreateService} disabled={!canOperate}><Plus size={16} /><span><b>创建推理服务</b><small>核对镜像、模型路径与布局，再执行预检</small></span><ArrowRight size={14} /></button>
        </div>}
      </section>
      <div className="overview-metrics" aria-label="登记与连接状态">
        <button onClick={() => onNavigate("services")} className="overview-metric">
          <span className="overview-metric-icon"><Layers3 size={24} /></span><span>{servicesRead.loaded && <strong>{services.length}</strong>}<span className="overview-metric-label">推理服务</span></span>
          <span className="overview-metric-note">{readNotice(servicesRead) || <>{running} 项运行中{errors.length > 0 && <Badge status="error">{errors.length} 项需关注</Badge>}</>}</span>
        </button>
        <button onClick={() => onNavigate("nodes")} className="overview-metric">
          <span className="overview-metric-icon"><Server size={24} /></span><span>{nodesRead.loaded && <strong>{nodes.length}</strong>}<span className="overview-metric-label">执行 Agent</span></span>
          <span className="overview-metric-note">{readNotice(nodesRead) || `${connected} 个连接正常`}</span>
        </button>
        {nodesRead.loaded && docker.total !== null && <button onClick={() => onNavigate("nodes")} className="overview-metric">
          <span className="overview-metric-icon"><Cpu size={24} /></span><span><strong>{docker.total}</strong><span className="overview-metric-label">Docker 设备</span></span>
          <span className="overview-metric-note">{readNotice(nodesRead) || `已采集 ${docker.knownAgentCount} / ${docker.agentCount} 个 Agent`}</span>
        </button>}
        <button onClick={() => attention.length ? attention[0].action() : onNavigate("nodes")} className="overview-metric">
          <span className="overview-metric-icon"><AlertCircle size={24} /></span><span>{attentionLoaded && <strong>{attention.length}</strong>}<span className="overview-metric-label">待关注事项</span></span>
          <span className="overview-metric-note">{readNotice(attentionRead) || (attention.length ? "查看服务与资源观测状态" : "已登记状态中暂无待处理项")}</span>
        </button>
      </div>
      <section className="overview-widget" aria-labelledby="overview-services-title">
        <header className="overview-widget-header"><h2 id="overview-services-title"><Layers3 size={16} />推理服务</h2><div className="overview-widget-actions"><Button onClick={() => onNavigate("services")}>全部服务<ArrowUpRight size={14} /></Button>{canOperate && <Button kind="primary" onClick={onCreateService}><Plus size={14} />创建推理服务</Button>}</div></header>
        <ReadNotice state={servicesRead} />
        {shownServices.length ? <div className="table-wrap"><table className="overview-services" aria-label="最近更新的推理服务"><thead><tr><th>名称</th><th>状态</th><th>模型</th><th>运行环境</th><th>更新时间</th></tr></thead><tbody>{shownServices.slice(0, 4).map(service => <tr key={service.id}>
          <td><button className="text-link" onClick={() => onOpenService(service)}>{service.name}</button></td>
          <td><Badge status={service.status}>{statusName(service.status)}</Badge></td>
          <td>{knownText(service.model) && service.model}</td>
          <td>{["docker", "kubernetes"].includes(service.runtime) && <span className="overview-runtime">{service.runtime === "kubernetes" ? <Layers3 size={13} /> : <Box size={13} />}{service.runtime === "kubernetes" ? "Kubernetes" : "Docker"}</span>}</td>
          <td>{when(service.updated_at || service.created_at)}</td>
        </tr>)}</tbody></table></div> : servicesRead.loaded ? <Empty title="创建第一项推理服务" description="保存原生配置，核对资源后执行部署预检。" icon={<Layers3 size={25} />} action={canOperate ? <Button onClick={onCreateService}><Plus size={14} />创建服务配置</Button> : undefined} /> : <p className="overview-empty-note">{servicesRead.error ? "尚未取得服务列表，不能确认已登记服务数量。" : "正在读取服务列表，其他资源可继续查看。"}</p>}
      </section>
      <section className="overview-widget" aria-labelledby="overview-resources-title">
        <header className="overview-widget-header"><h2 id="overview-resources-title"><Cpu size={16} />资源观测</h2><Button onClick={() => onNavigate("nodes")}>节点资源<ArrowUpRight size={14} /></Button></header>
        <ReadNotice state={nodesRead} />
        <div className="overview-resource-body">
          {nodesRead.loaded && !!docker.agentCount && <section className="overview-runtime-block"><header><Box size={16} /><b>Docker</b><span>{docker.agentCount} 个 Agent</span></header><DockerNpuValue nodes={nodes} now={now} demo={demo} />{docker.used !== null && <p>容器声明使用 {docker.used} 个设备</p>}</section>}
          {nodesRead.loaded && !!k8sAgents && <section className="overview-runtime-block"><header><Layers3 size={16} /><b>Kubernetes</b><span>{k8sAgents} 个 Agent</span></header><KubernetesNpuContent summary={npuResources.summary} demo={demo} /><p>调度上限是资源额度，不是当前空闲量。</p></section>}
          {nodesRead.loaded && !docker.agentCount && !k8sAgents && <p>接入执行 Agent 后显示已经采集的设备与资源份额。</p>}
        </div>
      </section>
      <div className="overview-operations">
        <section className="overview-widget" aria-labelledby="overview-attention-title">
          <header className="overview-widget-header"><h2 id="overview-attention-title"><AlertCircle size={16} />运行关注</h2></header>
          <ReadNotice state={attentionRead} />
          {attention.length ? <div className="overview-records">{attention.slice(0, 4).map(item => <button key={item.id} onClick={item.action}><AlertCircle size={14} className={item.tone} /><span><b>{item.title}</b><small>{item.detail}</small></span><ArrowUpRight size={14} /></button>)}</div> : attentionLoaded ? <p className="overview-empty-note">{nodes.length ? "已登记状态中暂无待处理事项；实际可用性以服务检查结果为准。" : "接入节点后集中查看连接和采集事项。"}</p> : <p className="overview-empty-note">等待服务与节点读取结果后汇总。</p>}
        </section>
        {canAdminister && auditEnabled && <section className="overview-widget" aria-labelledby="overview-activity-title">
          <header className="overview-widget-header"><h2 id="overview-activity-title"><Clock3 size={16} />最近操作</h2><button className="text-link" onClick={() => onNavigate("audit")}>操作审计<ArrowUpRight size={13} /></button></header>
          {overview?.activities?.length ? <div className="overview-records">{overview.activities.slice(0, 3).map(item => <button key={item.id} onClick={() => onNavigate("audit")}><Clock3 size={14} /><span><b>{activityNames[item.title] ?? item.title}</b>{knownText(activityDetail(item)) && <small>{activityDetail(item)}</small>}</span><time dateTime={item.time}>{when(item.time)}</time></button>)}</div> : <p className="overview-empty-note">还没有操作记录</p>}
        </section>}
      </div>
      {!!series.length && <section className="overview-widget" aria-labelledby="overview-traffic-title"><header className="overview-widget-header"><h2 id="overview-traffic-title"><Activity size={16} />流量采样</h2><span>{demo ? "演示指标" : "请求速率与时延"}</span></header><div className="overview-traffic-body"><div className="chart-legend"><span><i className="chart-dot orange" />完成请求速率 <small>req/s</small></span><span><i className="chart-dot blue" />请求时延 <small>ms</small></span></div><TrafficTrend series={series} /></div></section>}
    </div>
  );
}
