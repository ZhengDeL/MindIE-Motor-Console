import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { Cpu } from "./icons";
import { when } from "./api";
import type { NodeInfo } from "./api";
import {
  aggregateKubernetesNpu,
  isKubernetesAgent,
  summarizeDockerNpu,
} from "./npuResources";
import type { AgentNpuInventory, KubernetesNpuSummary } from "./npuResources";
import {
  nodeObservation,
  observationAge,
  telemetryObservation,
  useObservationClock,
} from "./resourceObservation";
import {
  dockerDeclaration,
  dockerNpuModels,
  hasDeviceMetrics,
  resourceNotice,
  resourceTypeName,
} from "./resourcePresentation";
import "./npu-resources.css";
import { serverNow } from "./serverClock";

function DefaultDockerNpuValue({
  nodes,
  demo = false,
  now,
}: {
  nodes: readonly NodeInfo[];
  demo?: boolean;
  now?: number;
}) {
  const clock = useObservationClock(now === undefined);
  const docker = summarizeDockerNpu(nodes, { now: now ?? clock, demo });
  if (docker.total === null)
    return (
      <UiElement as="span" className="resource-state-note">
        {docker.agentCount ? "设备清单待采集" : "接入 Docker 节点后展示"}
      </UiElement>
    );
  return (
    <UiElement as="span" className="docker-npu-value">
      <UiElement as="strong">{docker.total}</UiElement>
      <UiElement as="small"><UiText>{"已识别设备"}</UiText></UiElement>
      {docker.partial && (
        <UiElement as="span" className="npu-collection-state">
          <UiText>{"覆盖"}</UiText> {docker.knownAgentCount} <UiText>{"/"}</UiText> {docker.agentCount} <UiText>{"个 Docker Agent"}</UiText>
        </UiElement>
      )}
      {docker.duplicateAgentCount > 0 && (
        <UiElement as="span" className="npu-collection-state"><UiText>{"已按宿主去重"}</UiText></UiElement>
      )}
    </UiElement>
  );
}

