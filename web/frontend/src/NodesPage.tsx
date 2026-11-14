import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, MemoryStick, MoreHorizontal, Plus, Upload } from "./icons";
import { Activity, Clock3, Cpu, Layers3, RefreshCw, Server, Settings2, Trash2 } from "./icons";
import { api, isReadOnlyAgent, post, when } from "./api";
import type { NodeDetail, NodeInfo } from "./api";
import {
  Badge,
  Button,
  Empty,
  ErrorBox,
  Field,
  InfoBox,
  Modal,
  SearchInput,
  Select,
  StatBar,
  Drawer,
} from "./ui";
import ClusterNodesPanel from "./ClusterNodes";
import { AgentNpuResources } from "./NpuSummary";
import { isKubernetesAgent, isValidResourcePercent } from "./npuResources";
import type { NpuResourcesState } from "./useNpuResources";
import NodeResourceDetails from "./NodeResourceDetails";
import { nodeObservation, useObservationClock } from "./resourceObservation";
import { serverNow } from "./serverClock";
import AgentDetails from "./AgentDetails";
import { knownText } from "./resourcePresentation";

function credentialEditRule(
  node: NodeInfo | NodeDetail,
  field: "credentials" | "agent_url",
) {
  const value = (node as Partial<NodeDetail>).edit_policy?.[field];
  return typeof value?.allowed === "boolean" && typeof value.reason === "string"
    ? value
    : undefined;
}

function nodeRuntimeInfo(node: NodeInfo) {
  const versions = [
    node.docker_available !== false && knownText(node.docker_version)
      ? `Docker ${node.docker_version}`
      : "",
    node.kubernetes_available !== false && knownText(node.kubernetes_version)
      ? `Kubernetes ${node.kubernetes_version}`
      : "",
  ].filter(Boolean);
  return versions.join(" · ");
}

function nodeConnectionGroup(node: NodeInfo) {
  if (["online", "healthy", "ready"].includes(node.status)) return "online";
  if (["offline", "error", "unreachable"].includes(node.status)) return "offline";
  return "unknown";
}

function DefaultNodeMoreMenu({ node, busy, canAdminister, onSummary, onCluster, onProbe, onDelete }: {
  node: NodeInfo; busy: boolean; canAdminister: boolean;
  onSummary: () => void; onCluster?: () => void; onProbe: () => void; onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const stops = () => Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
    const frame = requestAnimationFrame(() => stops()[0]?.focus());
    const close = () => setOpen(false);
    const outside = (event: Event) => {
      const target = event.target as Node;
      if (!menu.current?.contains(target) && !trigger.current?.contains(target)) close();
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close(); trigger.current?.focus(); }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const items = stops();
        if (!items.length) return;
        const current = items.indexOf(document.activeElement as HTMLButtonElement);
        const index = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[index]?.focus();
      }
      if (event.key === "Tab") close();
    };
    document.addEventListener("pointerdown", outside); document.addEventListener("focusin", outside); document.addEventListener("keydown", key);
    window.addEventListener("resize", close); window.addEventListener("scroll", close, true);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", outside); document.removeEventListener("focusin", outside); document.removeEventListener("keydown", key);
      window.removeEventListener("resize", close); window.removeEventListener("scroll", close, true);
    };
  }, [open]);
  const choose = (action: () => void) => { setOpen(false); trigger.current?.focus(); action(); };
  return <>
    <UiButton actionContext={{node, canAdminister}} actionId="nodes.menu.open" ref={trigger} type="button" className="node-more-trigger" aria-label={`${node.name} 更多操作`} title="更多操作" aria-haspopup="menu" aria-expanded={open} onClick={() => {
      const rect = trigger.current!.getBoundingClientRect();
      setPosition({ left: Math.max(8, Math.min(innerWidth - 188, rect.left)), top: innerHeight - rect.bottom > 190 ? rect.bottom + 5 : Math.max(8, rect.top - 180) });
      setOpen(!open);
    }}><MoreHorizontal size={14} /></UiButton>
    {open && createPortal(<UiElement as="div" ref={menu} className="floating-actions node-more-menu" role="menu" aria-label={`${node.name} 节点操作`} style={position}>
      <UiButton actionContext={{node, canAdminister}} actionId="nodes.summary.open" type="button" role="menuitem" onClick={() => choose(onSummary)}><Server size={13} /><UiText>{"连接摘要"}</UiText></UiButton>
      {onCluster && <UiButton actionContext={{node, canAdminister}} actionId="nodes.cluster.open" type="button" role="menuitem" onClick={() => choose(onCluster)}><Layers3 size={13} /><UiText>{"集群节点"}</UiText></UiButton>}
      <UiButton actionContext={{node, canAdminister}} actionId="nodes.probe" type="button" role="menuitem" disabled={busy || !canAdminister} onClick={() => choose(onProbe)}><RefreshCw size={13} className={busy ? "spin" : undefined} />{busy ? "探测中…" : "连接探测"}</UiButton>
      <UiButton actionContext={{node, canAdminister}} actionId="nodes.remove" type="button" role="menuitem" className="danger-text" disabled={!canAdminister} onClick={() => choose(onDelete)}><Trash2 size={13} /><UiText>{"移除节点"}</UiText></UiButton>
    </UiElement>, document.body)}
  </>;
}

