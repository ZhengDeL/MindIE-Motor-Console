import { useEffect, useState } from "react";
import type { NodeInfo, NpuDeviceTelemetry, NpuTelemetry } from "./api";
import { serverNow, subscribeServerClock } from "./serverClock";

export const MAX_OBSERVATION_AGE_MS = 5 * 60_000;
const FUTURE_CLOCK_SKEW_MS = 30_000;
export type ObservationState =
  "fresh" | "stale" | "legacy" | "offline" | "unknown" | "error";
export type Observation = {
  state: ObservationState;
  label: string;
  collectedAt: string | null;
  expiresAt: string | null;
};

export function observationTime(value: unknown): number | null {
  if (
    typeof value !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T.+(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  )
    return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function observationIsFresh(
  collectedAt: unknown,
  expiresAt: unknown,
  now = serverNow(),
): boolean {
  const collected = observationTime(collectedAt),
    expires = observationTime(expiresAt);
  return (
    collected !== null &&
    expires !== null &&
    collected <= now + FUTURE_CLOCK_SKEW_MS &&
    expires > collected &&
    expires > now &&
    now - collected <= MAX_OBSERVATION_AGE_MS
  );
}

export function nodeObservation(
  node: NodeInfo,
  now = serverNow(),
  demo = false,
): Observation {
  const collectedAt = node.collected_at || null,
    expiresAt = node.expires_at || null;
  const result = (state: ObservationState, label: string): Observation => ({
    state,
    label,
    collectedAt,
    expiresAt,
  });
  if (demo || (node.demo === true && node.resource_freshness === "demo"))
    return result("fresh", "演示数据");
  if (node.status === "offline") return result("offline", "Agent 离线");
  if (node.identity_conflict)
    return result("unknown", "执行节点身份重复，资源未计入");
  if (node.inventory_schema !== 2)
    return result("legacy", "旧版观测，时效未确认");
  if (node.inventory_status === "error") return result("error", "资源采集失败");
  if (node.resource_freshness === "stale") return result("stale", "观测已过期");
  if (!["online", "healthy", "ready"].includes(node.status))
    return result("unknown", "连接状态未确认");
  if (
    node.resource_freshness === "unknown" ||
    !["ready", "partial"].includes(node.inventory_status || "")
  )
    return result("unknown", "尚无有效观测");
  const observed = observationTime(collectedAt),
    expires = observationTime(expiresAt);
  if (observed === null || expires === null || expires <= observed)
    return result("unknown", "采集时间无效或未确认");
  if (observed > now + FUTURE_CLOCK_SKEW_MS)
    return result("unknown", "采集时间超前，请核对节点时钟");
  if (!observationIsFresh(collectedAt, expiresAt, now))
    return result(
      observationTime(collectedAt) === null ? "unknown" : "stale",
      collectedAt ? "观测已过期" : "采集时间未确认",
    );
  return result(
    "fresh",
    node.inventory_status === "partial" ? "部分指标已采集" : "观测有效",
  );
}

export function telemetryObservation(
  node: NodeInfo,
  telemetry: NpuTelemetry | null | undefined,
  now = serverNow(),
): Observation {
  const collectedAt = telemetry?.collected_at || null,
    expiresAt = telemetry?.expires_at || null;
  const result = (state: ObservationState, label: string): Observation => ({
    state,
    label,
    collectedAt,
    expiresAt,
  });
  if (node.status === "offline")
    return result("offline", "Agent 离线，指标不作为当前观测");
  if (node.identity_conflict)
    return result("unknown", "执行节点身份冲突，硬件归属未确认");
  if (!["online", "healthy", "ready"].includes(node.status))
    return result("unknown", "连接状态未确认，硬件指标未知");
  if (!telemetry || telemetry.status === "unavailable")
    return result("unknown", "硬件指标尚未接入");
  if (telemetry.status === "error") return result("error", "硬件指标采集失败");
  if (!["ready", "partial"].includes(telemetry.status))
    return result("unknown", "硬件指标状态未确认");
  const observed = observationTime(collectedAt),
    expires = observationTime(expiresAt);
  if (observed === null || expires === null || expires <= observed)
    return result("unknown", "硬件采集时间无效或未确认");
  if (observed > now + FUTURE_CLOCK_SKEW_MS)
    return result("unknown", "硬件采集时间超前，请核对节点时钟");
  if (!observationIsFresh(collectedAt, expiresAt, now))
    return result("stale", "硬件指标已过期");
  return result(
    "fresh",
    telemetry.status === "partial" ? "部分硬件指标可用" : "硬件指标有效",
  );
}

export function isResourceNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}
export function isResourceInteger(value: unknown): value is number {
  return isResourceNumber(value) && Number.isSafeInteger(value);
}
/** Chip Logic ID is not a /dev/davinci index. Conflicting physical aliases stay unknown. */
export function confirmedPhysicalNpuIndex(
  device: NpuDeviceTelemetry,
): number | null {
  if (!["configured", "verified"].includes(device.mapping_status || ""))
    return null;
  const indices = [device.device_index, device.physical_device_id].filter(
    (value) => value !== null && value !== undefined,
  );
  if (
    !indices.length ||
    !indices.every(isResourceInteger) ||
    new Set(indices).size !== 1
  )
    return null;
  return indices[0];
}
export function resourceBytes(value: unknown): string {
  if (!isResourceNumber(value)) return "—";
  if (value === 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  const exponent = Math.min(
    units.length - 1,
    Math.max(0, Math.floor(Math.log(value) / Math.log(1024))),
  );
  return `${(value / 1024 ** exponent).toLocaleString("zh-CN", { maximumFractionDigits: exponent ? 2 : 0 })} ${units[exponent]}`;
}
export function resourcePercent(value: unknown): string {
  return isResourceNumber(value) && value <= 100
    ? `${value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })}%`
    : "—";
}
export function observationAge(
  value: string | null | undefined,
  now = serverNow(),
): string {
  const timestamp = observationTime(value);
  if (timestamp === null) return "采集时间未知";
  const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
  return seconds < 60
    ? `${seconds} 秒前`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)} 分钟前`
      : `${Math.floor(seconds / 3600)} 小时前`;
}

/** Refresh age labels without opening one network request per browser or table row. */
export function useObservationClock(active = true): number {
  const [now, setNow] = useState(serverNow);
  useEffect(() => {
    if (!active) return;
    let timer: ReturnType<typeof setInterval> | undefined;
    const update = () => {
      if (!document.hidden) setNow(serverNow());
    };
    const sync = () => {
      if (timer) clearInterval(timer);
      timer = undefined;
      if (!document.hidden) {
        update();
        timer = setInterval(update, 5000);
      }
    };
    sync();
    const unsubscribe = subscribeServerClock(update);
    document.addEventListener("visibilitychange", sync);
    return () => {
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", sync);
      unsubscribe();
    };
  }, [active]);
  return now;
}
