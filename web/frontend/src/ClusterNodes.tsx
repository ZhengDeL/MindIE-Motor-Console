import { useEffect, useRef, useState } from "react";
import { ChevronRight, Layers3, RefreshCw, Server } from "./icons";
import { api, when } from "./api";
import type { NodeInfo } from "./api";
import {
  kubernetesInventoryFreshness,
  parseNpuQuantity,
  validateKubernetesInventory,
} from "./npuResources";
import type { KubernetesInventory } from "./npuResources";
import { useObservationClock } from "./resourceObservation";
import { knownText, resourceTypeName } from "./resourcePresentation";
import {
  Badge,
  Button,
  Empty,
  ErrorBox,
  InfoBox,
  Drawer,
  SearchInput,
  Select,
  Spinner,
} from "./ui";
import "./cluster-nodes.css";

export type ClusterNode = {
  name: string;
  uid: string;
  roles: string[];
  internal_ips: string[];
  ready: "True" | "False" | "Unknown";
  unschedulable: boolean;
  kubelet_version: string;
  container_runtime: string;
  architecture: string;
  os: string;
  labels: Record<string, string>;
  taints: { key: string; value?: string; effect: string }[];
  resources: {
    capacity: Record<string, string>;
    allocatable: Record<string, string>;
  };
};
export type SchedulingChoice = {
  key: string;
  label: string;
  selector: unknown;
};

export function runtimeAvailable(
  node: NodeInfo,
  runtime: "docker" | "kubernetes",
) {
  return runtime === "docker"
    ? node.docker_available !== false
    : node.kubernetes_available !== false;
}

function selectorObject(value: unknown): value is Record<string, string> {
  return (
    value != null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.values(value).every((item) => typeof item === "string")
  );
}

function resourceEntries(node: ClusterNode) {
  return Array.from(
    new Set([
      ...Object.keys(node.resources.capacity),
      ...Object.keys(node.resources.allocatable),
    ]),
  )
    .map((key) => ({
      key,
      allocatable: parseNpuQuantity(node.resources.allocatable[key]),
      capacity: parseNpuQuantity(node.resources.capacity[key]),
    }))
    .filter((value) => value.allocatable !== null || value.capacity !== null);
}

function Resources({
  node,
  historical = false,
}: {
  node: ClusterNode;
  historical?: boolean;
}) {
  if (historical)
    return <span className="resource-state-note">刷新采样后查看资源份额</span>;
  const resources = resourceEntries(node);
  return resources.length ? (
    <div className="cluster-resource-list">
      {resources.map((resource) => (
        <div key={resource.key}>
          <span className="cluster-resource-type" title={resource.key}>
            {resourceTypeName(resource.key)}
          </span>
          <div className="cluster-resource-values">
            {resource.allocatable !== null && (
              <span>
                调度上限 <b>{resource.allocatable}</b>
              </span>
            )}
            {resource.capacity !== null && (
              <span>
                节点容量 <b>{resource.capacity}</b>
              </span>
            )}
            <small>份额</small>
          </div>
        </div>
      ))}
    </div>
  ) : (
    <span className="resource-state-note">NPU 资源份额待上报</span>
  );
}

