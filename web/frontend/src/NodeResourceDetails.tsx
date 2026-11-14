import { when } from "./api";
import type { NodeInfo, NpuDeviceTelemetry, ResourceLedger } from "./api";
import { Activity, CheckCircle2, Clock3, Cpu, RefreshCw, Server } from "./icons";
import { Button, InfoBox } from "./ui";
import { serverNow } from "./serverClock";
import {
  aggregateKubernetesNpu,
  isKubernetesAgent,
  isValidResourcePercent,
} from "./npuResources";
import type { AgentNpuInventory } from "./npuResources";
import { KubernetesNpuContent } from "./NpuSummary";
import {
  confirmedPhysicalNpuIndex,
  isResourceInteger,
  isResourceNumber,
  nodeObservation,
  observationAge,
  observationIsFresh,
  resourceBytes,
  resourcePercent,
  telemetryObservation,
} from "./resourceObservation";
import {
  dockerDeclaration,
  hasDeviceMetrics,
  knownText,
  resourceNotice,
} from "./resourcePresentation";

const allocationLabels = {
  pending: "预留中",
  active: "已分配",
  uncertain: "待核对",
  released: "已释放",
};
const sourceNames: Record<string, string> = {
  "host-npu-smi": "宿主硬件采集",
  "prometheus-exporter": "NPU Exporter",
  exporter: "NPU Exporter",
};
const decimal = (value: number, unit: string) =>
  `${value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })} ${unit}`;

