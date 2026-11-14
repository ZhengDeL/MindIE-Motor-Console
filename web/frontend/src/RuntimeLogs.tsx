import { UiElement, withUiSlot, UiText } from "./uiPluginSdk";
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

function DefaultRuntimeLogFilters({ service, nodes, nodeId, role, onChange }: {
  service?: Service; nodes: NodeInfo[]; nodeId: string; role: string;
  onChange: (nodeId: string, role: string) => void;
}) {
  const ids = [...new Set((service?.deployed_placements?.length ? service.deployed_placements : service?.placements ?? []).map((placement) => placement.node_id))];
  return <>
    <Select ariaLabel="日志执行节点" value={nodeId} disabled={!service} onChange={(value) => onChange(value, role)}>
      <UiElement as="option" value=""><UiText>{"全部执行节点"}</UiText></UiElement>
      {ids.map((id) => <UiElement as="option" key={id} value={id}>{nodes.find((node) => node.id === id)?.name || id}</UiElement>)}
    </Select>
    <Select ariaLabel="日志角色" value={role} disabled={!service} onChange={(value) => onChange(nodeId, value)}>
      <UiElement as="option" value=""><UiText>{"全部角色"}</UiText></UiElement>
      {roles.map((value) => <UiElement as="option" key={value} value={value}>{value === "management" ? "management · 管理组件" : value}</UiElement>)}
    </Select>
  </>;
}

function DefaultRuntimeLogLine({ line }: { line: Log }) {
  const time = line.timestamp_valid === false ? null : recordTime(line.time);
  const level = levels.has(line.level) ? line.level : null;
  const node = line.source === "kubernetes_stdout" ? line.node : knownText(line.node_name) ? line.node_name : line.node;
  const container = knownText(line.pod) ? [line.namespace, line.pod, line.container_name].filter(knownText).join(" / ")
    : knownText(line.container_name) ? line.container_name : knownText(line.container_id) ? line.container_id.slice(0, 12) : "";
  return <UiElement as="div" className="log-line">
    {time && <UiElement as="time">{when(time)}</UiElement>}
    {level && <UiElement as="span" className={`log-level ${level.toLowerCase()}`}>{level}</UiElement>}
    {knownText(line.role) && <UiElement as="span" className="log-role log-source-role">{line.source === "agent_error" ? "Agent 诊断" : line.role}</UiElement>}
    {(knownText(node) || container) && <UiElement as="span" className="log-origin" title={[line.node_name, line.node, line.namespace, line.pod, line.container_name, line.container_id].filter(knownText).join(" · ")}>
      {knownText(node) && <UiElement as="span" className="log-node">{sourceLabel(node)}</UiElement>}
      {container && <UiElement as="span" className="log-container">{sourceLabel(container)}</UiElement>}
    </UiElement>}
    <UiElement as="span">{line.message}</UiElement>
  </UiElement>;
}

export const matchesLog = (line: Log, query: string): boolean =>
  [line.message, line.role, line.node, line.node_name, line.container_name, line.pod, levels.has(line.level) ? line.level : ""].join(" ").toLowerCase().includes(query.toLowerCase());

export const RuntimeLogFilters = withUiSlot("component.RuntimeLogFilters", DefaultRuntimeLogFilters);
export const RuntimeLogLine = withUiSlot("component.RuntimeLogLine", DefaultRuntimeLogLine);

// Reference package implementation exports.
export { DefaultRuntimeLogFilters, DefaultRuntimeLogLine };
