import { when, type Log, type NodeInfo, type Service } from "./api";
import { recordTime } from "./operationRecords";
import { knownText } from "./resourcePresentation";
import { Select } from "./ui";

export type LogResponse = {
  items: Log[];
  partial?: boolean;
  errors?: { node_id: string; node_name: string; code: string }[];
};
export type LogFilters = { serviceId: string; nodeId: string; role: string };
const levels = new Set(["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"]);
const roles = ["prefill", "decode", "encode", "union", "management", "coordinator", "controller", "coordinator_controller", "single_container", "kv_store", "kv_conductor", "render"];
const sourceLabel = (value: string) => value.length > 52 ? `${value.slice(0, 32)}…${value.slice(-16)}` : value;

export function logPath(serviceId: string, tail: number, nodeId: string, role: string): string {
  const query = new URLSearchParams({ tail: String(tail) });
  if (nodeId) query.set("node_id", nodeId);
  if (role) query.set("role", role);
  return `/services/${encodeURIComponent(serviceId)}/logs?${query}`;
}

export function RuntimeLogFilters({ service, nodes, nodeId, role, onChange }: {
  service?: Service; nodes: NodeInfo[]; nodeId: string; role: string;
  onChange: (nodeId: string, role: string) => void;
}) {
  const ids = [...new Set((service?.deployed_placements?.length ? service.deployed_placements : service?.placements ?? []).map((placement) => placement.node_id))];
  return <>
    <Select ariaLabel="日志执行节点" value={nodeId} disabled={!service} onChange={(value) => onChange(value, role)}>
      <option value="">全部执行节点</option>
      {ids.map((id) => <option key={id} value={id}>{nodes.find((node) => node.id === id)?.name || id}</option>)}
    </Select>
    <Select ariaLabel="日志角色" value={role} disabled={!service} onChange={(value) => onChange(nodeId, value)}>
      <option value="">全部角色</option>
      {roles.map((value) => <option key={value} value={value}>{value === "management" ? "management · 管理组件" : value}</option>)}
    </Select>
  </>;
}

export function RuntimeLogLine({ line }: { line: Log }) {
  const time = line.timestamp_valid === false ? null : recordTime(line.time);
  const level = levels.has(line.level) ? line.level : null;
  const node = line.source === "kubernetes_stdout" ? line.node : knownText(line.node_name) ? line.node_name : line.node;
  const container = knownText(line.pod) ? [line.namespace, line.pod, line.container_name].filter(knownText).join(" / ")
    : knownText(line.container_name) ? line.container_name : knownText(line.container_id) ? line.container_id.slice(0, 12) : "";
  return <div className="log-line">
    {time && <time>{when(time)}</time>}
    {level && <span className={`log-level ${level.toLowerCase()}`}>{level}</span>}
    {knownText(line.role) && <span className="log-role log-source-role">{line.source === "agent_error" ? "Agent 诊断" : line.role}</span>}
    {(knownText(node) || container) && <span className="log-origin" title={[line.node_name, line.node, line.namespace, line.pod, line.container_name, line.container_id].filter(knownText).join(" · ")}>
      {knownText(node) && <span className="log-node">{sourceLabel(node)}</span>}
      {container && <span className="log-container">{sourceLabel(container)}</span>}
    </span>}
    <span>{line.message}</span>
  </div>;
}

export const matchesLog = (line: Log, query: string): boolean =>
  [line.message, line.role, line.node, line.node_name, line.container_name, line.pod, levels.has(line.level) ? line.level : ""].join(" ").toLowerCase().includes(query.toLowerCase());