export default function ClusterNodesPanel({
  agent,
  scheduling,
  onSelectHost,
}: {
  agent: NodeInfo;
  scheduling?: SchedulingChoice[];
  onSelectHost?: (field: string, hostname: string) => void;
}) {
  const [data, setData] = useState<KubernetesInventory | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [detail, setDetail] = useState<ClusterNode | null>(null);
  const [visible, setVisible] = useState(() => !document.hidden);
  const now = useObservationClock();
  const currentInventory =
    !!data && kubernetesInventoryFreshness(data, now) === "fresh" && !error;
  const request = useRef(0);
  useEffect(() => {
    const sync = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);
  useEffect(() => {
    const sequence = ++request.current;
    if (!visible) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25_000);
    setLoading(true);
    setError("");
    setData(null);
    setDetail(null);
    api<unknown>(`/nodes/${encodeURIComponent(agent.id)}/kubernetes/nodes`, {
      signal: controller.signal,
    })
      .then(validateKubernetesInventory)
      .then((value) => {
        if (request.current === sequence) setData(value);
      })
      .catch((reason) => {
        if (request.current === sequence)
          setError(
            controller.signal.aborted ? "读取集群节点超时" : reason.message,
          );
      })
      .finally(() => {
        clearTimeout(timeout);
        if (request.current === sequence) setLoading(false);
      });
    return () => {
      request.current++;
      controller.abort();
      clearTimeout(timeout);
    };
  }, [agent.id, refresh, visible]);
  const items = (data?.items ?? []).filter((node) => {
    const text =
      `${node.name} ${node.internal_ips.join(" ")} ${node.roles.join(" ")} ${Object.entries(
        node.labels,
      )
        .map(([key, value]) => `${key}=${value}`)
        .join(" ")}`.toLowerCase();
    return (
      text.includes(search.toLowerCase()) &&
      (filter === "all" ||
        (currentInventory && (filter === "ready"
          ? node.ready === "True"
          : filter === "not-ready"
            ? node.ready !== "True"
            : node.unschedulable)))
    );
  });
  return (
    <section className="cluster-nodes" aria-label={`${agent.name} 的集群节点`}>
      <div className="cluster-nodes-heading">
        <div>
          <h3>
            <Layers3 size={17} />
            集群节点
          </h3>
          <p>
            执行 Agent：<b>{agent.name}</b>
            {data?.cluster_version && <> · Kubernetes {data.cluster_version}</>}
          </p>
        </div>
        <Button
          loading={loading}
          onClick={() => setRefresh((value) => value + 1)}
        >
          <RefreshCw size={14} />
          刷新节点
        </Button>
      </div>
      <InfoBox>
        调度上限是可供 Pod 请求的资源额度，不是实时空闲量。节点 Ready
        表示集群就绪状态，部署仍需执行资源预检。
      </InfoBox>
      {data && !currentInventory && (
        <div role="status" className="cluster-observation-warning">
          当前采样已超过有效期，资源数值与 Ready
          状态已隐藏。刷新成功后再选择部署节点。
        </div>
      )}
      {error && (
        <ErrorBox
          message={`无法读取集群节点：${error}。请核对 Agent 版本、kubeconfig 连接及 list nodes 权限。`}
        />
      )}
      {loading ? (
        <Spinner label="正在读取集群节点" />
      ) : (
        data && (
          <>
            <div className="cluster-node-toolbar">
              <SearchInput
                value={search}
                onChange={setSearch}
                placeholder="搜索集群节点、IP 或标签"
              />
              <Select
                value={filter}
                onChange={setFilter}
                ariaLabel="筛选集群节点状态"
              >
                <option value="all">全部节点</option>
                <option value="ready">Ready</option>
                <option value="not-ready">非 Ready</option>
                <option value="cordoned">已禁止调度</option>
              </Select>
              <span className="muted-text">
                {items.length} / {data.items.length} 个节点 ·{" "}
                {when(data.collected_at)}
              </span>
            </div>
            {items.length ? (
              <div className="table-wrap cluster-node-table">
                <table>
                  <thead>
                    <tr>
                      <th>节点 / 内网地址</th>
                      <th>状态 / 调度限制</th>
                      <th>角色 / 运行时</th>
                      <th>NPU 资源份额</th>
                      <th>详情</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((node) => (
                      <tr key={node.uid || node.name}>
                        <td>
                          <b>{node.name}</b>
                          {!!node.internal_ips.length && (
                            <span className="cell-secondary tabular">
                              {node.internal_ips.join(" · ")}
                            </span>
                          )}
                        </td>
                        <td>
                          <Badge
                            status={
                              !currentInventory
                                ? "unknown"
                                : node.ready === "True"
                                  ? "ready"
                                  : node.ready === "False"
                                    ? "error"
                                    : "unknown"
                            }
                          >
                            {!currentInventory
                              ? "采样待刷新"
                              : node.ready === "True"
                                ? "Ready"
                                : node.ready === "False"
                                  ? "NotReady"
                                  : "等待 Ready 上报"}
                          </Badge>
                          {currentInventory && (
                            <span className="cell-secondary">
                              {node.unschedulable
                                ? "已禁止调度"
                                : "未设置禁止调度"}
                              {node.taints.length > 0 &&
                                ` · ${node.taints.length} 项污点`}
                            </span>
                          )}
                        </td>
                        <td>
                          {!!node.roles.length && (
                            <span>{node.roles.join(" / ")}</span>
                          )}
                          {(knownText(node.architecture) ||
                            knownText(node.container_runtime)) && (
                            <span className="cell-secondary">
                              {[node.architecture, node.container_runtime]
                                .filter(knownText)
                                .join(" · ")}
                            </span>
                          )}
                          {!node.roles.length &&
                            !knownText(node.architecture) &&
                            !knownText(node.container_runtime) && (
                              <span className="resource-state-note">
                                执行环境待上报
                              </span>
                            )}
                        </td>
                        <td>
                          <Resources
                            node={node}
                            historical={!currentInventory}
                          />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="text-link cluster-detail-button"
                            aria-label={`查看集群节点 ${node.name}`}
                            onClick={() => setDetail(node)}
                          >
                            查看
                            <ChevronRight size={13} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty
                icon={<Server size={26} />}
                title={
                  data.items.length ? "没有匹配的集群节点" : "集群没有返回节点"
                }
                description={
                  data.items.length
                    ? "调整搜索词或状态筛选。"
                    : "请核对连接的集群与节点清单。"
                }
              />
            )}
            {scheduling && onSelectHost && (
              <div className="cluster-scheduling">
                <h4>原生角色节点选择器</h4>
                <p>
                  可按节点的 hostname
                  标签限定角色；选择会合并到原生配置，其余标签保持。留空仅移除
                  hostname 限定。多个标签按 AND
                  匹配，污点、亲和性和资源请求仍由集群调度器校验。
                </p>
                <div className="cluster-scheduling-grid">
                  {scheduling.map(({ key, label, selector }) => {
                    const valid =
                      selector === undefined || selectorObject(selector);
                    const labels = selectorObject(selector) ? selector : {};
                    const hostname = labels["kubernetes.io/hostname"] ?? "";
                    const matched = data.items.filter((node) =>
                      Object.entries(labels).every(
                        ([name, value]) => node.labels[name] === value,
                      ),
                    );
                    const values = Array.from(
                      new Set(
                        data.items
                          .map((node) => node.labels["kubernetes.io/hostname"])
                          .filter(Boolean),
                      ),
                    );
                    return (
                      <div className="cluster-selector" key={key}>
                        <label>
                          <span>{label}</span>
                          <code>motor_deploy_config.{key}</code>
                          <Select
                            value={hostname}
                            onChange={(value) => onSelectHost(key, value)}
                            disabled={!valid || !currentInventory}
                            ariaLabel={`${label} 目标节点`}
                          >
                            <option value="">不限定 hostname</option>
                            {hostname && !values.includes(hostname) && (
                              <option value={hostname}>
                                {hostname}（
                                {currentInventory
                                  ? "当前集群未发现"
                                  : "当前配置"}
                                ）
                              </option>
                            )}
                            {values.map((value) => (
                              <option key={value} value={value}>
                                {value}
                              </option>
                            ))}
                          </Select>
                        </label>
                        {!valid ? (
                          <span className="danger-text">
                            选择器格式无效，请先在服务配置中修正为字符串键值对象。
                          </span>
                        ) : (
                          <>
                            <span
                              className={
                                matched.length ? "muted-text" : "warning-text"
                              }
                            >
                              {!currentInventory
                                ? "刷新集群采样后核对标签匹配"
                                : Object.keys(labels).length
                                  ? `自定义标签匹配 ${matched.length} 个节点`
                                  : "未设置自定义标签限定"}
                            </span>
                            {Object.keys(labels).length > 0 && (
                              <code className="cluster-selector-values">
                                {Object.entries(labels)
                                  .map(([name, value]) => `${name}=${value}`)
                                  .join(", ")}
                              </code>
                            )}
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
                <p className="subtle-note">
                  以上匹配只核对自定义标签，不是部署预检。原生 Motor
                  还会添加硬件等调度条件；多 Pod
                  实例固定到单节点可能无法满足分布式布局。
                </p>
              </div>
            )}
          </>
        )
      )}
      {detail && (
        <Drawer
          title={`集群节点 · ${detail.name}`}
          subtitle="只读节点信息，不修改标签、污点或调度状态"
          onClose={() => setDetail(null)}
          contentKey={detail.uid || detail.name}
          footer={(dismiss) => <Button onClick={dismiss}>关闭</Button>}
          wide
        >
          <dl className="cluster-details">
            {knownText(detail.uid) && (
              <>
                <dt>节点 UID</dt>
                <dd className="mono">{detail.uid}</dd>
              </>
            )}
            {knownText(detail.os) && (
              <>
                <dt>操作系统</dt>
                <dd>{detail.os}</dd>
              </>
            )}
            {knownText(detail.architecture) && (
              <>
                <dt>处理器架构</dt>
                <dd>{detail.architecture}</dd>
              </>
            )}
            {knownText(detail.kubelet_version) && (
              <>
                <dt>Kubelet</dt>
                <dd>{detail.kubelet_version}</dd>
              </>
            )}
            {knownText(detail.container_runtime) && (
              <>
                <dt>容器运行时</dt>
                <dd>{detail.container_runtime}</dd>
              </>
            )}
            {currentInventory && !!resourceEntries(detail).length && (
              <>
                <dt>NPU 资源份额</dt>
                <dd>
                  <Resources node={detail} />
                </dd>
              </>
            )}
          </dl>
          <h4>节点标签</h4>
          <div className="cluster-label-list">
            {Object.entries(detail.labels).map(([key, value]) => (
              <div key={key}>
                <code>{key}</code>
                <span>{value || "（空值）"}</span>
              </div>
            ))}
          </div>
          <h4>调度污点</h4>
          {detail.taints.length ? (
            <div className="cluster-label-list">
              {detail.taints.map((taint, index) => (
                <div key={index}>
                  <code>
                    {taint.key}
                    {taint.value ? `=${taint.value}` : ""}
                  </code>
                  <span>{taint.effect}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted-text">
              未设置污点；仍需核对亲和性、资源请求及原生 Motor 部署条件。
            </p>
          )}
        </Drawer>
      )}
    </section>
  );
}