function DefaultNodesPage({
  canAdminister,
  demo,
  nodes,
  resources,
  search,
  setSearch,
  refresh,
  notify,
  onDelete,
  onRolloutHistory,
  onAddNode,
  onRolloutAgent,
}: {
  nodes: NodeInfo[];
  resources: NpuResourcesState;
  search: string;
  setSearch: (s: string) => void;
  refresh: () => void;
  notify: (s: string, tone?: string) => void;
  onDelete: (n: NodeInfo) => void;
  demo: boolean;
  canAdminister: boolean;
  onRolloutHistory?: () => void;
  onAddNode?: () => void;
  onRolloutAgent?: () => void;
}) {
  const [busy, setBusy] = useState("");
  const [probeError, setProbeError] = useState("");
  const [clusterAgent, setClusterAgent] = useState<NodeInfo | null>(null);
  const [credentialNode, setCredentialNode] = useState<
    NodeInfo | NodeDetail | null
  >(null);
  const credentialOpenRequest = useRef<AbortController | null>(null);
  const credentialOpenSequence = useRef(0);
  const pageActive = useRef(true);
  const [detailNodeId, setDetailNodeId] = useState("");
  const [detailReloadRevision, setDetailReloadRevision] = useState(0);
  const detailNode = nodes.find((node) => node.id === detailNodeId);
  const [expanded, setExpanded] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("name");
  const [descending, setDescending] = useState(false);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [resourceNodeId, setResourceNodeId] = useState("");
  const resourceNode = nodes.find((node) => node.id === resourceNodeId);
  useEffect(() => {
    pageActive.current = true;
    return () => {
      pageActive.current = false;
      ++credentialOpenSequence.current;
      credentialOpenRequest.current?.abort();
    };
  }, []);
  const now = useObservationClock();
  const items = nodes.filter((node) =>
    `${node.name} ${node.address} ${node.labels.join(" ")}`.toLowerCase().includes(search.toLowerCase()) &&
    (filter === "all" || nodeConnectionGroup(node) === filter),
  ).sort((left, right) => {
    const a = sort === "last_seen" ? Date.parse(left.last_seen) || 0 : sort === "runtime" ? nodeRuntimeInfo(left) : sort === "status" ? nodeConnectionGroup(left) : left.name;
    const b = sort === "last_seen" ? Date.parse(right.last_seen) || 0 : sort === "runtime" ? nodeRuntimeInfo(right) : sort === "status" ? nodeConnectionGroup(right) : right.name;
    const order = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "zh-CN", { numeric: true });
    return (descending ? -order : order) || left.id.localeCompare(right.id);
  });
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(page, pages - 1);
  const visibleItems = items.slice(safePage * pageSize, (safePage + 1) * pageSize);
  useEffect(() => setPage(0), [filter, search, sort, descending, pageSize]);
  const probe = async (n: NodeInfo) => {
    setBusy(n.id);
    setProbeError("");
    try {
      await post(`/nodes/${n.id}/probe`);
      refresh();
      notify(`${n.name} 探测完成`);
    } catch (e) {
      setProbeError((e as Error).message);
      refresh();
    } finally {
      setBusy("");
    }
  };
  const openCredentials = async (node: NodeInfo | NodeDetail) => {
    credentialOpenRequest.current?.abort();
    const serial = ++credentialOpenSequence.current;
    if (
      credentialEditRule(node, "credentials") &&
      credentialEditRule(node, "agent_url")
    ) {
      setCredentialNode(node);
      return;
    }
    setProbeError("");
    const controller = new AbortController();
    credentialOpenRequest.current = controller;
    const timer = setTimeout(() => controller.abort(), 20_000);
    try {
      const latest = await api<NodeDetail>(
        `/nodes/${encodeURIComponent(node.id)}`,
        { signal: controller.signal },
      );
      if (!pageActive.current || credentialOpenSequence.current !== serial)
        return;
      if (
        latest.id !== node.id ||
        !credentialEditRule(latest, "credentials") ||
        !credentialEditRule(latest, "agent_url") ||
        !Number.isSafeInteger(latest.credential_revision) ||
        (latest.credential_revision ?? -1) < 0
      )
        throw Error("Agent 连接详情不完整，未打开编辑表单。");
      setCredentialNode(latest);
    } catch (reason) {
      if (!pageActive.current || credentialOpenSequence.current !== serial)
        return;
      setProbeError(
        controller.signal.aborted
          ? "读取最新 Agent 连接配置超时，请重试。"
          : (reason as Error).message,
      );
    } finally {
      clearTimeout(timer);
      if (credentialOpenRequest.current === controller)
        credentialOpenRequest.current = null;
    }
  };
  return (
    <>
      {probeError && <ErrorBox message={probeError} />}
      {!!nodes.length && <UiElement as="div" className="node-environment-summary" role="radiogroup" aria-label="按节点连接状态筛选">
        {[{ key: "all", label: "总计", count: nodes.length }, { key: "online", label: "在线", count: nodes.filter((n) => nodeConnectionGroup(n) === "online").length }, { key: "offline", label: "离线", count: nodes.filter((n) => nodeConnectionGroup(n) === "offline").length }, { key: "unknown", label: "未探测", count: nodes.filter((n) => nodeConnectionGroup(n) === "unknown").length }].filter((segment) => segment.count > 0 || filter === segment.key).map((segment) => <UiElement as="label" key={segment.key} className={`node-status-segment ${segment.key} ${filter === segment.key ? "selected" : ""}`}>
          <UiElement as="input" type="radio" name="node-connection-filter" value={segment.key} checked={filter === segment.key} onChange={() => setFilter(segment.key)} />
          {segment.key !== "all" && <UiElement as="i" aria-hidden="true" />}
          <UiElement as="span"><UiElement as="b">{segment.count}</UiElement><UiElement as="small">{segment.label}</UiElement></UiElement>
        </UiElement>)}
      </UiElement>}
      <UiRegion name="layout.panel" as="section" className="panel nodes-management-panel" aria-label="执行节点列表">
        <UiElement as="div" className="node-list-toolbar">
          <UiElement as="div" className="node-sort-controls"><UiElement as="span"><UiText>{"排序："}</UiText></UiElement><UiElement as="div" role="group" aria-label="节点排序">
            {[{ key: "name", label: "名称" }, { key: "last_seen", label: "最近连接" }, { key: "runtime", label: "运行环境" }, { key: "status", label: "连接状态" }].map((option) => <UiButton actionId="nodes.sort" key={option.key} type="button" aria-pressed={sort === option.key} className={sort === option.key ? "active" : ""} onClick={() => { setSort(option.key); setDescending(sort === option.key ? !descending : option.key === "last_seen"); }}>
              {option.label}{sort === option.key && <UiElement as="span" className="node-sort-order">{descending ? <ArrowDown size={10} /> : <ArrowUp size={10} />}</UiElement>}
            </UiButton>)}
          </UiElement></UiElement>
          <UiElement as="div" className="node-list-tools">
            {canAdminister && !demo && onRolloutHistory && <Button actionId="agents.rollout.history" onClick={onRolloutHistory}><Clock3 size={14} /><UiText>{"推送记录"}</UiText></Button>}
            <SearchInput value={search} onChange={setSearch} placeholder="搜索节点名称、IP 或标签" />
            {onRolloutAgent && <Button actionId="agents.rollout.open" onClick={onRolloutAgent}><Upload size={14} /><UiText>{"推送 Agent"}</UiText></Button>}
            {onAddNode && <Button actionId="nodes.connect.open" kind="primary" disabled={!canAdminister} onClick={onAddNode}><Plus size={14} /><UiText>{"接入节点"}</UiText></Button>}
          </UiElement>
        </UiElement>
        {items.length ? <UiElement as="div" className="node-environment-list" role="list" aria-label="执行节点列表">
          {visibleItems.map((node) => {
            const kubernetes = isKubernetesAgent(node);
            const hostFresh = nodeObservation(node, now, demo).state === "fresh";
            return <UiElement as="div" key={node.id} className="node-environment-card" role="listitem">
              <UiElement as="div" className="node-environment-main">
                <UiElement as="div" className="node-environment-identity">
                  <UiElement as="div" className="node-icon"><UiElement as="img" src={kubernetes ? "/runtime/kubernetes.png" : "/runtime/docker.svg"} width="60" alt="" /></UiElement>
                  <UiElement as="div" className="node-environment-copy">
                    <UiElement as="div" className="node-environment-heading"><UiButton actionId="nodes.details.open" type="button" className="node-environment-name" onClick={() => setDetailNodeId(node.id)} aria-label={`查看 ${node.name} 的 Agent 详情与配置`}>{node.name}</UiButton><Badge status={node.status} />{isReadOnlyAgent(node) && <UiElement as="span" className="tag" title="仅采集与查看；完成资源归属交接后才可启用运行变更。"><UiText>{"只读接入"}</UiText></UiElement>}</UiElement>
                    <UiElement as="div" className="node-environment-meta">
                      {Number.isFinite(Date.parse(node.last_seen)) && <UiElement as="time" title="最近连接探测" dateTime={node.last_seen}><Clock3 size={12} />{when(node.last_seen)}</UiElement>}
                      {nodeRuntimeInfo(node) && <UiElement as="span">{nodeRuntimeInfo(node)}</UiElement>}
                      <UiElement as="span" className="tabular">{node.address}</UiElement>
                    </UiElement>
                    <UiElement as="div" className="node-environment-meta node-environment-labels">
                      {knownText(node.architecture) && <UiElement as="span">{node.architecture}</UiElement>}
                      {node.labels.length > 0 && <UiElement as="span" title={node.labels.join("、")}>{node.labels.join("、")}</UiElement>}
                      <NodeMoreMenu node={node} busy={busy === node.id} canAdminister={canAdminister} onSummary={() => setExpanded(expanded === node.id ? "" : node.id)} onCluster={kubernetes ? () => setClusterAgent(node) : undefined} onProbe={() => void probe(node)} onDelete={() => onDelete(node)} />
                    </UiElement>
                  </UiElement>
                </UiElement>
                <UiElement as="div" className="node-row-resources">
                  <AgentNpuResources node={node} source={resources.sources.find((source) => source.agentId === node.id)} demo={demo} now={now} />
                  {hostFresh && isValidResourcePercent(node.cpu_percent) && <UiElement as="div" className="node-resource-tile node-host-tile"><Cpu size={22} /><UiElement as="div"><StatBar value={node.cpu_percent} /><UiElement as="span" className="npu-runtime-label"><UiText>{"宿主 CPU"}</UiText></UiElement></UiElement></UiElement>}
                  {hostFresh && isValidResourcePercent(node.memory_percent) && <UiElement as="div" className="node-resource-tile node-host-tile"><MemoryStick size={22} /><UiElement as="div"><StatBar value={node.memory_percent} kind="blue" /><UiElement as="span" className="npu-runtime-label"><UiText>{"宿主内存"}</UiText></UiElement></UiElement></UiElement>}
                </UiElement>
              </UiElement>
              <UiElement as="div" className="node-actions" role="group" aria-label={`${node.name} 快捷操作`}>
                <UiButton actionId="nodes.details.open" type="button" onClick={() => setDetailNodeId(node.id)} aria-label={`查看 ${node.name} 的详情与配置`}><Settings2 size={15} /><UiElement as="span"><UiText>{"查看配置"}</UiText></UiElement></UiButton>
                <UiButton actionId="nodes.resources.open" type="button" onClick={() => setResourceNodeId(node.id)} aria-label={`查看 ${node.name} 的资源详情`}><Activity size={15} /><UiElement as="span"><UiText>{"资源详情"}</UiText></UiElement></UiButton>
              </UiElement>
              {expanded === node.id && <UiElement as="div" className="node-expanded">
                <UiElement as="div"><UiText>{"Agent 连接"}</UiText> <UiElement as="span" className="tabular">{node.agent_url}</UiElement></UiElement>
                {knownText(node.nic_name) && <UiElement as="div"><UiText>{"登记通信网卡"}</UiText> <UiElement as="code">{node.nic_name}</UiElement></UiElement>}
                {knownText(node.os) && <UiElement as="div"><UiText>{"系统"}</UiText> <UiElement as="span">{node.os}</UiElement></UiElement>}
                <CredentialStatus node={node} /><NodeTlsStatus node={node} />
                {node.error && <UiElement as="p" className="danger-text">{node.error}</UiElement>}
              </UiElement>}
            </UiElement>;
          })}
        </UiElement> : <Empty title={nodes.length ? "没有匹配的节点" : "尚未接入执行节点"} description={nodes.length ? "调整搜索条件或节点状态筛选。" : canAdminister ? "通过“推送 Agent”完成安装，再通过“接入节点”登记连接。" : "请联系管理员接入执行节点。"} icon={<Server size={28} />} />}
        <UiElement as="div" className="node-list-footer">
          <UiElement as="div"><UiElement as="span">{items.length ? <>{safePage * pageSize + 1}<UiText>{"–"}</UiText>{Math.min((safePage + 1) * pageSize, items.length)} <UiText>{"/"}</UiText> {items.length} <UiText>{"个节点"}</UiText></> : "0 个节点"}</UiElement><UiElement as="select" value={pageSize} aria-label="每页节点数量" onChange={(event) => setPageSize(Number(event.target.value))}>{[10, 25, 50, 100].map((count) => <UiElement as="option" key={count} value={count}>{count}</UiElement>)}</UiElement></UiElement>
          {pages > 1 && <UiElement as="nav" className="node-list-pagination" aria-label="节点列表分页"><UiButton actionId="nodes.page.previous" type="button" disabled={safePage === 0} aria-label="上一页" onClick={() => setPage(safePage - 1)}><ChevronLeft size={14} /></UiButton><UiElement as="span">{safePage + 1} <UiText>{"/"}</UiText> {pages}</UiElement><UiButton actionId="nodes.page.next" type="button" disabled={safePage >= pages - 1} aria-label="下一页" onClick={() => setPage(safePage + 1)}><ChevronRight size={14} /></UiButton></UiElement>}
        </UiElement>
      </UiRegion>
      {detailNode && (
        <AgentDetails
          key={detailNode.id}
          node={detailNode}
          canAdminister={canAdminister}
          demo={demo}
          reloadRevision={detailReloadRevision}
          connectionSummary={(node) => (
            <>
              <CredentialStatus node={node} />
              <NodeTlsStatus node={node} />
            </>
          )}
          onCredentials={(node) => void openCredentials(node)}
          onSaved={(node) => {
            refresh();
            notify(`${node.name} 的登记信息已保存`);
          }}
          onClose={() => setDetailNodeId("")}
        />
      )}
      {resourceNode && (
        <Modal
          title="资源详情"
          subtitle={`${resourceNode.name} · ${resourceNode.address}`}
          wide
          contentKey={resourceNode.id}
          onClose={() => setResourceNodeId("")}
        >
          <NodeResourceDetails
            node={resourceNode}
            now={now}
            demo={demo}
            clusterSource={resources.sources.find(
              (source) => source.agentId === resourceNode.id,
            )}
            onRefresh={() => {
              refresh();
              resources.refresh();
            }}
          />
        </Modal>
      )}
      {clusterAgent && (
        <Drawer
          title="Kubernetes 集群资源"
          subtitle="查看执行 Agent 所连接的真实节点与调度条件"
          wide
          contentKey={clusterAgent.id}
          onClose={() => setClusterAgent(null)}
          footer={(dismiss) => <Button actionId="nodes.dialog.dismiss" onClick={dismiss}><UiText>{"关闭"}</UiText></Button>}
        >
          <ClusterNodesPanel agent={clusterAgent} />
        </Drawer>
      )}
      {credentialNode && canAdminister && (
        <NodeCredentialsModal
          node={credentialNode}
          demo={demo}
          onClose={(reload = false) => {
            setCredentialNode(null);
            if (reload) {
              setDetailReloadRevision((revision) => revision + 1);
              refresh();
            }
          }}
          onSaved={() => {
            setCredentialNode(null);
            setDetailReloadRevision((revision) => revision + 1);
            refresh();
            notify(
              demo ? "演示凭据已更新，未连接 Agent" : "新凭据已校验并保存",
            );
          }}
        />
      )}
    </>
  );
}