function DeviceObservations({
  devices,
  historical = false,
}: {
  devices: NpuDeviceTelemetry[];
  historical?: boolean;
}) {
  const measured = devices.filter(hasDeviceMetrics);
  return (
    <div
      className={`npu-device-observations ${historical ? "is-historical" : ""}`}
    >
      <div
        className="npu-device-grid"
        role="region"
        aria-label={historical ? "上次 NPU 硬件指标" : "当前 NPU 硬件指标"}
      >
        {measured.map((device, deviceIndex) => {
          const physical = confirmedPhysicalNpuIndex(device);
          const utilization = isValidResourcePercent(
            device.utilization_percent,
          );
          const memoryUsed = isResourceNumber(device.memory_used_bytes);
          const memoryTotal = isResourceNumber(device.memory_total_bytes);
          return (
            <article
              className="npu-device-card"
              key={device.device_id}
              aria-label={`NPU 设备 ${device.device_id}`}
              style={{ animationDelay: `${Math.min(deviceIndex, 7) * 24}ms` }}
            >
              <header>
                <span className="device-card-symbol">
                  <Cpu size={18} />
                </span>
                <div>
                  <h4>
                    {knownText(device.model_name) ? device.model_name : "NPU"}{" "}
                    <span>· 设备 {device.device_id}</span>
                  </h4>
                  {physical !== null && <code>/dev/davinci{physical}</code>}
                </div>
                {["healthy", "unhealthy"].includes(device.health) && (
                  <span
                    className={`npu-health ${historical ? "historical" : device.health}`}
                  >
                    {historical ? "历史 · " : ""}
                    {device.health === "healthy" ? "健康" : "异常"}
                  </span>
                )}
              </header>
              <div className="device-card-metrics">
                {utilization && (
                  <section className="device-utilization">
                    <span>计算利用率</span>
                    <strong>
                      {resourcePercent(device.utilization_percent)}
                    </strong>
                    <i aria-hidden="true">
                      <b
                        style={{
                          transform: `scaleX(${(device.utilization_percent ?? 0) / 100})`,
                        }}
                      />
                    </i>
                  </section>
                )}
                {(memoryUsed || memoryTotal) && (
                  <section className="device-memory">
                    <span>HBM 内存</span>
                    <div>
                      {memoryUsed && (
                        <strong>
                          {resourceBytes(device.memory_used_bytes)}
                          <small>已用</small>
                        </strong>
                      )}
                      {memoryTotal && (
                        <span>
                          {resourceBytes(device.memory_total_bytes)} 总量
                        </span>
                      )}
                    </div>
                    {isValidResourcePercent(device.memory_usage_percent) && (
                      <i aria-hidden="true">
                        <b
                          style={{
                            transform: `scaleX(${(device.memory_usage_percent ?? 0) / 100})`,
                          }}
                        />
                      </i>
                    )}
                  </section>
                )}
              </div>
              {(isResourceNumber(device.temperature_celsius) ||
                isResourceNumber(device.power_watts)) && (
                <div className="device-sensor-readings">
                  {isResourceNumber(device.temperature_celsius) && (
                    <span>
                      温度 <b>{decimal(device.temperature_celsius, "℃")}</b>
                    </span>
                  )}
                  {isResourceNumber(device.power_watts) && (
                    <span>
                      功耗 <b>{decimal(device.power_watts, "W")}</b>
                    </span>
                  )}
                </div>
              )}
              <details className="device-identifiers">
                <summary>设备标识</summary>
                <dl>
                  {physical !== null && (
                    <>
                      <dt>物理设备</dt>
                      <dd>
                        <code>/dev/davinci{physical}</code>
                      </dd>
                    </>
                  )}
                  {isResourceInteger(device.logical_device_id) && (
                    <>
                      <dt>逻辑 ID</dt>
                      <dd>{device.logical_device_id}</dd>
                    </>
                  )}
                  {isResourceInteger(device.npu_id) && (
                    <>
                      <dt>NPU ID</dt>
                      <dd>{device.npu_id}</dd>
                    </>
                  )}
                  {isResourceInteger(device.chip_id) && (
                    <>
                      <dt>Chip ID</dt>
                      <dd>{device.chip_id}</dd>
                    </>
                  )}
                  {knownText(device.pci_bus_id) && (
                    <>
                      <dt>PCI 地址</dt>
                      <dd>
                        <code>{device.pci_bus_id}</code>
                      </dd>
                    </>
                  )}
                  {knownText(device.uuid) && (
                    <>
                      <dt>UUID</dt>
                      <dd>
                        <code>{device.uuid}</code>
                      </dd>
                    </>
                  )}
                </dl>
              </details>
              {device.processes_status === "ready" && (
                <details className="device-processes">
                  <summary>{device.processes.length} 个观测进程</summary>
                  {device.process_pid_scope === "host" && <p>宿主机 PID</p>}
                  {device.processes.length ? (
                    <ul>
                      {device.processes.map((process) => (
                        <li key={`${device.device_id}-${process.pid}`}>
                          <code>PID {process.pid}</code>
                          {isResourceNumber(process.memory_used_bytes) && (
                            <span>
                              {resourceBytes(process.memory_used_bytes)}
                            </span>
                          )}
                          {knownText(process.container_id) && (
                            <code title={process.container_id}>
                              {process.container_id}
                            </code>
                          )}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>
                      本次完整查询未记录到进程。进程列表不用于单独判断可分配性。
                    </p>
                  )}
                </details>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}

function AllocationRecords({
  ledger,
  historical = false,
}: {
  ledger: ResourceLedger;
  historical?: boolean;
}) {
  return ledger.items.length ? (
    <div
      className="allocation-records"
      role="region"
      aria-label={historical ? "历史受管分配记录" : "受管资源分配清单"}
    >
      {ledger.items.map((allocation) => (
        <article key={allocation.allocation_id} className="allocation-record">
          <header>
            <code>{allocation.service_id}</code>
            <span
              className={`npu-allocation-state ${historical ? "historical" : allocation.state}`}
            >
              {allocationLabels[allocation.state] || "状态需核对"}
              {historical ? " · 历史" : ""}
            </span>
          </header>
          <div className="allocation-resource-tags">
            {allocation.devices.map((device) => (
              <span key={`device-${device}`}>davinci{device}</span>
            ))}
            {allocation.ports.map((port) => (
              <span key={`port-${port}`}>端口 {port}</span>
            ))}
          </div>
          {!!allocation.container_names.length && (
            <div className="allocation-container-names">
              {allocation.container_names.map((name) => (
                <code key={name}>{name}</code>
              ))}
            </div>
          )}
          <details>
            <summary>操作记录</summary>
            <code>{allocation.operation_id}</code>
            {allocation.updated_at && (
              <time>{when(allocation.updated_at)}</time>
            )}
          </details>
        </article>
      ))}
    </div>
  ) : (
    <p className="resource-empty-state">
      该次采样未记录受管分配。部署时仍须核验当前资源与其他程序的占用。
    </p>
  );
}

export default function NodeResourceDetails({
  node,
  now = serverNow(),
  demo = false,
  clusterSource,
  onRefresh,
}: {
  node: NodeInfo;
  now?: number;
  demo?: boolean;
  clusterSource?: AgentNpuInventory;
  onRefresh?: () => void;
}) {
  const observation = nodeObservation(node, now, demo);
  const hardware = telemetryObservation(node, node.npu_telemetry, now);
  const telemetry = node.npu_telemetry;
  const declaration = dockerDeclaration(node, now, demo);
  const current = observation.state === "fresh";
  const currentHardware = hardware.state === "fresh" ? telemetry : null;
  const currentDevices =
    currentHardware?.devices.filter(hasDeviceMetrics) ?? [];
  const historicalHardware = !currentDevices.length
    ? telemetry?.last_good
    : undefined;
  const currentLedger =
    node.docker_available !== false &&
    current &&
    node.resource_ledger &&
    observationIsFresh(node.resource_ledger.collected_at, node.expires_at, now)
      ? node.resource_ledger
      : null;
  const historicalLedger = !currentLedger
    ? (node.resource_ledger ?? node.last_good_inventory?.resource_ledger)
    : undefined;
  const cpuAvailable = current && isValidResourcePercent(node.cpu_percent);
  const memoryAvailable =
    current && isValidResourcePercent(node.memory_percent);
  const notice = resourceNotice(node, now, demo);
  const hardwareRelevant =
    node.docker_available !== false ||
    currentDevices.length > 0 ||
    !!historicalHardware?.devices.filter(hasDeviceMetrics).length;
  const clusterSummary = isKubernetesAgent(node)
    ? aggregateKubernetesNpu(
        [
          clusterSource ?? {
            agentId: node.id,
            agentName: node.name,
            status: node.status === "offline" ? "offline" : "loading",
          },
        ],
        now,
      )
    : null;

  return (
    <div className="node-resource-details">
      <div className="node-resource-toolbar">
        <div className="node-resource-sample" role="status">
          <span className={`resource-sample-status is-${observation.state} ${current ? "is-current" : ""}`}>
            <span aria-hidden="true" />{observation.label}
          </span>
          {observation.collectedAt && (
            <time dateTime={observation.collectedAt} title={when(observation.collectedAt)}>
              更新于 {observationAge(observation.collectedAt, now)}
            </time>
          )}
        </div>
        {onRefresh && <Button onClick={onRefresh}><RefreshCw size={14} />刷新观测</Button>}
      </div>
      {demo && <InfoBox>演示数据，未连接真实设备。</InfoBox>}
      {node.identity_conflict && (
        <InfoBox>
          {node.identity_conflict.message ||
            "执行宿主重复接入，请核对 Agent 身份。"}
        </InfoBox>
      )}
      {!current && (
        <div className={`resource-guidance ${notice.tone}`} role="status">
          <Clock3 size={20} />
          <div>
            <b>{notice.title}</b>
            <p>{notice.detail}</p>
          </div>
        </div>
      )}
      {(declaration || cpuAvailable || memoryAvailable) && (
        <div className="resource-observation-grid">
          {declaration && (
            <section>
              <Server size={19} />
              <span>Docker 设备声明</span>
              <strong>
                {declaration.used}
                <small> 已声明</small>
                <i />
                {declaration.total}
                <small> 总量</small>
              </strong>
              <span>声明用于保留设备分配关系，不等同实时利用率。</span>
            </section>
          )}
          {(cpuAvailable || memoryAvailable) && (
            <section>
              <Activity size={19} />
              <span>宿主采样</span>
              <strong>
                {cpuAvailable && <>CPU {resourcePercent(node.cpu_percent)}</>}
                {cpuAvailable && memoryAvailable && <i />}
                {memoryAvailable && (
                  <>内存 {resourcePercent(node.memory_percent)}</>
                )}
              </strong>
              {observation.collectedAt && (
                <span>采于 {when(observation.collectedAt)}</span>
              )}
            </section>
          )}
        </div>
      )}
      {clusterSummary && (
        <section className="resource-detail-section">
          <div className="resource-section-heading">
            <div>
              <span className="resource-eyebrow">KUBERNETES</span>
              <h3>集群调度份额</h3>
            </div>
          </div>
          <p className="npu-scope-note">
            来自当前集群 NodeList，按节点 UID 去重。调度上限是可供 Pod
            请求的额度，完整节点与调度条件可在“集群节点”查看。
          </p>
          <div className="cluster-resource-detail-summary">
            <KubernetesNpuContent summary={clusterSummary} demo={demo} />
          </div>
        </section>
      )}
      {hardwareRelevant && (
        <section className="resource-detail-section">
          <div className="resource-section-heading">
            <div>
              <h3>硬件指标</h3>
            </div>
            {!!currentDevices.length && (
              <span className="resource-verified">
                <CheckCircle2 size={13} />
                {currentDevices.length} 个设备已采集
              </span>
            )}
          </div>
          {!!currentDevices.length ? (
            <>
              <p className="npu-scope-note">
                展示已采集的有效字段。利用率与 HBM
                用量用于监控，部署仍执行独立资源准入。
              </p>
              <div className="npu-collection-meta">
                {knownText(currentHardware?.source) && (
                  <span>
                    {sourceNames[currentHardware.source] ??
                      currentHardware.source}
                  </span>
                )}
                {hardware.collectedAt && (
                  <time>
                    采于 {when(hardware.collectedAt)} ·{" "}
                    {observationAge(hardware.collectedAt, now)}
                  </time>
                )}
              </div>
              <DeviceObservations devices={currentDevices} />
            </>
          ) : (
            <div className="resource-guidance">
              <Cpu size={22} />
              <div>
                <b>
                  {hardware.state === "stale"
                    ? "硬件采样待刷新"
                    : hardware.state === "error"
                      ? "硬件采集失败"
                      : hardware.state === "offline"
                        ? "恢复连接后采集硬件指标"
                        : "接入硬件指标"}
                </b>
                <p>
                  {hardware.state === "stale"
                    ? "最近一次硬件采样已过期，请刷新观测并检查 Agent 采集状态。恢复有效采样前，不显示当前硬件读数。"
                    : hardware.state === "error"
                      ? "请查看硬件采集诊断并检查 Agent 采集源。恢复有效采样前，不以 Docker 设备声明代替硬件读数。"
                      : hardware.state === "offline"
                        ? "恢复 Agent 连接后重新读取硬件采样。历史读数不参与当前资源汇总与准入。"
                        : "在此宿主配置采集器或受信任的 NPU Exporter 后，可查看利用率、HBM、温度和进程。Docker 声明不会代替硬件读数。"}
                </p>
              </div>
            </div>
          )}
          {!!telemetry?.errors?.length && (
            <details className="npu-collection-issues">
              <summary>硬件采集诊断</summary>
              <ul>
                {telemetry.errors.map((error, index) => (
                  <li key={`${error.source}-${index}`}>
                    {error.message || error.code}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!!historicalHardware?.devices.filter(hasDeviceMetrics).length && (
            <details className="npu-last-good">
              <summary>
                历史硬件采样
                {historicalHardware.collected_at &&
                  ` · ${when(historicalHardware.collected_at)}`}
              </summary>
              <p className="npu-history-note">
                以下为历史记录，仅用于排障，不参与当前资源汇总与准入。
              </p>
              <DeviceObservations
                devices={historicalHardware.devices}
                historical
              />
            </details>
          )}
        </section>
      )}
      {node.docker_available !== false && (
        <section className="resource-detail-section">
          <div className="resource-section-heading">
            <div>
              <span className="resource-eyebrow">ALLOCATION</span>
              <h3>受管资源分配</h3>
            </div>
            {currentLedger && (
              <span>
                {currentLedger.complete
                  ? "当前 Agent 账本"
                  : "当前账本部分记录"}
              </span>
            )}
          </div>
          <p className="npu-scope-note">
            显示本 Agent
            管理的设备、端口与容器。标记“待核对”的资源保留预留，需核对操作回执。
          </p>
          {currentLedger ? (
            <>
              <div className="npu-collection-meta">
                <time>更新于 {when(currentLedger.collected_at)}</time>
              </div>
              <AllocationRecords ledger={currentLedger} />
            </>
          ) : (
            <div className="resource-guidance">
              <Server size={20} />
              <div>
                <b>分配账本待采集</b>
                <p>
                  连接有效的 Agent 后重新读取，不依据缺失记录判断资源已经释放。
                </p>
              </div>
            </div>
          )}
          {historicalLedger && (
            <details className="npu-last-good">
              <summary>历史分配记录</summary>
              <p className="npu-history-note">
                历史记录不用于确认当前资源已经释放。
              </p>
              <AllocationRecords ledger={historicalLedger} historical />
            </details>
          )}
        </section>
      )}
      {!!node.collection_errors?.length && (
        <details className="npu-collection-issues resource-diagnostics">
          <summary>节点采集诊断</summary>
          <ul>
            {node.collection_errors.map((error, index) => (
              <li key={`${error.source}-${error.code}-${index}`}>
                {error.source} · {error.message || error.code}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
