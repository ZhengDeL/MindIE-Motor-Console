import type { NodeInfo, NpuDeviceTelemetry } from "./api";
import { isValidResourcePercent } from "./npuResources";
import {
  isResourceInteger,
  isResourceNumber,
  nodeObservation,
  telemetryObservation,
} from "./resourceObservation";
import { serverNow } from "./serverClock";

export function knownText(value: unknown): value is string {
  return (
    typeof value === "string" &&
    !!value.trim() &&
    ![
      "unknown",
      "未知",
      "unavailable",
      "n/a",
      "none",
      "null",
      "undefined",
      "—",
      "-",
    ].includes(value.trim().toLowerCase())
  );
}

/** Presentation only: never converts a missing observation into an available resource. */
export function dockerDeclaration(
  node: NodeInfo,
  now = serverNow(),
  demo = false,
) {
  if (
    node.docker_available === false ||
    nodeObservation(node, now, demo).state !== "fresh" ||
    !isResourceInteger(node.npu_used) ||
    !isResourceInteger(node.npu_total) ||
    node.npu_used > node.npu_total
  )
    return null;
  return { used: node.npu_used, total: node.npu_total };
}

/** Report observed model names independently of device utilization and declarations. */
export function dockerNpuModels(node: NodeInfo, now = serverNow()) {
  if (
    node.docker_available !== true ||
    telemetryObservation(node, node.npu_telemetry, now).state !== "fresh"
  )
    return [];
  const models = new Map<string, number>();
  for (const device of node.npu_telemetry?.devices ?? []) {
    if (!knownText(device.device_id) || !knownText(device.model_name)) continue;
    const name = device.model_name.trim();
    models.set(name, (models.get(name) ?? 0) + 1);
  }
  return Array.from(models, ([name, count]) => ({ name, count }));
}

export function resourceNotice(
  node: NodeInfo,
  now = serverNow(),
  demo = false,
) {
  const observation = nodeObservation(node, now, demo);
  if (node.identity_conflict)
    return {
      title: "接入身份冲突",
      detail: "核对重复接入记录后重新采集，当前资源未计入汇总。",
      tone: "warning",
    };
  if (observation.state === "offline")
    return {
      title: "Agent 连接中断",
      detail: "恢复 Agent 连接后，平台会重新采集资源。",
      tone: "danger",
    };
  if (observation.state === "legacy")
    return {
      title: "需要更新 Agent",
      detail: "更新 Agent 后获取带时间校验的资源采样。",
      tone: "warning",
    };
  if (observation.state === "stale")
    return {
      title: "采样已过期",
      detail: "请检查 Agent 采集状态与节点时间；历史数值未计入当前汇总。",
      tone: "warning",
    };
  if (observation.state === "error")
    return {
      title: "资源采集失败",
      detail: "查看采集诊断，修复连接或采集源后重试。",
      tone: "danger",
    };
  return {
    title: "等待有效采样",
    detail: "完成连接探测后，展示已校验的设备和宿主指标。",
    tone: "neutral",
  };
}

export function hasDeviceMetrics(device: NpuDeviceTelemetry) {
  return (
    knownText(device.device_id) &&
    (isValidResourcePercent(device.utilization_percent) ||
      isResourceNumber(device.memory_used_bytes) ||
      isResourceNumber(device.memory_total_bytes) ||
      ["healthy", "unhealthy"].includes(device.health) ||
      isResourceNumber(device.temperature_celsius) ||
      isResourceNumber(device.power_watts) ||
      device.processes_status === "ready")
  );
}

export function resourceTypeName(key: string) {
  return key === "huawei.com/Ascend910"
    ? "Ascend 910"
    : key === "huawei.com/npu"
      ? "Ascend NPU"
      : key;
}