function DefaultNodeTlsStatus({ node }: { node: NodeInfo }) {
  let protocol: string;
  try {
    protocol = new URL(node.agent_url).protocol;
  } catch {
    return null;
  }
  if (protocol === "http:")
    return (
      <UiElement as="div">
        <UiText>{"连接传输"}</UiText><UiElement as="span"><UiText>{"HTTP 连接，未使用 TLS。"}</UiText></UiElement>
      </UiElement>
    );
  if (protocol !== "https:") return null;
  const trust = node.tls_trust;
  if (!trust) return null;
  if (trust.kind === "invalid_node_ca")
    return (
      <UiElement as="div" role="status">
        <UiText>{"TLS 信任"}</UiText>
        <UiElement as="span" style={{ color: "var(--danger)" }}>
          <UiText>{"节点 CA 无效，请更新信任配置后重新探测。"}</UiText>
        </UiElement>
      </UiElement>
    );
  if (trust.kind === "deployment_trust")
    return (
      <UiElement as="div">
        <UiText>{"TLS 信任"}</UiText><UiElement as="span"><UiText>{"使用部署环境的受信证书。"}</UiText></UiElement>
      </UiElement>
    );
  const expires = trust.ca_expires_at ? Date.parse(trust.ca_expires_at) : NaN;
  const remaining = expires - serverNow();
  const invalid = !Number.isFinite(expires);
  const expired = !invalid && remaining <= 0;
  const nearExpiry = !invalid && remaining > 0 && remaining <= 30 * 86400_000;
  const expiryText = invalid
    ? "到期时间未能读取，请检查节点信任配置。"
    : `${when(trust.ca_expires_at!)}${
        expired
          ? " · 已过期，请更新节点信任。"
          : nearExpiry
            ? " · 将在 30 天内到期，请提前更新节点信任。"
            : ""
      }`;
  return (
    <>
      <UiElement as="div">
        <UiText>{"TLS 信任"}</UiText>
        <UiElement as="span">
          <UiText>{"节点专属 CA"}</UiText>
          {trust.certificates ? ` · ${trust.certificates} 张证书` : ""}
        </UiElement>
      </UiElement>
      {trust.sha256 && (
        <UiElement as="div">
          <UiText>{"CA 摘要"}</UiText>
          <UiElement as="code" style={{ overflowWrap: "anywhere" }}>
            <UiText>{"SHA256:"}</UiText>{trust.sha256}
          </UiElement>
        </UiElement>
      )}
      <UiElement as="div" role={invalid || expired || nearExpiry ? "status" : undefined}>
        <UiText>{"CA 到期"}</UiText>
        <UiElement as="span"
          style={{
            color:
              invalid || expired
                ? "var(--danger)"
                : nearExpiry
                  ? "var(--warning)"
                  : undefined,
          }}
        >
          {expiryText}
        </UiElement>
      </UiElement>
    </>
  );
}

