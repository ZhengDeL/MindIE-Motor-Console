import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
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

function DefaultResources({
  node,
  historical = false,
}: {
  node: ClusterNode;
  historical?: boolean;
}) {
  if (historical)
    return <UiElement as="span" className="resource-state-note"><UiText>{"刷新采样后查看资源份额"}</UiText></UiElement>;
  const resources = resourceEntries(node);
  return resources.length ? (
    <UiElement as="div" className="cluster-resource-list">
      {resources.map((resource) => (
        <UiElement as="div" key={resource.key}>
          <UiElement as="span" className="cluster-resource-type" title={resource.key}>
            {resourceTypeName(resource.key)}
          </UiElement>
          <UiElement as="div" className="cluster-resource-values">
            {resource.allocatable !== null && (
              <UiElement as="span">
                <UiText>{"调度上限"}</UiText> <UiElement as="b">{resource.allocatable}</UiElement>
              </UiElement>
            )}
            {resource.capacity !== null && (
              <UiElement as="span">
                <UiText>{"节点容量"}</UiText> <UiElement as="b">{resource.capacity}</UiElement>
              </UiElement>
            )}
            <UiElement as="small"><UiText>{"份额"}</UiText></UiElement>
          </UiElement>
        </UiElement>
      ))}
    </UiElement>
  ) : (
    <UiElement as="span" className="resource-state-note"><UiText>{"NPU 资源份额待上报"}</UiText></UiElement>
  );
}

