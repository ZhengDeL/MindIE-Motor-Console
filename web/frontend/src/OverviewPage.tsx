import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
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
function DefaultReadNotice({ state, inline = false }: { state: ReadState; inline?: boolean }) {
  const message = readNotice(state);
  if (!message) return null;
  return inline
    ? <UiElement as="span" className="small-caption" role="status">{state.loaded ? " · " : ""}{message}</UiElement>
    : <UiElement as="p" className="overview-empty-note" role="status">{message}</UiElement>;
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

function DefaultOverviewPage({
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
    <UiElement as="div" className="overview-page">
      <UiElement as="header" className="overview-heading">
        <UiElement as="h1"><UiText>{"集群概览"}</UiText></UiElement>
        <UiElement as="div" className="overview-heading-actions">
          <UiButton actionId="action.OverviewPage.001" type="button" className="icon-btn" onClick={onRefresh} disabled={refreshing} aria-label="刷新集群概览">
            <RefreshCw size={18} className={refreshing ? "spin" : ""} />
          </UiButton>
        </UiElement>
      </UiElement>
      {demo && <UiElement as="p" className="overview-demo-note"><UiText>{"当前为演示环境，数据为模拟数据。"}</UiText></UiElement>}
      <UiRegion name="layout.panel" as="section" className="overview-widget" aria-labelledby="overview-info-title">
        <UiElement as="header" className="overview-widget-header">
          <UiElement as="h2" id="overview-info-title"><Server size={16} /><UiText>{"平台概况"}</UiText></UiElement>
          <UiButton actionId="action.OverviewPage.002" className="text-link" onClick={() => setShowGuide(value => !value)} aria-expanded={showGuide}>
            <BookOpen size={14} />{showGuide ? "收起指引" : "接入指引"}
          </UiButton>
        </UiElement>
        <UiElement as="dl" className="overview-details">
          <UiElement as="div"><UiElement as="dt"><UiText>{"推理服务"}</UiText></UiElement><UiElement as="dd">{servicesRead.loaded && <>{services.length} <UiText>{"项已登记"}</UiText><UiElement as="span" className="overview-detail-separator"><UiText>{"·"}</UiText></UiElement>{running} <UiText>{"项运行中"}</UiText>{processing > 0 && <><UiElement as="span" className="overview-detail-separator"><UiText>{"·"}</UiText></UiElement>{processing} <UiText>{"项处理中"}</UiText></>}</>}<ReadNotice state={servicesRead} inline /></UiElement></UiElement>
          <UiElement as="div"><UiElement as="dt"><UiText>{"执行环境"}</UiText></UiElement><UiElement as="dd">{nodesRead.loaded && <>{nodes.length} <UiText>{"个 Agent"}</UiText><UiElement as="span" className="overview-detail-separator"><UiText>{"·"}</UiText></UiElement>{connected} <UiText>{"个连接正常"}</UiText>{latestSeen && <><UiElement as="span" className="overview-detail-separator"><UiText>{"·"}</UiText></UiElement><UiElement as="time" dateTime={latestSeen}><UiText>{"最近探测"}</UiText> {when(latestSeen)}</UiElement></>}</>}<ReadNotice state={nodesRead} inline /></UiElement></UiElement>
        </UiElement>
        {showGuide && <UiElement as="div" className="overview-guide" aria-label={`开始使用${branding.name}`}>
          <UiButton actionId="action.OverviewPage.003" onClick={() => onNavigate("nodes")}><Server size={16} /><UiElement as="span"><UiElement as="b"><UiText>{"接入执行节点"}</UiText></UiElement><UiElement as="small"><UiText>{"登记 Agent 并检查设备采样"}</UiText></UiElement></UiElement><ArrowRight size={14} /></UiButton>
          {templatesEnabled && <UiButton actionId="action.OverviewPage.004" onClick={() => onNavigate("templates")}><Layers3 size={16} /><UiElement as="span"><UiElement as="b"><UiText>{"选择配置模板"}</UiText></UiElement><UiElement as="small"><UiText>{"使用官方模板或自己的原生配置"}</UiText></UiElement></UiElement><ArrowRight size={14} /></UiButton>}
          <UiButton actionId="action.OverviewPage.005" onClick={onCreateService} disabled={!canOperate}><Plus size={16} /><UiElement as="span"><UiElement as="b"><UiText>{"创建推理服务"}</UiText></UiElement><UiElement as="small"><UiText>{"核对镜像、模型路径与布局，再执行预检"}</UiText></UiElement></UiElement><ArrowRight size={14} /></UiButton>
        </UiElement>}
      </UiRegion>
      <UiElement as="div" className="overview-metrics" aria-label="登记与连接状态">
        <UiButton actionId="action.OverviewPage.006" onClick={() => onNavigate("services")} className="overview-metric">
          <UiElement as="span" className="overview-metric-icon"><Layers3 size={24} /></UiElement><UiElement as="span">{servicesRead.loaded && <UiElement as="strong">{services.length}</UiElement>}<UiElement as="span" className="overview-metric-label"><UiText>{"推理服务"}</UiText></UiElement></UiElement>
          <UiElement as="span" className="overview-metric-note">{readNotice(servicesRead) || <>{running} <UiText>{"项运行中"}</UiText>{errors.length > 0 && <Badge status="error">{errors.length} <UiText>{"项需关注"}</UiText></Badge>}</>}</UiElement>
        </UiButton>
        <UiButton actionId="action.OverviewPage.007" onClick={() => onNavigate("nodes")} className="overview-metric">
          <UiElement as="span" className="overview-metric-icon"><Server size={24} /></UiElement><UiElement as="span">{nodesRead.loaded && <UiElement as="strong">{nodes.length}</UiElement>}<UiElement as="span" className="overview-metric-label"><UiText>{"执行 Agent"}</UiText></UiElement></UiElement>
          <UiElement as="span" className="overview-metric-note">{readNotice(nodesRead) || `${connected} 个连接正常`}</UiElement>
        </UiButton>
        {nodesRead.loaded && docker.total !== null && <UiButton actionId="action.OverviewPage.008" onClick={() => onNavigate("nodes")} className="overview-metric">
          <UiElement as="span" className="overview-metric-icon"><Cpu size={24} /></UiElement><UiElement as="span"><UiElement as="strong">{docker.total}</UiElement><UiElement as="span" className="overview-metric-label"><UiText>{"Docker 设备"}</UiText></UiElement></UiElement>
          <UiElement as="span" className="overview-metric-note">{readNotice(nodesRead) || `已采集 ${docker.knownAgentCount} / ${docker.agentCount} 个 Agent`}</UiElement>
        </UiButton>}
        <UiButton actionId="action.OverviewPage.009" onClick={() => attention.length ? attention[0].action() : onNavigate("nodes")} className="overview-metric">
          <UiElement as="span" className="overview-metric-icon"><AlertCircle size={24} /></UiElement><UiElement as="span">{attentionLoaded && <UiElement as="strong">{attention.length}</UiElement>}<UiElement as="span" className="overview-metric-label"><UiText>{"待关注事项"}</UiText></UiElement></UiElement>
          <UiElement as="span" className="overview-metric-note">{readNotice(attentionRead) || (attention.length ? "查看服务与资源观测状态" : "已登记状态中暂无待处理项")}</UiElement>
        </UiButton>
      </UiElement>
      <UiRegion name="layout.panel" as="section" className="overview-widget" aria-labelledby="overview-services-title">
        <UiElement as="header" className="overview-widget-header"><UiElement as="h2" id="overview-services-title"><Layers3 size={16} /><UiText>{"推理服务"}</UiText></UiElement><UiElement as="div" className="overview-widget-actions"><Button actionId="action.OverviewPage.010" onClick={() => onNavigate("services")}><UiText>{"全部服务"}</UiText><ArrowUpRight size={14} /></Button>{canOperate && <Button actionId="action.OverviewPage.011" kind="primary" onClick={onCreateService}><Plus size={14} /><UiText>{"创建推理服务"}</UiText></Button>}</UiElement></UiElement>
        <ReadNotice state={servicesRead} />
        {shownServices.length ? <UiElement as="div" className="table-wrap"><UiElement as="table" className="overview-services" aria-label="最近更新的推理服务"><UiElement as="thead"><UiElement as="tr"><UiElement as="th"><UiText>{"名称"}</UiText></UiElement><UiElement as="th"><UiText>{"状态"}</UiText></UiElement><UiElement as="th"><UiText>{"模型"}</UiText></UiElement><UiElement as="th"><UiText>{"运行环境"}</UiText></UiElement><UiElement as="th"><UiText>{"更新时间"}</UiText></UiElement></UiElement></UiElement><UiElement as="tbody">{shownServices.slice(0, 4).map(service => <UiElement as="tr" key={service.id}>
          <UiElement as="td"><UiButton actionId="action.OverviewPage.012" className="text-link" onClick={() => onOpenService(service)}>{service.name}</UiButton></UiElement>
          <UiElement as="td"><Badge status={service.status}>{statusName(service.status)}</Badge></UiElement>
          <UiElement as="td">{knownText(service.model) && service.model}</UiElement>
          <UiElement as="td">{["docker", "kubernetes"].includes(service.runtime) && <UiElement as="span" className="overview-runtime">{service.runtime === "kubernetes" ? <Layers3 size={13} /> : <Box size={13} />}{service.runtime === "kubernetes" ? "Kubernetes" : "Docker"}</UiElement>}</UiElement>
          <UiElement as="td">{when(service.updated_at || service.created_at)}</UiElement>
        </UiElement>)}</UiElement></UiElement></UiElement> : servicesRead.loaded ? <Empty title="创建第一项推理服务" description="保存原生配置，核对资源后执行部署预检。" icon={<Layers3 size={25} />} action={canOperate ? <Button actionId="action.OverviewPage.013" onClick={onCreateService}><Plus size={14} /><UiText>{"创建服务配置"}</UiText></Button> : undefined} /> : <UiElement as="p" className="overview-empty-note">{servicesRead.error ? "尚未取得服务列表，不能确认已登记服务数量。" : "正在读取服务列表，其他资源可继续查看。"}</UiElement>}
      </UiRegion>
      <UiRegion name="layout.panel" as="section" className="overview-widget" aria-labelledby="overview-resources-title">
        <UiElement as="header" className="overview-widget-header"><UiElement as="h2" id="overview-resources-title"><Cpu size={16} /><UiText>{"资源观测"}</UiText></UiElement><Button actionId="action.OverviewPage.014" onClick={() => onNavigate("nodes")}><UiText>{"节点资源"}</UiText><ArrowUpRight size={14} /></Button></UiElement>
        <ReadNotice state={nodesRead} />
        <UiElement as="div" className="overview-resource-body">
          {nodesRead.loaded && !!docker.agentCount && <UiRegion name="layout.panel" as="section" className="overview-runtime-block"><UiElement as="header"><Box size={16} /><UiElement as="b"><UiText>{"Docker"}</UiText></UiElement><UiElement as="span">{docker.agentCount} <UiText>{"个 Agent"}</UiText></UiElement></UiElement><DockerNpuValue nodes={nodes} now={now} demo={demo} />{docker.used !== null && <UiElement as="p"><UiText>{"容器声明使用"}</UiText> {docker.used} <UiText>{"个设备"}</UiText></UiElement>}</UiRegion>}
          {nodesRead.loaded && !!k8sAgents && <UiRegion name="layout.panel" as="section" className="overview-runtime-block"><UiElement as="header"><Layers3 size={16} /><UiElement as="b"><UiText>{"Kubernetes"}</UiText></UiElement><UiElement as="span">{k8sAgents} <UiText>{"个 Agent"}</UiText></UiElement></UiElement><KubernetesNpuContent summary={npuResources.summary} demo={demo} /><UiElement as="p"><UiText>{"调度上限是资源额度，不是当前空闲量。"}</UiText></UiElement></UiRegion>}
          {nodesRead.loaded && !docker.agentCount && !k8sAgents && <UiElement as="p"><UiText>{"接入执行 Agent 后显示已经采集的设备与资源份额。"}</UiText></UiElement>}
        </UiElement>
      </UiRegion>
      <UiElement as="div" className="overview-operations">
        <UiRegion name="layout.panel" as="section" className="overview-widget" aria-labelledby="overview-attention-title">
          <UiElement as="header" className="overview-widget-header"><UiElement as="h2" id="overview-attention-title"><AlertCircle size={16} /><UiText>{"运行关注"}</UiText></UiElement></UiElement>
          <ReadNotice state={attentionRead} />
          {attention.length ? <UiElement as="div" className="overview-records">{attention.slice(0, 4).map(item => <UiButton actionId="action.OverviewPage.015" key={item.id} onClick={item.action}><AlertCircle size={14} className={item.tone} /><UiElement as="span"><UiElement as="b">{item.title}</UiElement><UiElement as="small">{item.detail}</UiElement></UiElement><ArrowUpRight size={14} /></UiButton>)}</UiElement> : attentionLoaded ? <UiElement as="p" className="overview-empty-note">{nodes.length ? "已登记状态中暂无待处理事项；实际可用性以服务检查结果为准。" : "接入节点后集中查看连接和采集事项。"}</UiElement> : <UiElement as="p" className="overview-empty-note"><UiText>{"等待服务与节点读取结果后汇总。"}</UiText></UiElement>}
        </UiRegion>
        {canAdminister && auditEnabled && <UiRegion name="layout.panel" as="section" className="overview-widget" aria-labelledby="overview-activity-title">
          <UiElement as="header" className="overview-widget-header"><UiElement as="h2" id="overview-activity-title"><Clock3 size={16} /><UiText>{"最近操作"}</UiText></UiElement><UiButton actionId="action.OverviewPage.016" className="text-link" onClick={() => onNavigate("audit")}><UiText>{"操作审计"}</UiText><ArrowUpRight size={13} /></UiButton></UiElement>
          {overview?.activities?.length ? <UiElement as="div" className="overview-records">{overview.activities.slice(0, 3).map(item => <UiButton actionId="action.OverviewPage.017" key={item.id} onClick={() => onNavigate("audit")}><Clock3 size={14} /><UiElement as="span"><UiElement as="b">{activityNames[item.title] ?? item.title}</UiElement>{knownText(activityDetail(item)) && <UiElement as="small">{activityDetail(item)}</UiElement>}</UiElement><UiElement as="time" dateTime={item.time}>{when(item.time)}</UiElement></UiButton>)}</UiElement> : <UiElement as="p" className="overview-empty-note"><UiText>{"还没有操作记录"}</UiText></UiElement>}
        </UiRegion>}
      </UiElement>
      {!!series.length && <UiRegion name="layout.panel" as="section" className="overview-widget" aria-labelledby="overview-traffic-title"><UiElement as="header" className="overview-widget-header"><UiElement as="h2" id="overview-traffic-title"><Activity size={16} /><UiText>{"流量采样"}</UiText></UiElement><UiElement as="span">{demo ? "演示指标" : "请求速率与时延"}</UiElement></UiElement><UiElement as="div" className="overview-traffic-body"><UiElement as="div" className="chart-legend"><UiElement as="span"><UiElement as="i" className="chart-dot orange" /><UiText>{"完成请求速率"}</UiText> <UiElement as="small"><UiText>{"req/s"}</UiText></UiElement></UiElement><UiElement as="span"><UiElement as="i" className="chart-dot blue" /><UiText>{"请求时延"}</UiText> <UiElement as="small"><UiText>{"ms"}</UiText></UiElement></UiElement></UiElement><TrafficTrend series={series} /></UiElement></UiRegion>}
    </UiElement>
  );
}

const ReadNotice = withUiSlot("component.ReadNotice", DefaultReadNotice);
const OverviewPage = withUiSlot("component.OverviewPage", DefaultOverviewPage);
export default OverviewPage;

// Reference package implementation exports.
export { DefaultReadNotice, DefaultOverviewPage };
