import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, MemoryStick, MoreHorizontal, Plus, Upload } from "lucide-react";
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

function NodeMoreMenu({ node, busy, canAdminister, onSummary, onCluster, onProbe, onDelete }: {
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
    <button ref={trigger} type="button" className="node-more-trigger" aria-label={`${node.name} 更多操作`} title="更多操作" aria-haspopup="menu" aria-expanded={open} onClick={() => {
      const rect = trigger.current!.getBoundingClientRect();
      setPosition({ left: Math.max(8, Math.min(innerWidth - 188, rect.left)), top: innerHeight - rect.bottom > 190 ? rect.bottom + 5 : Math.max(8, rect.top - 180) });
      setOpen(!open);
    }}><MoreHorizontal size={14} /></button>
    {open && createPortal(<div ref={menu} className="floating-actions node-more-menu" role="menu" aria-label={`${node.name} 节点操作`} style={position}>
      <button type="button" role="menuitem" onClick={() => choose(onSummary)}><Server size={13} />连接摘要</button>
      {onCluster && <button type="button" role="menuitem" onClick={() => choose(onCluster)}><Layers3 size={13} />集群节点</button>}
      <button type="button" role="menuitem" disabled={busy || !canAdminister} onClick={() => choose(onProbe)}><RefreshCw size={13} className={busy ? "spin" : undefined} />{busy ? "探测中…" : "连接探测"}</button>
      <button type="button" role="menuitem" className="danger-text" disabled={!canAdminister} onClick={() => choose(onDelete)}><Trash2 size={13} />移除节点</button>
    </div>, document.body)}
  </>;
}

export default function NodesPage({
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
      {!!nodes.length && <div className="node-environment-summary" role="radiogroup" aria-label="按节点连接状态筛选">
        {[{ key: "all", label: "总计", count: nodes.length }, { key: "online", label: "在线", count: nodes.filter((n) => nodeConnectionGroup(n) === "online").length }, { key: "offline", label: "离线", count: nodes.filter((n) => nodeConnectionGroup(n) === "offline").length }, { key: "unknown", label: "未探测", count: nodes.filter((n) => nodeConnectionGroup(n) === "unknown").length }].filter((segment) => segment.count > 0 || filter === segment.key).map((segment) => <label key={segment.key} className={`node-status-segment ${segment.key} ${filter === segment.key ? "selected" : ""}`}>
          <input type="radio" name="node-connection-filter" value={segment.key} checked={filter === segment.key} onChange={() => setFilter(segment.key)} />
          {segment.key !== "all" && <i aria-hidden="true" />}
          <span><b>{segment.count}</b><small>{segment.label}</small></span>
        </label>)}
      </div>}
      <section className="panel nodes-management-panel" aria-label="执行节点列表">
        <div className="node-list-toolbar">
          <div className="node-sort-controls"><span>排序：</span><div role="group" aria-label="节点排序">
            {[{ key: "name", label: "名称" }, { key: "last_seen", label: "最近连接" }, { key: "runtime", label: "运行环境" }, { key: "status", label: "连接状态" }].map((option) => <button key={option.key} type="button" aria-pressed={sort === option.key} className={sort === option.key ? "active" : ""} onClick={() => { setSort(option.key); setDescending(sort === option.key ? !descending : option.key === "last_seen"); }}>
              {option.label}{sort === option.key && <span className="node-sort-order">{descending ? <ArrowDown size={10} /> : <ArrowUp size={10} />}</span>}
            </button>)}
          </div></div>
          <div className="node-list-tools">
            {canAdminister && !demo && onRolloutHistory && <Button onClick={onRolloutHistory}><Clock3 size={14} />推送记录</Button>}
            <SearchInput value={search} onChange={setSearch} placeholder="搜索节点名称、IP 或标签" />
            {onRolloutAgent && <Button onClick={onRolloutAgent}><Upload size={14} />推送 Agent</Button>}
            {onAddNode && <Button kind="primary" disabled={!canAdminister} onClick={onAddNode}><Plus size={14} />接入节点</Button>}
          </div>
        </div>
        {items.length ? <div className="node-environment-list" role="list" aria-label="执行节点列表">
          {visibleItems.map((node) => {
            const kubernetes = isKubernetesAgent(node);
            const hostFresh = nodeObservation(node, now, demo).state === "fresh";
            return <div key={node.id} className="node-environment-card" role="listitem">
              <div className="node-environment-main">
                <div className="node-environment-identity">
                  <div className="node-icon"><img src={kubernetes ? "/runtime/kubernetes.png" : "/runtime/docker.svg"} width="60" alt="" /></div>
                  <div className="node-environment-copy">
                    <div className="node-environment-heading"><button type="button" className="node-environment-name" onClick={() => setDetailNodeId(node.id)} aria-label={`查看 ${node.name} 的 Agent 详情与配置`}>{node.name}</button><Badge status={node.status} />{isReadOnlyAgent(node) && <span className="tag" title="仅采集与查看；完成资源归属交接后才可启用运行变更。">只读接入</span>}</div>
                    <div className="node-environment-meta">
                      {Number.isFinite(Date.parse(node.last_seen)) && <time title="最近连接探测" dateTime={node.last_seen}><Clock3 size={12} />{when(node.last_seen)}</time>}
                      {nodeRuntimeInfo(node) && <span>{nodeRuntimeInfo(node)}</span>}
                      <span className="tabular">{node.address}</span>
                    </div>
                    <div className="node-environment-meta node-environment-labels">
                      {knownText(node.architecture) && <span>{node.architecture}</span>}
                      {node.labels.length > 0 && <span title={node.labels.join("、")}>{node.labels.join("、")}</span>}
                      <NodeMoreMenu node={node} busy={busy === node.id} canAdminister={canAdminister} onSummary={() => setExpanded(expanded === node.id ? "" : node.id)} onCluster={kubernetes ? () => setClusterAgent(node) : undefined} onProbe={() => void probe(node)} onDelete={() => onDelete(node)} />
                    </div>
                  </div>
                </div>
                <div className="node-row-resources">
                  <AgentNpuResources node={node} source={resources.sources.find((source) => source.agentId === node.id)} demo={demo} now={now} />
                  {hostFresh && isValidResourcePercent(node.cpu_percent) && <div className="node-resource-tile node-host-tile"><Cpu size={22} /><div><StatBar value={node.cpu_percent} /><span className="npu-runtime-label">宿主 CPU</span></div></div>}
                  {hostFresh && isValidResourcePercent(node.memory_percent) && <div className="node-resource-tile node-host-tile"><MemoryStick size={22} /><div><StatBar value={node.memory_percent} kind="blue" /><span className="npu-runtime-label">宿主内存</span></div></div>}
                </div>
              </div>
              <div className="node-actions" role="group" aria-label={`${node.name} 快捷操作`}>
                <button type="button" onClick={() => setDetailNodeId(node.id)} aria-label={`查看 ${node.name} 的详情与配置`}><Settings2 size={15} /><span>查看配置</span></button>
                <button type="button" onClick={() => setResourceNodeId(node.id)} aria-label={`查看 ${node.name} 的资源详情`}><Activity size={15} /><span>资源详情</span></button>
              </div>
              {expanded === node.id && <div className="node-expanded">
                <div>Agent 连接 <span className="tabular">{node.agent_url}</span></div>
                {knownText(node.nic_name) && <div>登记通信网卡 <code>{node.nic_name}</code></div>}
                {knownText(node.os) && <div>系统 <span>{node.os}</span></div>}
                <CredentialStatus node={node} /><NodeTlsStatus node={node} />
                {node.error && <p className="danger-text">{node.error}</p>}
              </div>}
            </div>;
          })}
        </div> : <Empty title={nodes.length ? "没有匹配的节点" : "尚未接入执行节点"} description={nodes.length ? "调整搜索条件或节点状态筛选。" : canAdminister ? "通过“推送 Agent”完成安装，再通过“接入节点”登记连接。" : "请联系管理员接入执行节点。"} icon={<Server size={28} />} />}
        <div className="node-list-footer">
          <div><span>{items.length ? <>{safePage * pageSize + 1}–{Math.min((safePage + 1) * pageSize, items.length)} / {items.length} 个节点</> : "0 个节点"}</span><select value={pageSize} aria-label="每页节点数量" onChange={(event) => setPageSize(Number(event.target.value))}>{[10, 25, 50, 100].map((count) => <option key={count} value={count}>{count}</option>)}</select></div>
          {pages > 1 && <nav className="node-list-pagination" aria-label="节点列表分页"><button type="button" disabled={safePage === 0} aria-label="上一页" onClick={() => setPage(safePage - 1)}><ChevronLeft size={14} /></button><span>{safePage + 1} / {pages}</span><button type="button" disabled={safePage >= pages - 1} aria-label="下一页" onClick={() => setPage(safePage + 1)}><ChevronRight size={14} /></button></nav>}
        </div>
      </section>
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
          footer={(dismiss) => <Button onClick={dismiss}>关闭</Button>}
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

function NodeTlsStatus({ node }: { node: NodeInfo }) {
  let protocol: string;
  try {
    protocol = new URL(node.agent_url).protocol;
  } catch {
    return null;
  }
  if (protocol === "http:")
    return (
      <div>
        连接传输<span>HTTP 连接，未使用 TLS。</span>
      </div>
    );
  if (protocol !== "https:") return null;
  const trust = node.tls_trust;
  if (!trust) return null;
  if (trust.kind === "invalid_node_ca")
    return (
      <div role="status">
        TLS 信任
        <span style={{ color: "var(--danger)" }}>
          节点 CA 无效，请更新信任配置后重新探测。
        </span>
      </div>
    );
  if (trust.kind === "deployment_trust")
    return (
      <div>
        TLS 信任<span>使用部署环境的受信证书。</span>
      </div>
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
      <div>
        TLS 信任
        <span>
          节点专属 CA
          {trust.certificates ? ` · ${trust.certificates} 张证书` : ""}
        </span>
      </div>
      {trust.sha256 && (
        <div>
          CA 摘要
          <code style={{ overflowWrap: "anywhere" }}>
            SHA256:{trust.sha256}
          </code>
        </div>
      )}
      <div role={invalid || expired || nearExpiry ? "status" : undefined}>
        CA 到期
        <span
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
        </span>
      </div>
    </>
  );
}

function CredentialStatus({ node }: { node: NodeInfo }) {
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
          <div>
            控制台凭据版本 <span>{node.credential_revision}</span>
          </div>
        )}
      {node.credentials_updated_at && (
        <div>
          凭据更新时间 <span>{when(node.credentials_updated_at)}</span>
        </div>
      )}
      {policy && (
        <>
          <div>
            Agent 凭据状态
            <span
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
            </span>
          </div>
          <div>
            当前凭据 ID <code>{policy.current.id}</code>
          </div>
          {policy.current.expires_at && (
            <div>
              当前凭据到期 <span>{when(policy.current.expires_at)}</span>
            </div>
          )}
          {policy.previous &&
            typeof policy.previous.id === "string" &&
            typeof policy.previous.active === "boolean" && (
              <div>
                旧凭据过渡
                <span>
                  {policy.previous.active
                    ? `截至 ${when(policy.previous.expires_at ?? "")}`
                    : "已失效"}
                </span>
              </div>
            )}
          <p className="muted-text">
            状态来自最近一次 Agent 校验；以连接探测结果为准。
          </p>
        </>
      )}
    </>
  );
}

