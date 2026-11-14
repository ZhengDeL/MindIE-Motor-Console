import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
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

function DefaultDeviceObservations({
  devices,
  historical = false,
}: {
  devices: NpuDeviceTelemetry[];
  historical?: boolean;
}) {
  const measured = devices.filter(hasDeviceMetrics);
  return (
    <UiElement as="div"
      className={`npu-device-observations ${historical ? "is-historical" : ""}`}
    >
      <UiElement as="div"
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
            <UiElement as="article"
              className="npu-device-card"
              key={device.device_id}
              aria-label={`NPU 设备 ${device.device_id}`}
              style={{ animationDelay: `${Math.min(deviceIndex, 7) * 24}ms` }}
            >
              <UiElement as="header">
                <UiElement as="span" className="device-card-symbol">
                  <Cpu size={18} />
                </UiElement>
                <UiElement as="div">
                  <UiElement as="h4">
                    {knownText(device.model_name) ? device.model_name : "NPU"}{" "}
                    <UiElement as="span"><UiText>{"· 设备"}</UiText> {device.device_id}</UiElement>
                  </UiElement>
                  {physical !== null && <UiElement as="code"><UiText>{"/dev/davinci"}</UiText>{physical}</UiElement>}
                </UiElement>
                {["healthy", "unhealthy"].includes(device.health) && (
                  <UiElement as="span"
                    className={`npu-health ${historical ? "historical" : device.health}`}
                  >
                    {historical ? "历史 · " : ""}
                    {device.health === "healthy" ? "健康" : "异常"}
                  </UiElement>
                )}
              </UiElement>
              <UiElement as="div" className="device-card-metrics">
                {utilization && (
                  <UiRegion name="layout.panel" as="section" className="device-utilization">
                    <UiElement as="span"><UiText>{"计算利用率"}</UiText></UiElement>
                    <UiElement as="strong">
                      {resourcePercent(device.utilization_percent)}
                    </UiElement>
                    <UiElement as="i" aria-hidden="true">
                      <UiElement as="b"
                        style={{
                          transform: `scaleX(${(device.utilization_percent ?? 0) / 100})`,
                        }}
                      />
                    </UiElement>
                  </UiRegion>
                )}
                {(memoryUsed || memoryTotal) && (
                  <UiRegion name="layout.panel" as="section" className="device-memory">
                    <UiElement as="span"><UiText>{"HBM 内存"}</UiText></UiElement>
                    <UiElement as="div">
                      {memoryUsed && (
                        <UiElement as="strong">
                          {resourceBytes(device.memory_used_bytes)}
                          <UiElement as="small"><UiText>{"已用"}</UiText></UiElement>
                        </UiElement>
                      )}
                      {memoryTotal && (
                        <UiElement as="span">
                          {resourceBytes(device.memory_total_bytes)} <UiText>{"总量"}</UiText>
                        </UiElement>
                      )}
                    </UiElement>
                    {isValidResourcePercent(device.memory_usage_percent) && (
                      <UiElement as="i" aria-hidden="true">
                        <UiElement as="b"
                          style={{
                            transform: `scaleX(${(device.memory_usage_percent ?? 0) / 100})`,
                          }}
                        />
                      </UiElement>
                    )}
                  </UiRegion>
                )}
              </UiElement>
              {(isResourceNumber(device.temperature_celsius) ||
                isResourceNumber(device.power_watts)) && (
                <UiElement as="div" className="device-sensor-readings">
                  {isResourceNumber(device.temperature_celsius) && (
                    <UiElement as="span">
                      <UiText>{"温度"}</UiText> <UiElement as="b">{decimal(device.temperature_celsius, "℃")}</UiElement>
                    </UiElement>
                  )}
                  {isResourceNumber(device.power_watts) && (
                    <UiElement as="span">
                      <UiText>{"功耗"}</UiText> <UiElement as="b">{decimal(device.power_watts, "W")}</UiElement>
                    </UiElement>
                  )}
                </UiElement>
              )}
              <UiElement as="details" className="device-identifiers">
                <UiElement as="summary"><UiText>{"设备标识"}</UiText></UiElement>
                <UiElement as="dl">
                  {physical !== null && (
                    <>
                      <UiElement as="dt"><UiText>{"物理设备"}</UiText></UiElement>
                      <UiElement as="dd">
                        <UiElement as="code"><UiText>{"/dev/davinci"}</UiText>{physical}</UiElement>
                      </UiElement>
                    </>
                  )}
                  {isResourceInteger(device.logical_device_id) && (
                    <>
                      <UiElement as="dt"><UiText>{"逻辑 ID"}</UiText></UiElement>
                      <UiElement as="dd">{device.logical_device_id}</UiElement>
                    </>
                  )}
                  {isResourceInteger(device.npu_id) && (
                    <>
                      <UiElement as="dt"><UiText>{"NPU ID"}</UiText></UiElement>
                      <UiElement as="dd">{device.npu_id}</UiElement>
                    </>
                  )}
                  {isResourceInteger(device.chip_id) && (
                    <>
                      <UiElement as="dt"><UiText>{"Chip ID"}</UiText></UiElement>
                      <UiElement as="dd">{device.chip_id}</UiElement>
                    </>
                  )}
                  {knownText(device.pci_bus_id) && (
                    <>
                      <UiElement as="dt"><UiText>{"PCI 地址"}</UiText></UiElement>
                      <UiElement as="dd">
                        <UiElement as="code">{device.pci_bus_id}</UiElement>
                      </UiElement>
                    </>
                  )}
                  {knownText(device.uuid) && (
                    <>
                      <UiElement as="dt"><UiText>{"UUID"}</UiText></UiElement>
                      <UiElement as="dd">
                        <UiElement as="code">{device.uuid}</UiElement>
                      </UiElement>
                    </>
                  )}
                </UiElement>
              </UiElement>
              {device.processes_status === "ready" && (
                <UiElement as="details" className="device-processes">
                  <UiElement as="summary">{device.processes.length} <UiText>{"个观测进程"}</UiText></UiElement>
                  {device.process_pid_scope === "host" && <UiElement as="p"><UiText>{"宿主机 PID"}</UiText></UiElement>}
                  {device.processes.length ? (
                    <UiElement as="ul">
                      {device.processes.map((process) => (
                        <UiElement as="li" key={`${device.device_id}-${process.pid}`}>
                          <UiElement as="code"><UiText>{"PID"}</UiText> {process.pid}</UiElement>
                          {isResourceNumber(process.memory_used_bytes) && (
                            <UiElement as="span">
                              {resourceBytes(process.memory_used_bytes)}
                            </UiElement>
                          )}
                          {knownText(process.container_id) && (
                            <UiElement as="code" title={process.container_id}>
                              {process.container_id}
                            </UiElement>
                          )}
                        </UiElement>
                      ))}
                    </UiElement>
                  ) : (
                    <UiElement as="p">
                      <UiText>{"本次完整查询未记录到进程。进程列表不用于单独判断可分配性。"}</UiText>
                    </UiElement>
                  )}
                </UiElement>
              )}
            </UiElement>
          );
        })}
      </UiElement>
    </UiElement>
  );
}