function DefaultKubernetesNpuContent({
  summary,
  demo = false,
  compact = false,
  now = serverNow(),
}: {
  summary: KubernetesNpuSummary;
  demo?: boolean;
  compact?: boolean;
  now?: number;
}) {
  if (demo)
    return (
      <UiElement as="span" className="resource-state-note"><UiText>{"演示模式，不连接真实集群"}</UiText></UiElement>
    );
  const groups = summary.groups.filter(
    (group) => group.capacity !== null || group.allocatable !== null,
  );
  const label =
    summary.status === "none"
      ? "接入 Kubernetes 集群后展示"
      : summary.status === "loading"
        ? "正在读取集群资源"
        : summary.status === "stale"
          ? "集群采样待刷新"
          : summary.status === "error"
            ? "集群资源读取失败"
            : "当前节点清单未包含 NPU 资源字段";
  if (compact)
    return (
      <UiElement as="div" className="kubernetes-npu-content npu-compact-content">
        {groups.length ? groups.map((group) => (
          <UiElement as="div" className="npu-compact-group" key={group.key}>
            <UiElement as="span" className="npu-compact-name" title={group.key}>{resourceTypeName(group.key)}</UiElement>
            <UiElement as="span" className="npu-compact-quantity">
              {group.allocatable !== null && <UiElement as="span"><UiElement as="small"><UiText>{"调度上限"}</UiText></UiElement><UiElement as="b">{group.allocatable}</UiElement></UiElement>}
              {group.capacity !== null && <UiElement as="span"><UiElement as="small"><UiText>{"容量"}</UiText></UiElement><UiElement as="b">{group.capacity}</UiElement></UiElement>}
              <UiElement as="small"><UiText>{"份额"}</UiText></UiElement>
            </UiElement>
            {group.conflictCount > 0 && <UiElement as="span" className="warning-text">{group.conflictCount} <UiText>{"项上报需核对"}</UiText></UiElement>}
          </UiElement>
        )) : <UiElement as="span" className={`resource-state-note ${["error", "stale"].includes(summary.status) ? "warning-text" : ""}`} role="status">{label}</UiElement>}
        {!!groups.length && <UiElement as="span" className="npu-compact-meta">
          {summary.nodeCount} <UiText>{"个节点 · 按 UID 去重"}</UiText>
          {summary.collectedAt && <UiElement as="time" dateTime={summary.collectedAt} title={when(summary.collectedAt)}>{observationAge(summary.collectedAt, now)}</UiElement>}
        </UiElement>}
        {summary.status === "partial" && <UiElement as="span" className="warning-text" title={summary.issues.join("\n")}>
          {summary.readyAgentCount < summary.agentCount ? `覆盖 ${summary.readyAgentCount} / ${summary.agentCount} 个集群 Agent` : "部分资源字段已采集"}
        </UiElement>}
      </UiElement>
    );
  return (
    <UiElement as="div" className="kubernetes-npu-content">
      {groups.length ? (
        <UiElement as="div" className="npu-resource-groups">
          {groups.map((group) => (
            <UiElement as="div" className="npu-resource-group" key={group.key}>
              <UiElement as="div" className="npu-resource-name">
                <UiElement as="span" title={group.key}>{resourceTypeName(group.key)}</UiElement>
                <UiElement as="small">{group.nodeCount} <UiText>{"个节点"}</UiText></UiElement>
              </UiElement>
              <UiElement as="div" className="npu-resource-quantity">
                {group.allocatable !== null && (
                  <UiElement as="span">
                    <UiElement as="small"><UiText>{"调度上限"}</UiText></UiElement>
                    <UiElement as="strong">{group.allocatable}</UiElement>
                  </UiElement>
                )}
                {group.capacity !== null && (
                  <UiElement as="span">
                    <UiElement as="small"><UiText>{"节点容量"}</UiText></UiElement>
                    <UiElement as="strong">{group.capacity}</UiElement>
                  </UiElement>
                )}
                <UiElement as="small"><UiText>{"份额"}</UiText></UiElement>
              </UiElement>
              {group.conflictCount > 0 && (
                <UiElement as="span" className="npu-collection-state warning-text">
                  {group.conflictCount} <UiText>{"项上报需核对"}</UiText>
                </UiElement>
              )}
            </UiElement>
          ))}
        </UiElement>
      ) : (
        <UiElement as="span"
          className={`resource-state-note ${["error", "stale"].includes(summary.status) ? "warning-text" : ""}`}
          role="status"
        >
          {label}
        </UiElement>
      )}
      {!!groups.length && (
        <UiElement as="div" className="npu-collection-meta">
          <UiElement as="span">{summary.nodeCount} <UiText>{"个已采集节点 · 按 UID 去重"}</UiText></UiElement>
          {summary.collectedAt && (
            <UiElement as="time" dateTime={summary.collectedAt}>
              <UiText>{"采于"}</UiText> {when(summary.collectedAt)}
            </UiElement>
          )}
          {summary.status === "partial" && (
            <UiElement as="span" className="warning-text">
              {summary.readyAgentCount < summary.agentCount
                ? `覆盖 ${summary.readyAgentCount} / ${summary.agentCount} 个集群 Agent`
                : "部分资源字段已采集"}
            </UiElement>
          )}
        </UiElement>
      )}
      {!!summary.issues.length && (
        <UiElement as="details" className="npu-collection-issues">
          <UiElement as="summary"><UiText>{"采集诊断"}</UiText></UiElement>
          <UiElement as="ul">
            {summary.issues.map((issue) => (
              <UiElement as="li" key={issue}>{issue}</UiElement>
            ))}
          </UiElement>
        </UiElement>
      )}
    </UiElement>
  );
}

function DefaultNpuResourceCard({
  nodes,
  summary,
  demo = false,
}: {
  nodes: readonly NodeInfo[];
  summary: KubernetesNpuSummary;
  demo?: boolean;
}) {
  return (
    <UiRegion name="layout.panel" as="section" className="stat-card npu-resource-card" aria-label="NPU 资源">
      <UiElement as="div" className="stat-label">
        <UiElement as="span"><UiText>{"NPU 资源"}</UiText></UiElement>
        <UiElement as="div" className="stat-card-icon color-2">
          <Cpu size={19} />
        </UiElement>
      </UiElement>
      <UiElement as="div" className="npu-runtime-summary npu-docker-summary">
        <UiElement as="span" className="npu-runtime-label"><UiText>{"Docker 设备清单"}</UiText></UiElement>
        <DockerNpuValue nodes={nodes} demo={demo} />
      </UiElement>
      <UiElement as="div" className="npu-runtime-summary">
        <UiElement as="span" className="npu-runtime-label"><UiText>{"Kubernetes 资源份额"}</UiText></UiElement>
        <KubernetesNpuContent summary={summary} demo={demo} />
      </UiElement>
      <UiElement as="p" className="npu-scope-note"><UiText>{"设备清单、调度上限与实时空闲量分别判断。"}</UiText></UiElement>
    </UiRegion>
  );
}