function DefaultCredentialStatus({ node }: { node: NodeInfo }) {
  const candidate = node.agent_metadata?.credential_policy;
  const policy =
    candidate &&
    typeof candidate.legacy === "boolean" &&
    typeof candidate.disabled === "boolean" &&
    candidate.current &&
    typeof candidate.current.id === "string" &&
    typeof candidate.current.active === "boolean" &&
    (candidate.current.expires_at === null ||
      typeof candidate.current.expires_at === "string")
      ? candidate
      : null;
  return (
    <>
      {Number.isSafeInteger(node.credential_revision) &&
        (node.credential_revision ?? -1) >= 0 && (
          <UiElement as="div">
            <UiText>{"控制台凭据版本"}</UiText> <UiElement as="span">{node.credential_revision}</UiElement>
          </UiElement>
        )}
      {node.credentials_updated_at && (
        <UiElement as="div">
          <UiText>{"凭据更新时间"}</UiText> <UiElement as="span">{when(node.credentials_updated_at)}</UiElement>
        </UiElement>
      )}
      {policy && (
        <>
          <UiElement as="div">
            <UiText>{"Agent 凭据状态"}</UiText>
            <UiElement as="span"
              className={
                policy.disabled || !policy.current.active ? "danger-text" : ""
              }
            >
              {policy.disabled
                ? "已停用"
                : !policy.current.active
                  ? "当前凭据已失效"
                  : policy.legacy
                    ? "兼容旧式令牌"
                    : "当前凭据有效"}
            </UiElement>
          </UiElement>
          <UiElement as="div">
            <UiText>{"当前凭据 ID"}</UiText> <UiElement as="code">{policy.current.id}</UiElement>
          </UiElement>
          {policy.current.expires_at && (
            <UiElement as="div">
              <UiText>{"当前凭据到期"}</UiText> <UiElement as="span">{when(policy.current.expires_at)}</UiElement>
            </UiElement>
          )}
          {policy.previous &&
            typeof policy.previous.id === "string" &&
            typeof policy.previous.active === "boolean" && (
              <UiElement as="div">
                <UiText>{"旧凭据过渡"}</UiText>
                <UiElement as="span">
                  {policy.previous.active
                    ? `截至 ${when(policy.previous.expires_at ?? "")}`
                    : "已失效"}
                </UiElement>
              </UiElement>
            )}
          <UiElement as="p" className="muted-text">
            <UiText>{"状态来自最近一次 Agent 校验；以连接探测结果为准。"}</UiText>
          </UiElement>
        </>
      )}
    </>
  );
}