function DefaultAllocationRecords({
  ledger,
  historical = false,
}: {
  ledger: ResourceLedger;
  historical?: boolean;
}) {
  return ledger.items.length ? (
    <UiElement as="div"
      className="allocation-records"
      role="region"
      aria-label={historical ? "历史受管分配记录" : "受管资源分配清单"}
    >
      {ledger.items.map((allocation) => (
        <UiElement as="article" key={allocation.allocation_id} className="allocation-record">
          <UiElement as="header">
            <UiElement as="code">{allocation.service_id}</UiElement>
            <UiElement as="span"
              className={`npu-allocation-state ${historical ? "historical" : allocation.state}`}
            >
              {allocationLabels[allocation.state] || "状态需核对"}
              {historical ? " · 历史" : ""}
            </UiElement>
          </UiElement>
          <UiElement as="div" className="allocation-resource-tags">
            {allocation.devices.map((device) => (
              <UiElement as="span" key={`device-${device}`}><UiText>{"davinci"}</UiText>{device}</UiElement>
            ))}
            {allocation.ports.map((port) => (
              <UiElement as="span" key={`port-${port}`}><UiText>{"端口"}</UiText> {port}</UiElement>
            ))}
          </UiElement>
          {!!allocation.container_names.length && (
            <UiElement as="div" className="allocation-container-names">
              {allocation.container_names.map((name) => (
                <UiElement as="code" key={name}>{name}</UiElement>
              ))}
            </UiElement>
          )}
          <UiElement as="details">
            <UiElement as="summary"><UiText>{"操作记录"}</UiText></UiElement>
            <UiElement as="code">{allocation.operation_id}</UiElement>
            {allocation.updated_at && (
              <UiElement as="time">{when(allocation.updated_at)}</UiElement>
            )}
          </UiElement>
        </UiElement>
      ))}
    </UiElement>
  ) : (
    <UiElement as="p" className="resource-empty-state">
      <UiText>{"该次采样未记录受管分配。部署时仍须核验当前资源与其他程序的占用。"}</UiText>
    </UiElement>
  );
}