function NodeCredentialsModal({
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
          <Button onClick={close} disabled={busy}>
            {outcomeUnknown ? "关闭并读取最新" : "取消"}
          </Button>
          <Button
            kind="primary"
            loading={busy}
            disabled={!token || outcomeUnknown || !credentialsAllowed}
            onClick={() => void save()}
          >
            校验并保存
          </Button>
        </>
      }
    >
      <InfoBox>
        {demo
          ? "演示环境不连接真实 Agent。"
          : "请先在节点上启用新令牌；控制台会验证连接后再保存。"}
        节点 ID 和已有服务关联会保留。这里维护管理连接，不修改模型部署 IP
        或网卡；旧令牌的撤销由节点管理员完成。
      </InfoBox>
      {Number.isSafeInteger(node.credential_revision) &&
        (node.credential_revision ?? -1) >= 0 && (
          <dl className="credential-node-info">
            <div>
              <dt>当前凭据版本</dt>
              <dd>{node.credential_revision}</dd>
            </div>
          </dl>
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
        <input
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
        <input
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
          <option value="keep">保持现有信任（默认）</option>
          <option value="replace">替换 CA PEM 证书</option>
          <option value="default">使用部署默认信任</option>
        </Select>
      </Field>
      {caMode === "replace" && (
        <Field
          label="新的 CA PEM 证书"
          required
          hint="仅输入新的公开 CA 证书，最多 64 KiB；不回显已保存证书，不接受任何私钥。"
        >
          <textarea
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
          保存时将清除该节点的专属
          CA，改用部署环境的默认信任。控制台会先按此信任验证连接，通过后才保存。
        </InfoBox>
      )}
      {busy && (
        <p className="subtle-note">
          正在验证管理连接，完成后会清除本次输入的令牌与 CA。
        </p>
      )}
    </Modal>
  );
}

export function NodeModal({
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
          <Button onClick={close} disabled={busy}>{outcomeUnknown ? "关闭并核对节点列表" : "取消"}</Button>
          <Button kind="primary" loading={busy} disabled={outcomeUnknown} onClick={submit}>
            接入节点
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      <div className="form-two">
        <Field label="节点名称" required>
          <input
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
          <input
            placeholder="192.0.2.39"
            disabled={busy || outcomeUnknown}
            maxLength={64}
            value={data.address}
            onChange={(e) => setData({ ...data, address: e.target.value })}
          />
        </Field>
      </div>
      <Field
        label="Agent 连接地址（URL）"
        required
        hint="生产远端节点使用 HTTPS；同机回环地址可使用 HTTP"
      >
        <input
          placeholder="https://agent.example.com:8443"
          disabled={busy || outcomeUnknown}
          maxLength={2048}
          value={data.agent_url}
          onChange={(e) => setData({ ...data, agent_url: e.target.value })}
        />
      </Field>
      <Field label="Agent 访问令牌" hint="仅在保存时提交，控制台不会回显令牌">
        <input
          type="password"
          disabled={busy || outcomeUnknown}
          maxLength={2000}
          autoComplete="new-password"
          value={data.agent_token}
          onChange={(e) => setData({ ...data, agent_token: e.target.value })}
          placeholder="输入节点 Agent 的访问令牌"
        />
      </Field>
      <details>
        <summary>自定义 CA 信任（可选）</summary>
        <Field label="Agent CA PEM 证书" hint="留空使用部署默认信任；仅填写公开 CA 证书，不接受私钥，最多 64 KiB。">
          <textarea value={data.agent_ca_pem} rows={5} maxLength={65536} autoComplete="off" spellCheck={false}
            disabled={busy || outcomeUnknown} onChange={(event) => setData({ ...data, agent_ca_pem: event.target.value })}
            placeholder="-----BEGIN CERTIFICATE-----" />
        </Field>
      </details>
      <div className="form-two">
        <Field label="模型通信网卡" hint="留空时使用 Agent 上报网卡">
          <input
            placeholder="例如 enp189s0f0"
            disabled={busy || outcomeUnknown}
            maxLength={15}
            value={data.nic_name}
            onChange={(e) => setData({ ...data, nic_name: e.target.value })}
          />
        </Field>
        <Field label="节点标签" hint="多个标签用逗号分隔">
          <input
            placeholder="A3, production"
            disabled={busy || outcomeUnknown}
            maxLength={4096}
            value={data.labels}
            onChange={(e) => setData({ ...data, labels: e.target.value })}
          />
        </Field>
      </div>
    </Modal>
  );
}
