import type { KubernetesInventory, AgentNpuInventory, KubernetesNpuSummary } from '@mindie-motor/ui-sdk';
export type { KubernetesInventory, AgentNpuInventory, NpuResourceGroup, KubernetesNpuSummary } from '@mindie-motor/ui-sdk';
import type { NodeInfo } from "./api";
import type { ClusterNode } from "./ClusterNodes";
import { serverNow } from "./serverClock";
import {
  isResourceInteger,
  nodeObservation,
  observationTime,
} from "./resourceObservation";

export const KUBERNETES_INVENTORY_TTL_MS = 60_000;

// These are the two resource keys projected by the Agent's read-only inventory API.
// Different keys describe different resource contracts and must never be added together.
export const NPU_RESOURCE_KEYS = [
  "huawei.com/Ascend910",
  "huawei.com/npu",
] as const;

export function kubernetesInventoryFreshness(
  inventory: KubernetesInventory,
  now = serverNow(),
) {
  const collected = observationTime(inventory.collected_at);
  const expires = inventory.expires_at
    ? observationTime(inventory.expires_at)
    : collected === null
      ? null
      : collected + KUBERNETES_INVENTORY_TTL_MS;
  if (
    collected === null ||
    collected > now + 30_000 ||
    expires === null ||
    expires <= collected
  )
    return "invalid";
  return expires <= now || now - collected > KUBERNETES_INVENTORY_TTL_MS
    ? "stale"
    : "fresh";
}

export function isKubernetesAgent(node: NodeInfo): boolean {
  return (
    node.kubernetes_available === true ||
    (node.kubernetes_available !== false && !!node.kubernetes_version)
  );
}

export function isValidResourcePercent(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 100
  );
}

/** Return an exact nonnegative integer, or null when it cannot be summed safely.
 * Quantity arithmetic stays in BigInt; fractional device shares are never rounded.
 * Bounds also prevent an untrusted exponent from allocating an enormous BigInt.
 */
export function parseNpuQuantity(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 128)
    return null;
  const match =
    /^\+?(\d+(?:\.\d*)?|\.\d+)([eE][+-]?\d+|[EPTGMK]i|[EPTGMkmun])?$/.exec(raw);
  if (!match) return null;
  const [whole = "", fraction = ""] = match[1].split(".");
  const suffix = match[2] ?? "";
  let decimalPower = 0;
  let binaryPower = 0;
  if (/^[eE][+-]?\d+$/.test(suffix)) {
    const exponent = suffix.slice(1).replace(/^[+-]/, "").replace(/^0+/, "");
    if (exponent.length > 3) return null;
    decimalPower = Number(suffix.slice(1));
    if (Math.abs(decimalPower) > 256) return null;
  } else if (suffix.endsWith("i")) {
    binaryPower = ("KMGTPE".indexOf(suffix[0]) + 1) * 10;
  } else {
    decimalPower =
      { n: -9, u: -6, m: -3, k: 3, M: 6, G: 9, T: 12, P: 15, E: 18 }[suffix] ??
      0;
  }
  decimalPower -= fraction.length;
  let numerator = BigInt((whole || "0") + fraction) * 2n ** BigInt(binaryPower);
  let denominator = 1n;
  if (decimalPower >= 0) numerator *= 10n ** BigInt(decimalPower);
  else denominator = 10n ** BigInt(-decimalPower);
  if (numerator % denominator !== 0n) return null;
  const value = (numerator / denominator).toString();
  return value.length <= 256 ? value : null;
}

function resourceKeys(node: ClusterNode): string[] {
  return NPU_RESOURCE_KEYS.filter(
    (key) =>
      Object.hasOwn(node.resources?.capacity ?? {}, key) ||
      Object.hasOwn(node.resources?.allocatable ?? {}, key),
  );
}

/** Deduplicate by NodeUID, comparing normalized quantities rather than raw strings.
 * Errors affect completeness, never turn into a zero-valued inventory.
 */