function DefaultAgentNpuResources({
  node,
  source,
  demo,
  now = serverNow(),
}: {
  node: NodeInfo;
  source?: AgentNpuInventory;
  demo: boolean;
  now?: number;
}) {
  const declaration = dockerDeclaration(node, now, demo);
  const models = dockerNpuModels(node, now);
  const hasKubernetes = isKubernetesAgent(node);
  const observation = nodeObservation(node, now, demo);
  const hardware = telemetryObservation(node, node.npu_telemetry, now);
  const devices =
    hardware.state === "fresh"
      ? (node.npu_telemetry?.devices.filter(hasDeviceMetrics) ?? [])
      : [];
  const notice = resourceNotice(node, now, demo);
  return (
    <UiElement as="div" className="agent-npu-resources">
      {(declaration || models.length > 0) && (
        <UiElement as="div" className="node-resource-tile node-device-tile">
          <UiElement as="span" className="npu-runtime-label">
            {declaration ? "NPU 声明 / 总量" : "NPU 设备型号"}
          </UiElement>
          {models.length > 0 && (
            <UiElement as="ul" className="agent-npu-models" aria-label="NPU 型号">
              {models.map((model) => (
                <UiElement as="li" key={model.name}>
                  <UiElement as="span">{model.name}</UiElement>
                  {models.length > 1 && (
                    <UiElement as="small"><UiText>{"已识别"}</UiText> {model.count} <UiText>{"个设备"}</UiText></UiElement>
                  )}
                </UiElement>
              ))}
            </UiElement>
          )}
          {declaration && (
            <UiElement as="div" className="agent-docker-quantity">
              <UiElement as="strong">{declaration.used}</UiElement>
              <UiElement as="span"><UiText>{"/"}</UiText></UiElement>
              <UiElement as="strong">{declaration.total}</UiElement>
              <UiElement as="span"><UiText>{"设备"}</UiText></UiElement>
            </UiElement>
          )}
          {declaration && observation.collectedAt && (
            <UiElement as="time"
              className="npu-collection-state"
              dateTime={observation.collectedAt}
            >
              <UiText>{"采于"}</UiText> {observationAge(observation.collectedAt, now)}
            </UiElement>
          )}
        </UiElement>
      )}
      {hasKubernetes && (
        <UiElement as="div" className="node-resource-tile node-kubernetes-tile">
          <UiElement as="span" className="npu-runtime-label"><UiText>{"Kubernetes 资源份额"}</UiText></UiElement>
          <KubernetesNpuContent
            demo={demo}
            compact
            now={now}
            summary={aggregateKubernetesNpu(
              [
                source ?? {
                  agentId: node.id,
                  agentName: node.name,
                  status: ["offline", "error"].includes(node.status)
                    ? "offline"
                    : "loading",
                },
              ],
              now,
            )}
          />
        </UiElement>
      )}
      {!!devices.length && (
        <UiElement as="div" className="node-resource-tile node-hardware-tile">
          <UiElement as="span" className="npu-runtime-label"><UiText>{"硬件采样"}</UiText></UiElement>
          <UiElement as="span" className="agent-hardware-summary"><UiElement as="span" className="resource-ready-dot" /><UiElement as="b">{devices.length}</UiElement> <UiText>{"个设备"}</UiText></UiElement>
          {node.npu_telemetry?.collected_at && (
            <UiElement as="time">{observationAge(node.npu_telemetry.collected_at, now)}</UiElement>
          )}
        </UiElement>
      )}
      {!declaration && !hasKubernetes && !devices.length && !models.length && (
        <UiElement as="div" className={`node-resource-notice ${notice.tone}`} role="status">
          <UiElement as="b">{notice.title}</UiElement>
          <UiElement as="span">{notice.detail}</UiElement>
        </UiElement>
      )}
    </UiElement>
  );
}

export const DockerNpuValue = withUiSlot("component.DockerNpuValue", DefaultDockerNpuValue);
export const KubernetesNpuContent = withUiSlot("component.KubernetesNpuContent", DefaultKubernetesNpuContent);
export const NpuResourceCard = withUiSlot("component.NpuResourceCard", DefaultNpuResourceCard);
export const AgentNpuResources = withUiSlot("component.AgentNpuResources", DefaultAgentNpuResources);

// Reference package implementation exports.
export { DefaultDockerNpuValue, DefaultKubernetesNpuContent, DefaultNpuResourceCard, DefaultAgentNpuResources };