function DefaultNodeCredentialsModal({
  node,
  demo,
  onClose,
  onSaved,
}: {
  node: NodeInfo | NodeDetail;
  demo: boolean;
  onClose: (reload?: boolean) => void;
  onSaved: () => void;
}) {
  const [token, setToken] = useState("");
  const [agentUrl, setAgentUrl] = useState(node.agent_url);
  const [caMode, setCaMode] = useState<"keep" | "replace" | "default">("keep");
  const [caPem, setCaPem] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  const active = useRef(true);
  const request = useRef<AbortController | null>(null);
  const rule = (field: "credentials" | "agent_url") =>
    credentialEditRule(node, field);
  // A list entry may not include policy; the backend remains the final check.
  const credentialsAllowed = rule("credentials")?.allowed !== false;
  const urlAllowed = rule("agent_url")?.allowed !== false;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      request.current?.abort();
    };
  }, []);
  const close = () => {
    if (request.current) return;
    setToken("");
    setCaPem("");
    setCaMode("keep");
    setAgentUrl(node.agent_url);
    onClose(outcomeUnknown);
  };
  const save = async () => {
    if (request.current || outcomeUnknown || !credentialsAllowed) return;
    setError("");
    const destination = urlAllowed ? agentUrl.trim() : node.agent_url;
    if (!destination) {
      setError("请填写 Agent 地址");
      return;
    }
    const pem = caPem.trim();
    if (
      caMode === "replace" &&
      (!pem ||
        new TextEncoder().encode(pem).length > 64 * 1024 ||
        /PRIVATE\s+KEY/i.test(pem) ||
        !pem.includes("-----BEGIN CERTIFICATE-----") ||
        !pem.includes("-----END CERTIFICATE-----"))
    ) {
      setError(
        "请填写不超过 64 KiB 的 CA PEM 证书，不能包含私钥；证书有效性由控制台校验。",
      );
      return;
    }
    if (
      token.length < 24 ||
      token.length > 2000 ||
      !/^[\x21-\x7e]+$/.test(token)
    ) {
      setError("请输入 24 至 2000 字符的 Agent 令牌，不能包含空白字符");
      return;
    }
    setBusy(true);
    const controller = new AbortController();
    request.current = controller;
    const timer = setTimeout(() => controller.abort(), 20_000);
    try {
      await api(`/nodes/${encodeURIComponent(node.id)}/credentials`, {
        method: "PATCH",
        signal: controller.signal,
        body: JSON.stringify({
          agent_token: token,
          expected_revision: node.credential_revision ?? 0,
          agent_url: destination,
          ...(caMode === "keep"
            ? {}
            : { agent_ca_pem: caMode === "default" ? "" : pem }),
        }),
      });
      if (!active.current) return;
      setToken("");
      setCaPem("");
      setCaMode("keep");
      onSaved();
    } catch (error) {
      if (active.current) {
        const failure = error as Error & { outcomeUnknown?: boolean };
        if (controller.signal.aborted || failure.outcomeUnknown) {
          setOutcomeUnknown(true);
          setError(
            "请求结果尚未确认，服务器可能已保存；本次令牌和 CA 输入已清除。请关闭并读取最新配置核对，系统不会自动重发。",
          );
        } else setError(failure.message);
      }
    } finally {
      clearTimeout(timer);
      if (request.current === controller) request.current = null;
      if (active.current) {
        setToken("");
        setCaPem("");
        setBusy(false);
      }
    }
  };
  return (
    <Modal
      title="更新 Agent 连接与凭据"
      subtitle={node.name}
      onClose={close}
      closeDisabled={busy}
      footer={
        <>
          <Button actionId="nodes.connect.cancel" onClick={close} disabled={busy}>
            {outcomeUnknown ? "关闭并读取最新" : "取消"}
          </Button>
          <Button actionId="nodes.connect"
            kind="primary"
            loading={busy}
            disabled={!token || outcomeUnknown || !credentialsAllowed}
            onClick={() => void save()}
          >
            <UiText>{"校验并保存"}</UiText>
          </Button>
        </>
      }
    >
      <InfoBox>
        {demo
          ? "演示环境不连接真实 Agent。"
          : "请先在节点上启用新令牌；控制台会验证连接后再保存。"}
        <UiText>{"节点 ID 和已有服务关联会保留。这里维护管理连接，不修改模型部署 IP 或网卡；旧令牌的撤销由节点管理员完成。"}</UiText>
      </InfoBox>
      {Number.isSafeInteger(node.credential_revision) &&
        (node.credential_revision ?? -1) >= 0 && (
          <UiElement as="dl" className="credential-node-info">
            <UiElement as="div">
              <UiElement as="dt"><UiText>{"当前凭据版本"}</UiText></UiElement>
              <UiElement as="dd">{node.credential_revision}</UiElement>
            </UiElement>
          </UiElement>
        )}
      {error && <ErrorBox message={error} />}
      {!credentialsAllowed && (
        <InfoBox>
          {rule("credentials")?.reason || "当前不允许更新凭据。"}
        </InfoBox>
      )}
      <Field
        label="Agent 连接地址（URL）"
        required
        hint={
          urlAllowed
            ? "可更新为已启用的 HTTPS 入口；验证成功后保留原节点及服务关联"
            : rule("agent_url")?.reason ||
              "当前 URL 不可更改，仍可在原地址轮换令牌和 CA。"
        }
      >
        <UiElement as="input"
          type="url"
          aria-label="Agent 连接地址（URL）"
          value={agentUrl}
          maxLength={2048}
          autoComplete="off"
          disabled={
            busy || outcomeUnknown || !urlAllowed || !credentialsAllowed
          }
          onChange={(event) => setAgentUrl(event.target.value)}
          placeholder="https://agent.example.com:8443"
        />
      </Field>
      <Field
        label="新的 Agent 访问令牌"
        required
        hint="令牌仅提交给控制台校验，不会回显或写入浏览器存储"
      >
        <UiElement as="input"
          type="password"
          autoComplete="new-password"
          disabled={busy || outcomeUnknown || !credentialsAllowed}
          spellCheck={false}
          value={token}
          maxLength={2000}
          aria-label="新的 Agent 访问令牌"
          onChange={(event) => setToken(event.target.value)}
          placeholder="输入已在节点上启用的新令牌"
          onKeyDown={(event) => {
            if (event.key === "Enter" && !busy && token) void save();
          }}
        />
      </Field>
      <Field
        label="CA 信任操作"
        hint="只更新控制台的连接信任，验证成功后保存；不会更改宿主或 Agent 的服务器证书。"
      >
        <Select
          ariaLabel="CA 信任操作"
          value={caMode}
          disabled={busy || outcomeUnknown || !credentialsAllowed}
          onChange={(value) => {
            setCaMode(value as "keep" | "replace" | "default");
            setCaPem("");
            setError("");
          }}
        >
          <UiElement as="option" value="keep"><UiText>{"保持现有信任（默认）"}</UiText></UiElement>
          <UiElement as="option" value="replace"><UiText>{"替换 CA PEM 证书"}</UiText></UiElement>
          <UiElement as="option" value="default"><UiText>{"使用部署默认信任"}</UiText></UiElement>
        </Select>
      </Field>
      {caMode === "replace" && (
        <Field
          label="新的 CA PEM 证书"
          required
          hint="仅输入新的公开 CA 证书，最多 64 KiB；不回显已保存证书，不接受任何私钥。"
        >
          <UiElement as="textarea"
            aria-label="新的 CA PEM 证书"
            value={caPem}
            rows={7}
            maxLength={65536}
            autoComplete="off"
            spellCheck={false}
            disabled={busy || outcomeUnknown || !credentialsAllowed}
            onChange={(event) => {
              setCaPem(event.target.value);
              setError("");
            }}
            placeholder="-----BEGIN CERTIFICATE-----"
          />
        </Field>
      )}
      {caMode === "default" && (
        <InfoBox>
          <UiText>{"保存时将清除该节点的专属 CA，改用部署环境的默认信任。控制台会先按此信任验证连接，通过后才保存。"}</UiText>
        </InfoBox>
      )}
      {busy && (
        <UiElement as="p" className="subtle-note">
          <UiText>{"正在验证管理连接，完成后会清除本次输入的令牌与 CA。"}</UiText>
        </UiElement>
      )}
    </Modal>
  );
}

function DefaultNodeModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const [data, setData] = useState({
    name: "",
    address: "",
    agent_url: "",
    agent_token: "",
    agent_ca_pem: "",
    nic_name: "",
    labels: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcomeUnknown, setOutcomeUnknown] = useState(false);
  const active = useRef(true);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      request.current?.abort();
    };
  }, []);
  const close = () => {
    if (request.current) return;
    setData((current) => ({ ...current, agent_token: "", agent_ca_pem: "" }));
    onClose();
  };
  const submit = async () => {
    if (request.current || outcomeUnknown) return;
    setError("");
    if (!data.name.trim() || !data.address.trim() || !data.agent_url.trim()) {
      setError("请填写节点名称、地址与 Agent URL");
      return;
    }
    const certificate = data.agent_ca_pem.trim();
    if (certificate && (new TextEncoder().encode(certificate).length > 64 * 1024 ||
      /PRIVATE\s+KEY/i.test(certificate) || !certificate.includes("-----BEGIN CERTIFICATE-----") ||
      !certificate.includes("-----END CERTIFICATE-----"))) {
      setError("自定义 CA 须为不超过 64 KiB 的公开 PEM 证书，不能包含私钥；留空使用部署默认信任。");
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    const timer = setTimeout(() => controller.abort(), 20_000);
    try {
      await api("/nodes", {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          ...data,
          name: data.name.trim(),
          address: data.address.trim(),
          agent_url: data.agent_url.trim(),
          agent_ca_pem: certificate,
          labels: data.labels
            .split(/[,，]/)
            .map((s) => s.trim())
            .filter(Boolean),
        }),
      });
      if (!active.current) return;
      setData((current) => ({ ...current, agent_token: "", agent_ca_pem: "" }));
      onSaved();
    } catch (e) {
      if (!active.current) return;
      const failure = e as Error & { outcomeUnknown?: boolean };
      if (controller.signal.aborted || failure.outcomeUnknown) {
        setOutcomeUnknown(true);
        setError("接入结果尚未确认，节点可能已经保存；本次访问令牌已清除。请关闭窗口并刷新节点列表核对，确认后再接入，系统不会自动重发。");
      } else setError(failure.message);
    } finally {
      clearTimeout(timer);
      if (request.current === controller) request.current = null;
      if (active.current) {
        setData((current) => ({ ...current, agent_token: "", agent_ca_pem: "" }));
        setBusy(false);
      }
    }
  };
  return (
    <Modal
      title="接入计算节点"
      subtitle="通过 Agent 连接 Docker 或 Kubernetes 执行环境"
      onClose={close}
      closeDisabled={busy}
      footer={
        <>
          <Button actionId="nodes.connect.cancel" onClick={close} disabled={busy}>{outcomeUnknown ? "关闭并核对节点列表" : "取消"}</Button>
          <Button actionId="nodes.connect.submit" kind="primary" loading={busy} disabled={outcomeUnknown} onClick={submit}>
            <UiText>{"接入节点"}</UiText>
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      <UiElement as="div" className="form-two">
        <Field label="节点名称" required>
          <UiElement as="input"
            placeholder="例如 npu-node-01"
            disabled={busy || outcomeUnknown}
            maxLength={100}
            value={data.name}
            onChange={(e) => setData({ ...data, name: e.target.value })}
          />
        </Field>
        <Field
          label="模型部署 IPv4 地址"
          required
          hint="用于模型部署与通信，与 Agent 管理连接 URL 分开配置"
        >
          <UiElement as="input"
            placeholder="192.0.2.39"
            disabled={busy || outcomeUnknown}
            maxLength={64}
            value={data.address}
            onChange={(e) => setData({ ...data, address: e.target.value })}
          />
        </Field>
      </UiElement>
      <Field
        label="Agent 连接地址（URL）"
        required
        hint="生产远端节点使用 HTTPS；同机回环地址可使用 HTTP"
      >
        <UiElement as="input"
          placeholder="https://agent.example.com:8443"
          disabled={busy || outcomeUnknown}
          maxLength={2048}
          value={data.agent_url}
          onChange={(e) => setData({ ...data, agent_url: e.target.value })}
        />
      </Field>
      <Field label="Agent 访问令牌" hint="仅在保存时提交，控制台不会回显令牌">
        <UiElement as="input"
          type="password"
          disabled={busy || outcomeUnknown}
          maxLength={2000}
          autoComplete="new-password"
          value={data.agent_token}
          onChange={(e) => setData({ ...data, agent_token: e.target.value })}
          placeholder="输入节点 Agent 的访问令牌"
        />
      </Field>
      <UiElement as="details">
        <UiElement as="summary"><UiText>{"自定义 CA 信任（可选）"}</UiText></UiElement>
        <Field label="Agent CA PEM 证书" hint="留空使用部署默认信任；仅填写公开 CA 证书，不接受私钥，最多 64 KiB。">
          <UiElement as="textarea" value={data.agent_ca_pem} rows={5} maxLength={65536} autoComplete="off" spellCheck={false}
            disabled={busy || outcomeUnknown} onChange={(event) => setData({ ...data, agent_ca_pem: event.target.value })}
            placeholder="-----BEGIN CERTIFICATE-----" />
        </Field>
      </UiElement>
      <UiElement as="div" className="form-two">
        <Field label="模型通信网卡" hint="留空时使用 Agent 上报网卡">
          <UiElement as="input"
            placeholder="例如 enp189s0f0"
            disabled={busy || outcomeUnknown}
            maxLength={15}
            value={data.nic_name}
            onChange={(e) => setData({ ...data, nic_name: e.target.value })}
          />
        </Field>
        <Field label="节点标签" hint="多个标签用逗号分隔">
          <UiElement as="input"
            placeholder="A3, production"
            disabled={busy || outcomeUnknown}
            maxLength={4096}
            value={data.labels}
            onChange={(e) => setData({ ...data, labels: e.target.value })}
          />
        </Field>
      </UiElement>
    </Modal>
  );
}

const NodeMoreMenu = withUiSlot("component.NodeMoreMenu", DefaultNodeMoreMenu);
const NodesPage = withUiSlot("component.NodesPage", DefaultNodesPage);
export default NodesPage;
const NodeTlsStatus = withUiSlot("component.NodeTlsStatus", DefaultNodeTlsStatus);
const CredentialStatus = withUiSlot("component.CredentialStatus", DefaultCredentialStatus);
const NodeCredentialsModal = withUiSlot("component.NodeCredentialsModal", DefaultNodeCredentialsModal);
export const NodeModal = withUiSlot("component.NodeModal", DefaultNodeModal);

// Reference package implementation exports.
export { DefaultNodeMoreMenu, DefaultNodesPage, DefaultNodeTlsStatus, DefaultCredentialStatus, DefaultNodeCredentialsModal, DefaultNodeModal };