export function aggregateKubernetesNpu(
  sources: readonly AgentNpuInventory[],
  now = serverNow(),
): KubernetesNpuSummary {
  const issues: string[] = [];
  const snapshots = new Map<string, ClusterNode[]>();
  const unknownKeys = new Set<string>();
  const times: number[] = [];
  let readyAgentCount = 0;
  let staleAgentCount = 0;
  for (const source of sources) {
    if (source.status !== "ready" || !source.inventory) {
      issues.push(
        `${source.agentName}：${source.status === "loading" ? "正在采集" : source.status === "offline" ? "Agent 离线，资源未知" : source.error || "采集失败，资源未知"}`,
      );
      continue;
    }
    const freshness = kubernetesInventoryFreshness(source.inventory, now);
    if (freshness === "invalid") {
      issues.push(`${source.agentName}：采集时间无效，当前资源未知`);
      continue;
    }
    if (freshness === "stale") {
      staleAgentCount++;
      issues.push(`${source.agentName}：观测已过期，旧值未计入当前汇总`);
      continue;
    }
    readyAgentCount++;
    times.push(observationTime(source.inventory.collected_at)!);
    for (const node of source.inventory.items) {
      if (
        typeof node.uid !== "string" ||
        !node.uid ||
        node.uid.trim() !== node.uid ||
        node.uid.length > 256
      ) {
        issues.push(
          `${source.agentName} / ${node.name || "未命名节点"}：缺少有效 NodeUID，无法去重汇总`,
        );
        resourceKeys(node).forEach((key) => unknownKeys.add(key));
        continue;
      }
      const existing = snapshots.get(node.uid) ?? [];
      existing.push(node);
      snapshots.set(node.uid, existing);
    }
  }
  type WorkingGroup = {
    capacity: bigint;
    allocatable: bigint;
    capacityKnown: boolean;
    allocatableKnown: boolean;
    nodeCount: number;
    conflicts: number;
  };
  const working = new Map<string, WorkingGroup>();
  const groupFor = (key: string) => {
    if (!working.has(key))
      working.set(key, {
        capacity: 0n,
        allocatable: 0n,
        capacityKnown: true,
        allocatableKnown: true,
        nodeCount: 0,
        conflicts: 0,
      });
    return working.get(key)!;
  };
  for (const nodes of snapshots.values()) {
    const keys = new Set(nodes.flatMap(resourceKeys));
    for (const key of keys) {
      const group = groupFor(key);
      group.nodeCount++;
      const names = new Set(nodes.map((node) => node.name));
      const capacities = nodes.map((node) =>
        parseNpuQuantity(node.resources?.capacity?.[key]),
      );
      const allocatables = nodes.map((node) =>
        parseNpuQuantity(node.resources?.allocatable?.[key]),
      );
      const capacityConflict = new Set(capacities).size > 1;
      const allocatableConflict = new Set(allocatables).size > 1;
      const identityConflict = names.size > 1;
      if (identityConflict || capacityConflict || allocatableConflict) {
        group.conflicts++;
        issues.push(
          `${nodes[0].name || "未命名节点"} / ${key}：相同 NodeUID 的上报存在冲突`,
        );
      }
      for (const field of ["capacity", "allocatable"] as const) {
        const values = field === "capacity" ? capacities : allocatables;
        const conflict =
          field === "capacity" ? capacityConflict : allocatableConflict;
        if (identityConflict || conflict || values[0] === null) {
          group[`${field}Known`] = false;
          if (!identityConflict && !conflict)
            issues.push(
              `${nodes[0].name || "未命名节点"} / ${key}：${field === "capacity" ? "容量" : "可分配量"}缺失或无法精确汇总`,
            );
        } else group[field] += BigInt(values[0]);
      }
    }
  }
  for (const key of unknownKeys) {
    const group = groupFor(key);
    group.capacityKnown = false;
    group.allocatableKnown = false;
  }
  const partial = issues.length > 0;
  return {
    groups: Array.from(working, ([key, group]) => ({
      key,
      capacity: group.capacityKnown ? group.capacity.toString() : null,
      allocatable: group.allocatableKnown ? group.allocatable.toString() : null,
      nodeCount: group.nodeCount,
      conflictCount: group.conflicts,
      partial,
    })).sort((a, b) => a.key.localeCompare(b.key)),
    status: !sources.length
      ? "none"
      : readyAgentCount === 0
        ? sources.some((source) => source.status === "loading")
          ? "loading"
          : staleAgentCount
            ? "stale"
            : "error"
        : partial
          ? "partial"
          : "ready",
    agentCount: sources.length,
    readyAgentCount,
    nodeCount: snapshots.size,
    collectedAt: times.length
      ? new Date(Math.min(...times)).toISOString()
      : null,
    issues: Array.from(new Set(issues)),
  };
}

