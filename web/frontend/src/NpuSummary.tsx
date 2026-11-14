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

export function DockerNpuValue({
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
      <span className="resource-state-note">
        {docker.agentCount ? "设备清单待采集" : "接入 Docker 节点后展示"}
      </span>
    );
  return (
    <span className="docker-npu-value">
      <strong>{docker.total}</strong>
      <small>已识别设备</small>
      {docker.partial && (
        <span className="npu-collection-state">
          覆盖 {docker.knownAgentCount} / {docker.agentCount} 个 Docker Agent
        </span>
      )}
      {docker.duplicateAgentCount > 0 && (
        <span className="npu-collection-state">已按宿主去重</span>
      )}
    </span>
  );
}

export function KubernetesNpuContent({
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
      <span className="resource-state-note">演示模式，不连接真实集群</span>
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
      <div className="kubernetes-npu-content npu-compact-content">
        {groups.length ? groups.map((group) => (
          <div className="npu-compact-group" key={group.key}>
            <span className="npu-compact-name" title={group.key}>{resourceTypeName(group.key)}</span>
            <span className="npu-compact-quantity">
              {group.allocatable !== null && <span><small>调度上限</small><b>{group.allocatable}</b></span>}
              {group.capacity !== null && <span><small>容量</small><b>{group.capacity}</b></span>}
              <small>份额</small>
            </span>
            {group.conflictCount > 0 && <span className="warning-text">{group.conflictCount} 项上报需核对</span>}
          </div>
        )) : <span className={`resource-state-note ${["error", "stale"].includes(summary.status) ? "warning-text" : ""}`} role="status">{label}</span>}
        {!!groups.length && <span className="npu-compact-meta">
          {summary.nodeCount} 个节点 · 按 UID 去重
          {summary.collectedAt && <time dateTime={summary.collectedAt} title={when(summary.collectedAt)}>{observationAge(summary.collectedAt, now)}</time>}
        </span>}
        {summary.status === "partial" && <span className="warning-text" title={summary.issues.join("\n")}>
          {summary.readyAgentCount < summary.agentCount ? `覆盖 ${summary.readyAgentCount} / ${summary.agentCount} 个集群 Agent` : "部分资源字段已采集"}
        </span>}
      </div>
    );
  return (
    <div className="kubernetes-npu-content">
      {groups.length ? (
        <div className="npu-resource-groups">
          {groups.map((group) => (
            <div className="npu-resource-group" key={group.key}>
              <div className="npu-resource-name">
                <span title={group.key}>{resourceTypeName(group.key)}</span>
                <small>{group.nodeCount} 个节点</small>
              </div>
              <div className="npu-resource-quantity">
                {group.allocatable !== null && (
                  <span>
                    <small>调度上限</small>
                    <strong>{group.allocatable}</strong>
                  </span>
                )}
                {group.capacity !== null && (
                  <span>
                    <small>节点容量</small>
                    <strong>{group.capacity}</strong>
                  </span>
                )}
                <small>份额</small>
              </div>
              {group.conflictCount > 0 && (
                <span className="npu-collection-state warning-text">
                  {group.conflictCount} 项上报需核对
                </span>
              )}
            </div>
          ))}
        </div>
      ) : (
        <span
          className={`resource-state-note ${["error", "stale"].includes(summary.status) ? "warning-text" : ""}`}
          role="status"
        >
          {label}
        </span>
      )}
      {!!groups.length && (
        <div className="npu-collection-meta">
          <span>{summary.nodeCount} 个已采集节点 · 按 UID 去重</span>
          {summary.collectedAt && (
            <time dateTime={summary.collectedAt}>
              采于 {when(summary.collectedAt)}
            </time>
          )}
          {summary.status === "partial" && (
            <span className="warning-text">
              {summary.readyAgentCount < summary.agentCount
                ? `覆盖 ${summary.readyAgentCount} / ${summary.agentCount} 个集群 Agent`
                : "部分资源字段已采集"}
            </span>
          )}
        </div>
      )}
      {!!summary.issues.length && (
        <details className="npu-collection-issues">
          <summary>采集诊断</summary>
          <ul>
            {summary.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function NpuResourceCard({
  nodes,
  summary,
  demo = false,
}: {
  nodes: readonly NodeInfo[];
  summary: KubernetesNpuSummary;
  demo?: boolean;
}) {
  return (
    <section className="stat-card npu-resource-card" aria-label="NPU 资源">
      <div className="stat-label">
        <span>NPU 资源</span>
        <div className="stat-card-icon color-2">
          <Cpu size={19} />
        </div>
      </div>
      <div className="npu-runtime-summary npu-docker-summary">
        <span className="npu-runtime-label">Docker 设备清单</span>
        <DockerNpuValue nodes={nodes} demo={demo} />
      </div>
      <div className="npu-runtime-summary">
        <span className="npu-runtime-label">Kubernetes 资源份额</span>
        <KubernetesNpuContent summary={summary} demo={demo} />
      </div>
      <p className="npu-scope-note">设备清单、调度上限与实时空闲量分别判断。</p>
    </section>
  );
}

export function AgentNpuResources({
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
    <div className="agent-npu-resources">
      {(declaration || models.length > 0) && (
        <div className="node-resource-tile node-device-tile">
          <span className="npu-runtime-label">
            {declaration ? "NPU 声明 / 总量" : "NPU 设备型号"}
          </span>
          {models.length > 0 && (
            <ul className="agent-npu-models" aria-label="NPU 型号">
              {models.map((model) => (
                <li key={model.name}>
                  <span>{model.name}</span>
                  {models.length > 1 && (
                    <small>已识别 {model.count} 个设备</small>
                  )}
                </li>
              ))}
            </ul>
          )}
          {declaration && (
            <div className="agent-docker-quantity">
              <strong>{declaration.used}</strong>
              <span>/</span>
              <strong>{declaration.total}</strong>
              <span>设备</span>
            </div>
          )}
          {declaration && observation.collectedAt && (
            <time
              className="npu-collection-state"
              dateTime={observation.collectedAt}
            >
              采于 {observationAge(observation.collectedAt, now)}
            </time>
          )}
        </div>
      )}
      {hasKubernetes && (
        <div className="node-resource-tile node-kubernetes-tile">
          <span className="npu-runtime-label">Kubernetes 资源份额</span>
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
        </div>
      )}
      {!!devices.length && (
        <div className="node-resource-tile node-hardware-tile">
          <span className="npu-runtime-label">硬件采样</span>
          <span className="agent-hardware-summary"><span className="resource-ready-dot" /><b>{devices.length}</b> 个设备</span>
          {node.npu_telemetry?.collected_at && (
            <time>{observationAge(node.npu_telemetry.collected_at, now)}</time>
          )}
        </div>
      )}
      {!declaration && !hasKubernetes && !devices.length && !models.length && (
        <div className={`node-resource-notice ${notice.tone}`} role="status">
          <b>{notice.title}</b>
          <span>{notice.detail}</span>
        </div>
      )}
    </div>
  );
}