function DefaultClusterNodesPanel({
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
    <UiRegion name="layout.panel" as="section" className="cluster-nodes" aria-label={`${agent.name} 的集群节点`}>
      <UiElement as="div" className="cluster-nodes-heading">
        <UiElement as="div">
          <UiElement as="h3">
            <Layers3 size={17} />
            <UiText>{"集群节点"}</UiText>
          </UiElement>
          <UiElement as="p">
            <UiText>{"执行 Agent："}</UiText><UiElement as="b">{agent.name}</UiElement>
            {data?.cluster_version && <> <UiText>{"· Kubernetes"}</UiText> {data.cluster_version}</>}
          </UiElement>
        </UiElement>
        <Button actionId="action.ClusterNodes.001"
          loading={loading}
          onClick={() => setRefresh((value) => value + 1)}
        >
          <RefreshCw size={14} />
          <UiText>{"刷新节点"}</UiText>
        </Button>
      </UiElement>
      <InfoBox>
        <UiText>{"调度上限是可供 Pod 请求的资源额度，不是实时空闲量。节点 Ready 表示集群就绪状态，部署仍需执行资源预检。"}</UiText>
      </InfoBox>
      {data && !currentInventory && (
        <UiElement as="div" role="status" className="cluster-observation-warning">
          <UiText>{"当前采样已超过有效期，资源数值与 Ready 状态已隐藏。刷新成功后再选择部署节点。"}</UiText>
        </UiElement>
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
            <UiElement as="div" className="cluster-node-toolbar">
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
                <UiElement as="option" value="all"><UiText>{"全部节点"}</UiText></UiElement>
                <UiElement as="option" value="ready"><UiText>{"Ready"}</UiText></UiElement>
                <UiElement as="option" value="not-ready"><UiText>{"非 Ready"}</UiText></UiElement>
                <UiElement as="option" value="cordoned"><UiText>{"已禁止调度"}</UiText></UiElement>
              </Select>
              <UiElement as="span" className="muted-text">
                {items.length} <UiText>{"/"}</UiText> {data.items.length} <UiText>{"个节点 ·"}</UiText>{" "}
                {when(data.collected_at)}
              </UiElement>
            </UiElement>
            {items.length ? (
              <UiElement as="div" className="table-wrap cluster-node-table">
                <UiElement as="table">
                  <UiElement as="thead">
                    <UiElement as="tr">
                      <UiElement as="th"><UiText>{"节点 / 内网地址"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"状态 / 调度限制"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"角色 / 运行时"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"NPU 资源份额"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"详情"}</UiText></UiElement>
                    </UiElement>
                  </UiElement>
                  <UiElement as="tbody">
                    {items.map((node) => (
                      <UiElement as="tr" key={node.uid || node.name}>
                        <UiElement as="td">
                          <UiElement as="b">{node.name}</UiElement>
                          {!!node.internal_ips.length && (
                            <UiElement as="span" className="cell-secondary tabular">
                              {node.internal_ips.join(" · ")}
                            </UiElement>
                          )}
                        </UiElement>
                        <UiElement as="td">
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
                            <UiElement as="span" className="cell-secondary">
                              {node.unschedulable
                                ? "已禁止调度"
                                : "未设置禁止调度"}
                              {node.taints.length > 0 &&
                                ` · ${node.taints.length} 项污点`}
                            </UiElement>
                          )}
                        </UiElement>
                        <UiElement as="td">
                          {!!node.roles.length && (
                            <UiElement as="span">{node.roles.join(" / ")}</UiElement>
                          )}
                          {(knownText(node.architecture) ||
                            knownText(node.container_runtime)) && (
                            <UiElement as="span" className="cell-secondary">
                              {[node.architecture, node.container_runtime]
                                .filter(knownText)
                                .join(" · ")}
                            </UiElement>
                          )}
                          {!node.roles.length &&
                            !knownText(node.architecture) &&
                            !knownText(node.container_runtime) && (
                              <UiElement as="span" className="resource-state-note">
                                <UiText>{"执行环境待上报"}</UiText>
                              </UiElement>
                            )}
                        </UiElement>
                        <UiElement as="td">
                          <Resources
                            node={node}
                            historical={!currentInventory}
                          />
                        </UiElement>
                        <UiElement as="td">
                          <UiButton actionId="action.ClusterNodes.002"
                            type="button"
                            className="text-link cluster-detail-button"
                            aria-label={`查看集群节点 ${node.name}`}
                            onClick={() => setDetail(node)}
                          >
                            <UiText>{"查看"}</UiText>
                            <ChevronRight size={13} />
                          </UiButton>
                        </UiElement>
                      </UiElement>
                    ))}
                  </UiElement>
                </UiElement>
              </UiElement>
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
              <UiElement as="div" className="cluster-scheduling">
                <UiElement as="h4"><UiText>{"原生角色节点选择器"}</UiText></UiElement>
                <UiElement as="p">
                  <UiText>{"可按节点的 hostname 标签限定角色；选择会合并到原生配置，其余标签保持。留空仅移除 hostname 限定。多个标签按 AND 匹配，污点、亲和性和资源请求仍由集群调度器校验。"}</UiText>
                </UiElement>
                <UiElement as="div" className="cluster-scheduling-grid">
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
                      <UiElement as="div" className="cluster-selector" key={key}>
                        <UiElement as="label">
                          <UiElement as="span">{label}</UiElement>
                          <UiElement as="code"><UiText>{"motor_deploy_config."}</UiText>{key}</UiElement>
                          <Select
                            value={hostname}
                            onChange={(value) => onSelectHost(key, value)}
                            disabled={!valid || !currentInventory}
                            ariaLabel={`${label} 目标节点`}
                          >
                            <UiElement as="option" value=""><UiText>{"不限定 hostname"}</UiText></UiElement>
                            {hostname && !values.includes(hostname) && (
                              <UiElement as="option" value={hostname}>
                                {hostname}<UiText>{"（"}</UiText>
                                {currentInventory
                                  ? "当前集群未发现"
                                  : "当前配置"}
                                <UiText>{"）"}</UiText>
                              </UiElement>
                            )}
                            {values.map((value) => (
                              <UiElement as="option" key={value} value={value}>
                                {value}
                              </UiElement>
                            ))}
                          </Select>
                        </UiElement>
                        {!valid ? (
                          <UiElement as="span" className="danger-text">
                            <UiText>{"选择器格式无效，请先在服务配置中修正为字符串键值对象。"}</UiText>
                          </UiElement>
                        ) : (
                          <>
                            <UiElement as="span"
                              className={
                                matched.length ? "muted-text" : "warning-text"
                              }
                            >
                              {!currentInventory
                                ? "刷新集群采样后核对标签匹配"
                                : Object.keys(labels).length
                                  ? `自定义标签匹配 ${matched.length} 个节点`
                                  : "未设置自定义标签限定"}
                            </UiElement>
                            {Object.keys(labels).length > 0 && (
                              <UiElement as="code" className="cluster-selector-values">
                                {Object.entries(labels)
                                  .map(([name, value]) => `${name}=${value}`)
                                  .join(", ")}
                              </UiElement>
                            )}
                          </>
                        )}
                      </UiElement>
                    );
                  })}
                </UiElement>
                <UiElement as="p" className="subtle-note">
                  <UiText>{"以上匹配只核对自定义标签，不是部署预检。原生 Motor 还会添加硬件等调度条件；多 Pod 实例固定到单节点可能无法满足分布式布局。"}</UiText>
                </UiElement>
              </UiElement>
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
          footer={(dismiss) => <Button actionId="action.ClusterNodes.003" onClick={dismiss}><UiText>{"关闭"}</UiText></Button>}
          wide
        >
          <UiElement as="dl" className="cluster-details">
            {knownText(detail.uid) && (
              <>
                <UiElement as="dt"><UiText>{"节点 UID"}</UiText></UiElement>
                <UiElement as="dd" className="mono">{detail.uid}</UiElement>
              </>
            )}
            {knownText(detail.os) && (
              <>
                <UiElement as="dt"><UiText>{"操作系统"}</UiText></UiElement>
                <UiElement as="dd">{detail.os}</UiElement>
              </>
            )}
            {knownText(detail.architecture) && (
              <>
                <UiElement as="dt"><UiText>{"处理器架构"}</UiText></UiElement>
                <UiElement as="dd">{detail.architecture}</UiElement>
              </>
            )}
            {knownText(detail.kubelet_version) && (
              <>
                <UiElement as="dt"><UiText>{"Kubelet"}</UiText></UiElement>
                <UiElement as="dd">{detail.kubelet_version}</UiElement>
              </>
            )}
            {knownText(detail.container_runtime) && (
              <>
                <UiElement as="dt"><UiText>{"容器运行时"}</UiText></UiElement>
                <UiElement as="dd">{detail.container_runtime}</UiElement>
              </>
            )}
            {currentInventory && !!resourceEntries(detail).length && (
              <>
                <UiElement as="dt"><UiText>{"NPU 资源份额"}</UiText></UiElement>
                <UiElement as="dd">
                  <Resources node={detail} />
                </UiElement>
              </>
            )}
          </UiElement>
          <UiElement as="h4"><UiText>{"节点标签"}</UiText></UiElement>
          <UiElement as="div" className="cluster-label-list">
            {Object.entries(detail.labels).map(([key, value]) => (
              <UiElement as="div" key={key}>
                <UiElement as="code">{key}</UiElement>
                <UiElement as="span">{value || "（空值）"}</UiElement>
              </UiElement>
            ))}
          </UiElement>
          <UiElement as="h4"><UiText>{"调度污点"}</UiText></UiElement>
          {detail.taints.length ? (
            <UiElement as="div" className="cluster-label-list">
              {detail.taints.map((taint, index) => (
                <UiElement as="div" key={index}>
                  <UiElement as="code">
                    {taint.key}
                    {taint.value ? `=${taint.value}` : ""}
                  </UiElement>
                  <UiElement as="span">{taint.effect}</UiElement>
                </UiElement>
              ))}
            </UiElement>
          ) : (
            <UiElement as="p" className="muted-text">
              <UiText>{"未设置污点；仍需核对亲和性、资源请求及原生 Motor 部署条件。"}</UiText>
            </UiElement>
          )}
        </Drawer>
      )}
    </UiRegion>
  );
}

const Resources = withUiSlot("component.Resources", DefaultResources);
const ClusterNodesPanel = withUiSlot("component.ClusterNodesPanel", DefaultClusterNodesPanel);
export default ClusterNodesPanel;

// Reference package implementation exports.
export { DefaultResources, DefaultClusterNodesPanel };