function DefaultNodeResourceDetails({
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
    <UiElement as="div" className="node-resource-details">
      <UiElement as="div" className="node-resource-toolbar">
        <UiElement as="div" className="node-resource-sample" role="status">
          <UiElement as="span" className={`resource-sample-status is-${observation.state} ${current ? "is-current" : ""}`}>
            <UiElement as="span" aria-hidden="true" />{observation.label}
          </UiElement>
          {observation.collectedAt && (
            <UiElement as="time" dateTime={observation.collectedAt} title={when(observation.collectedAt)}>
              <UiText>{"更新于"}</UiText> {observationAge(observation.collectedAt, now)}
            </UiElement>
          )}
        </UiElement>
        {onRefresh && <Button actionId="action.NodeResourceDetails.001" onClick={onRefresh}><RefreshCw size={14} /><UiText>{"刷新观测"}</UiText></Button>}
      </UiElement>
      {demo && <InfoBox><UiText>{"演示数据，未连接真实设备。"}</UiText></InfoBox>}
      {node.identity_conflict && (
        <InfoBox>
          {node.identity_conflict.message ||
            "执行宿主重复接入，请核对 Agent 身份。"}
        </InfoBox>
      )}
      {!current && (
        <UiElement as="div" className={`resource-guidance ${notice.tone}`} role="status">
          <Clock3 size={20} />
          <UiElement as="div">
            <UiElement as="b">{notice.title}</UiElement>
            <UiElement as="p">{notice.detail}</UiElement>
          </UiElement>
        </UiElement>
      )}
      {(declaration || cpuAvailable || memoryAvailable) && (
        <UiElement as="div" className="resource-observation-grid">
          {declaration && (
            <UiRegion name="layout.panel" as="section">
              <Server size={19} />
              <UiElement as="span"><UiText>{"Docker 设备声明"}</UiText></UiElement>
              <UiElement as="strong">
                {declaration.used}
                <UiElement as="small"> <UiText>{"已声明"}</UiText></UiElement>
                <UiElement as="i" />
                {declaration.total}
                <UiElement as="small"> <UiText>{"总量"}</UiText></UiElement>
              </UiElement>
              <UiElement as="span"><UiText>{"声明用于保留设备分配关系，不等同实时利用率。"}</UiText></UiElement>
            </UiRegion>
          )}
          {(cpuAvailable || memoryAvailable) && (
            <UiRegion name="layout.panel" as="section">
              <Activity size={19} />
              <UiElement as="span"><UiText>{"宿主采样"}</UiText></UiElement>
              <UiElement as="strong">
                {cpuAvailable && <><UiText>{"CPU"}</UiText> {resourcePercent(node.cpu_percent)}</>}
                {cpuAvailable && memoryAvailable && <UiElement as="i" />}
                {memoryAvailable && (
                  <><UiText>{"内存"}</UiText> {resourcePercent(node.memory_percent)}</>
                )}
              </UiElement>
              {observation.collectedAt && (
                <UiElement as="span"><UiText>{"采于"}</UiText> {when(observation.collectedAt)}</UiElement>
              )}
            </UiRegion>
          )}
        </UiElement>
      )}
      {clusterSummary && (
        <UiRegion name="layout.panel" as="section" className="resource-detail-section">
          <UiElement as="div" className="resource-section-heading">
            <UiElement as="div">
              <UiElement as="span" className="resource-eyebrow"><UiText>{"KUBERNETES"}</UiText></UiElement>
              <UiElement as="h3"><UiText>{"集群调度份额"}</UiText></UiElement>
            </UiElement>
          </UiElement>
          <UiElement as="p" className="npu-scope-note">
            <UiText>{"来自当前集群 NodeList，按节点 UID 去重。调度上限是可供 Pod 请求的额度，完整节点与调度条件可在“集群节点”查看。"}</UiText>
          </UiElement>
          <UiElement as="div" className="cluster-resource-detail-summary">
            <KubernetesNpuContent summary={clusterSummary} demo={demo} />
          </UiElement>
        </UiRegion>
      )}
      {hardwareRelevant && (
        <UiRegion name="layout.panel" as="section" className="resource-detail-section">
          <UiElement as="div" className="resource-section-heading">
            <UiElement as="div">
              <UiElement as="h3"><UiText>{"硬件指标"}</UiText></UiElement>
            </UiElement>
            {!!currentDevices.length && (
              <UiElement as="span" className="resource-verified">
                <CheckCircle2 size={13} />
                {currentDevices.length} <UiText>{"个设备已采集"}</UiText>
              </UiElement>
            )}
          </UiElement>
          {!!currentDevices.length ? (
            <>
              <UiElement as="p" className="npu-scope-note">
                <UiText>{"展示已采集的有效字段。利用率与 HBM 用量用于监控，部署仍执行独立资源准入。"}</UiText>
              </UiElement>
              <UiElement as="div" className="npu-collection-meta">
                {knownText(currentHardware?.source) && (
                  <UiElement as="span">
                    {sourceNames[currentHardware.source] ??
                      currentHardware.source}
                  </UiElement>
                )}
                {hardware.collectedAt && (
                  <UiElement as="time">
                    <UiText>{"采于"}</UiText> {when(hardware.collectedAt)} <UiText>{"·"}</UiText>{" "}
                    {observationAge(hardware.collectedAt, now)}
                  </UiElement>
                )}
              </UiElement>
              <DeviceObservations devices={currentDevices} />
            </>
          ) : (
            <UiElement as="div" className="resource-guidance">
              <Cpu size={22} />
              <UiElement as="div">
                <UiElement as="b">
                  {hardware.state === "stale"
                    ? "硬件采样待刷新"
                    : hardware.state === "error"
                      ? "硬件采集失败"
                      : hardware.state === "offline"
                        ? "恢复连接后采集硬件指标"
                        : "接入硬件指标"}
                </UiElement>
                <UiElement as="p">
                  {hardware.state === "stale"
                    ? "最近一次硬件采样已过期，请刷新观测并检查 Agent 采集状态。恢复有效采样前，不显示当前硬件读数。"
                    : hardware.state === "error"
                      ? "请查看硬件采集诊断并检查 Agent 采集源。恢复有效采样前，不以 Docker 设备声明代替硬件读数。"
                      : hardware.state === "offline"
                        ? "恢复 Agent 连接后重新读取硬件采样。历史读数不参与当前资源汇总与准入。"
                        : "在此宿主配置采集器或受信任的 NPU Exporter 后，可查看利用率、HBM、温度和进程。Docker 声明不会代替硬件读数。"}
                </UiElement>
              </UiElement>
            </UiElement>
          )}
          {!!telemetry?.errors?.length && (
            <UiElement as="details" className="npu-collection-issues">
              <UiElement as="summary"><UiText>{"硬件采集诊断"}</UiText></UiElement>
              <UiElement as="ul">
                {telemetry.errors.map((error, index) => (
                  <UiElement as="li" key={`${error.source}-${index}`}>
                    {error.message || error.code}
                  </UiElement>
                ))}
              </UiElement>
            </UiElement>
          )}
          {!!historicalHardware?.devices.filter(hasDeviceMetrics).length && (
            <UiElement as="details" className="npu-last-good">
              <UiElement as="summary">
                <UiText>{"历史硬件采样"}</UiText>
                {historicalHardware.collected_at &&
                  ` · ${when(historicalHardware.collected_at)}`}
              </UiElement>
              <UiElement as="p" className="npu-history-note">
                <UiText>{"以下为历史记录，仅用于排障，不参与当前资源汇总与准入。"}</UiText>
              </UiElement>
              <DeviceObservations
                devices={historicalHardware.devices}
                historical
              />
            </UiElement>
          )}
        </UiRegion>
      )}
      {node.docker_available !== false && (
        <UiRegion name="layout.panel" as="section" className="resource-detail-section">
          <UiElement as="div" className="resource-section-heading">
            <UiElement as="div">
              <UiElement as="span" className="resource-eyebrow"><UiText>{"ALLOCATION"}</UiText></UiElement>
              <UiElement as="h3"><UiText>{"受管资源分配"}</UiText></UiElement>
            </UiElement>
            {currentLedger && (
              <UiElement as="span">
                {currentLedger.complete
                  ? "当前 Agent 账本"
                  : "当前账本部分记录"}
              </UiElement>
            )}
          </UiElement>
          <UiElement as="p" className="npu-scope-note">
            <UiText>{"显示本 Agent 管理的设备、端口与容器。标记“待核对”的资源保留预留，需核对操作回执。"}</UiText>
          </UiElement>
          {currentLedger ? (
            <>
              <UiElement as="div" className="npu-collection-meta">
                <UiElement as="time"><UiText>{"更新于"}</UiText> {when(currentLedger.collected_at)}</UiElement>
              </UiElement>
              <AllocationRecords ledger={currentLedger} />
            </>
          ) : (
            <UiElement as="div" className="resource-guidance">
              <Server size={20} />
              <UiElement as="div">
                <UiElement as="b"><UiText>{"分配账本待采集"}</UiText></UiElement>
                <UiElement as="p">
                  <UiText>{"连接有效的 Agent 后重新读取，不依据缺失记录判断资源已经释放。"}</UiText>
                </UiElement>
              </UiElement>
            </UiElement>
          )}
          {historicalLedger && (
            <UiElement as="details" className="npu-last-good">
              <UiElement as="summary"><UiText>{"历史分配记录"}</UiText></UiElement>
              <UiElement as="p" className="npu-history-note">
                <UiText>{"历史记录不用于确认当前资源已经释放。"}</UiText>
              </UiElement>
              <AllocationRecords ledger={historicalLedger} historical />
            </UiElement>
          )}
        </UiRegion>
      )}
      {!!node.collection_errors?.length && (
        <UiElement as="details" className="npu-collection-issues resource-diagnostics">
          <UiElement as="summary"><UiText>{"节点采集诊断"}</UiText></UiElement>
          <UiElement as="ul">
            {node.collection_errors.map((error, index) => (
              <UiElement as="li" key={`${error.source}-${error.code}-${index}`}>
                {error.source} <UiText>{"·"}</UiText> {error.message || error.code}
              </UiElement>
            ))}
          </UiElement>
        </UiElement>
      )}
    </UiElement>
  );
}

const DeviceObservations = withUiSlot("component.DeviceObservations", DefaultDeviceObservations);
const AllocationRecords = withUiSlot("component.AllocationRecords", DefaultAllocationRecords);
const NodeResourceDetails = withUiSlot("component.NodeResourceDetails", DefaultNodeResourceDetails);
export default NodeResourceDetails;

// Reference package implementation exports.
export { DefaultDeviceObservations, DefaultAllocationRecords, DefaultNodeResourceDetails };