export function summarizeDockerNpu(
  nodes: readonly NodeInfo[],
  { now = serverNow(), demo = false } = {},
) {
  const agents = nodes.filter((node) => node.docker_available !== false);
  const issues: string[] = [];
  const current = agents.filter((node) => {
    if (!demo && node.demo === true) {
      issues.push(`${node.name}：演示记录未计入真实资源汇总`);
      return false;
    }
    const observation = nodeObservation(node, now, demo);
    if (observation.state !== "fresh") {
      issues.push(`${node.name}：${observation.label}，未计入当前汇总`);
      return false;
    }
    if (
      !isResourceInteger(node.npu_total) ||
      !isResourceInteger(node.npu_used) ||
      node.npu_used > node.npu_total
    ) {
      issues.push(`${node.name}：设备声明数据不完整`);
      return false;
    }
    if (
      [node.devices, node.devices_in_use].some(
        (values) =>
          values !== undefined &&
          (!Array.isArray(values) ||
            values.length > 4096 ||
            values.some((value) => !isResourceInteger(value))),
      )
    ) {
      issues.push(`${node.name}：设备标识数据无效`);
      return false;
    }
    return true;
  });
  // Host identity and daemon identity are aliases. Either may expose duplicate
  // Agents; connecting both prevents counting one physical pool twice.
  const roots = current.map((_, index) => index);
  const find = (index: number): number => {
    while (roots[index] !== index) {
      roots[index] = roots[roots[index]];
      index = roots[index];
    }
    return index;
  };
  const aliases = new Map<string, number>();
  const accepted: number[] = [];
  current.forEach((node, index) => {
    const identities =
      demo || (node.demo === true && node.resource_freshness === "demo")
        ? [`demo:${node.id}`]
        : (
            [
              ["daemon", node.daemon_id],
              ["host", node.host_identity],
            ] as const
          )
            .filter(
              ([, value]) =>
                typeof value === "string" &&
                value.trim() &&
                value.length <= 256,
            )
            .map(([kind, value]) => `${kind}:${value}`);
    if (!identities.length) {
      issues.push(`${node.name}：缺少稳定节点身份，无法去重汇总`);
      return;
    }
    accepted.push(index);
    for (const identity of identities) {
      const previous = aliases.get(identity);
      if (previous !== undefined) roots[find(index)] = find(previous);
      aliases.set(identity, index);
    }
  });
  const hosts = new Map<number, NodeInfo[]>();
  for (const index of accepted) {
    const identity = find(index);
    hosts.set(identity, [...(hosts.get(identity) || []), current[index]]);
  }
  const unique: NodeInfo[] = [];
  let duplicateAgentCount = 0;
  let knownAgentCount = 0;
  for (const reports of hosts.values()) {
    duplicateAgentCount += reports.length - 1;
    const values = new Set(
      reports.map((node) =>
        JSON.stringify([
          node.npu_total,
          node.npu_used,
          [...(node.devices || [])].sort((a, b) => a - b),
          [...(node.devices_in_use || [])].sort((a, b) => a - b),
        ]),
      ),
    );
    if (values.size > 1) {
      issues.push(
        `${reports.map((node) => node.name).join("、")}：相同宿主的声明数据冲突，未计入汇总`,
      );
      continue;
    }
    knownAgentCount += reports.length;
    unique.push(reports[0]);
  }
  const sum = (field: "npu_total" | "npu_used") =>
    unique.length
      ? unique
          .reduce((total, node) => total + BigInt(node[field]!), 0n)
          .toString()
      : null;
  return {
    agentCount: agents.length,
    total: sum("npu_total"),
    used: sum("npu_used"),
    knownAgentCount,
    uniqueHostCount: unique.length,
    duplicateAgentCount,
    partial:
      issues.length > 0 ||
      unique.some((node) => node.inventory_status === "partial"),
    issues,
  };
}

export function validateKubernetesInventory(
  value: unknown,
): KubernetesInventory {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Error("集群响应无效");
  const inventory = value as KubernetesInventory;
  if (
    !Array.isArray(inventory.items) ||
    inventory.items.length > 500 ||
    observationTime(inventory.collected_at) === null
  )
    throw Error("集群节点或采集时间无效，无法汇总");
  for (const node of inventory.items) {
    if (
      !node ||
      typeof node !== "object" ||
      typeof node.name !== "string" ||
      typeof node.uid !== "string" ||
      node.name.length > 256 ||
      node.uid.length > 256 ||
      !node.resources ||
      typeof node.resources !== "object"
    )
      throw Error("集群节点身份或资源字段无效");
    if (
      !Array.isArray(node.roles) ||
      node.roles.length > 32 ||
      node.roles.some(
        (role) => typeof role !== "string" || role.length > 128,
      ) ||
      !Array.isArray(node.internal_ips) ||
      node.internal_ips.length > 32 ||
      node.internal_ips.some(
        (ip) => typeof ip !== "string" || ip.length > 128,
      ) ||
      !["True", "False", "Unknown"].includes(node.ready) ||
      typeof node.unschedulable !== "boolean" ||
      !node.labels ||
      typeof node.labels !== "object" ||
      Array.isArray(node.labels) ||
      Object.keys(node.labels).length > 512 ||
      Object.values(node.labels).some(
        (value) => typeof value !== "string" || value.length > 1024,
      ) ||
      !Array.isArray(node.taints) ||
      node.taints.length > 128 ||
      node.taints.some(
        (taint) =>
          !taint ||
          typeof taint !== "object" ||
          typeof taint.key !== "string" ||
          typeof taint.effect !== "string" ||
          (taint.value !== undefined && typeof taint.value !== "string"),
      )
    )
      throw Error("集群节点状态或调度字段无效");
    for (const field of ["capacity", "allocatable"] as const) {
      const quantities = node.resources[field];
      if (
        !quantities ||
        typeof quantities !== "object" ||
        Array.isArray(quantities) ||
        Object.keys(quantities).length > 128 ||
        Object.values(quantities).some(
          (item) => typeof item !== "string" || item.length > 128,
        )
      )
        throw Error("集群资源数量无效");
    }
  }
  return inventory;
}
