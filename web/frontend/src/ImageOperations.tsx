import { useEffect, useRef, useState } from "react";
import { api, createOperationKey, isReadOnlyAgent, post, when } from "./api";
import type { NodeInfo } from "./api";
import { Badge, Button, Confirm, Copy, ErrorBox, Field, InfoBox, Modal, Select } from "./ui";
import { Download, RefreshCw } from "./icons";
import { usePollingQuery } from "./usePollingQuery";
import Pagination from "./Pagination";
import { knownText } from "./resourcePresentation";
import { imageOperationStorageKey } from "./browserStateKeys";

export type ImageRemoval = { id: string; node_id: string; node_name: string; tags: string[] };
type Receipt = {
  operation_id: string; operation_status: string; action?: string; status?: string;
  message?: string; error?: string; resources_may_exist?: boolean;
  result?: { image_id?: string; image_reference?: string; repo_digests?: string[]; size_bytes?: number };
};
type Tracked = { node_id: string; operation_id: string; image: string; action: "pull" | "remove" | "lookup" };
type OperationRecord = Receipt & { node_id: string; reference: string; updated_at?: string };
function receiptFor(value: Receipt | { operation: Receipt }, operationId: string): Receipt {
  const receipt = value && typeof value === "object" && "operation" in value ? value.operation : value as Receipt;
  if (!receipt || receipt.operation_id !== operationId || typeof receipt.operation_status !== "string" || !receipt.operation_status.trim())
    throw Error("回执内容或操作标识不匹配，未作为本次操作结果展示。");
  return receipt;
}
const terminal = (receipt: Receipt | null) => !!receipt && (receipt.operation_status === "completed" ||
  (["failed", "interrupted"].includes(receipt.operation_status) && receipt.status !== "unknown" && receipt.resources_may_exist !== true));
const uncertain = (receipt: Receipt | null) => !!receipt && receipt.operation_status !== "completed" &&
  (receipt.status === "unknown" || (["failed", "interrupted"].includes(receipt.operation_status) && receipt.resources_may_exist === true));
const statusLabel = (receipt: Receipt | null) => uncertain(receipt) ? "结果待核对"
  : receipt?.operation_status === "completed" ? "操作已完成" : receipt?.operation_status === "failed" ? "操作未完成"
  : receipt?.operation_status === "interrupted" ? "执行中断，需核对库存" : "正在核对执行进度";
function stored(key: string): Tracked | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    if (value && ["pull", "remove", "lookup"].includes(value.action) && typeof value.image === "string" && value.image.length <= 1024 &&
      /^[A-Za-z0-9_.:-]{1,128}$/.test(value.node_id) && /^[A-Za-z0-9_.:-]{1,128}$/.test(value.operation_id)) return value;
  } catch { /* The operation remains durable on its Agent when browser storage is unavailable. */ }
  return null;
}

export default function ImageOperations({ nodes, canOperate, principal, removal, onRemovalClosed, onCompleted }: {
  nodes: NodeInfo[]; canOperate: boolean; principal: string; removal: ImageRemoval | null;
  onRemovalClosed: () => void; onCompleted: () => void;
}) {
  const storageKey = imageOperationStorageKey(principal);
  const [tracked, setTracked] = useState<Tracked | null>(() => stored(storageKey));
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState("");
  const [records, setRecords] = useState<OperationRecord[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyError, setHistoryError] = useState("");
  const [formError, setFormError] = useState("");
  const [pullOpen, setPullOpen] = useState(false);
  const [lookupOpen, setLookupOpen] = useState(false);
  const [lookupId, setLookupId] = useState("");
  const [nodeId, setNodeId] = useState("");
  const [image, setImage] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const completed = useRef("");
  const submittingRef = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!canOperate) { setPullOpen(false); setUsername(""); setPassword(""); onRemovalClosed(); }
  }, [canOperate]);
  const dockerNodes = nodes.filter((node) => node.docker_available !== false && !!node.docker_version);
  const writable = dockerNodes.filter((node) => node.status === "online" && !isReadOnlyAgent(node));
  const track = (value: Tracked) => {
    setTracked(value); setReceipt(null); setError("");
    try { localStorage.setItem(storageKey, JSON.stringify(value)); } catch { /* IDs can still be copied from the current task. */ }
  };
  const operation = usePollingQuery(`image-operation:${tracked?.node_id}:${tracked?.operation_id}`, async (signal) => {
    const value = await api<Receipt | { operation: Receipt }>(`/images/operations/${encodeURIComponent(tracked!.operation_id)}?node_id=${encodeURIComponent(tracked!.node_id)}`, { signal });
    return receiptFor(value, tracked!.operation_id);
  }, {
    enabled: !!tracked, intervalMs: terminal(receipt) ? 0 : uncertain(receipt) ? 10_000 : 2000,
    onSuccess: (value) => {
      if (value.operation_id !== tracked?.operation_id) { setError("回执操作标识不匹配，未展示。"); return; }
      setReceipt(value); setError("");
      if (value.operation_status === "completed" && completed.current !== value.operation_id) {
        completed.current = value.operation_id; onCompleted();
      }
    },
    onError: (failure) => setError(`回执暂未读取：${failure.message}。保留原操作标识核对，不会自动重发。`),
  });
  const history = usePollingQuery(`image-operation-history:${principal}:${historyPage}`,
    (signal) => api<{ items: OperationRecord[]; total: number }>(`/images/operations?limit=20&offset=${(historyPage - 1) * 20}`, { signal }), {
      intervalMs: 30_000, invalidateOn: ["/images/pull", "/images/remove"],
      onSuccess: (value) => {
        if (!Array.isArray(value.items) || value.items.some((item) => !item ||
          typeof item.operation_id !== "string" || typeof item.node_id !== "string" || typeof item.reference !== "string"))
          throw Error("镜像任务列表格式不正确，未覆盖已有记录。");
        if (!Number.isSafeInteger(value.total) || value.total < value.items.length) throw Error("镜像任务总数不正确，未覆盖已有记录。");
        setRecords(value.items); setHistoryTotal(value.total); setHistoryError("");
      }, onError: (failure) => setHistoryError(`任务记录暂未读取：${failure.message}。仍可凭原操作 ID 查询回执。`),
    });
  const trackRecord = (value: OperationRecord) => track({ node_id: value.node_id, operation_id: value.operation_id,
    image: value.reference, action: value.action === "image_remove" ? "remove" : "pull" });
  useEffect(() => {
    if (!tracked) {
      const pending = records.find((value) => !terminal(value));
      if (pending) trackRecord(pending);
    }
  }, [records, tracked]);
  const historyPages = Math.min(501, Math.max(1, Math.ceil(historyTotal / 20)));
  useEffect(() => setHistoryPage((page) => Math.min(page, historyPages)), [historyPages]);
  const submit = async (action: "pull" | "remove", target: string, reference: string) => {
    if (submittingRef.current) return;
    if (!canOperate) { setFormError("当前角色仅可查看镜像与回执，不能修改节点镜像。"); return; }
    const node = writable.find((candidate) => candidate.id === target);
    if (!node) { setFormError("请选择在线且已启用管理的 Docker Agent；只读节点不能修改镜像。"); return; }
    if (action === "pull" && (!reference.trim() || /\s/.test(reference) || reference.length > 256)) { setFormError("请填写完整镜像名称或摘要，不含空格，最多 256 个字符。"); return; }
    if (action === "remove" && !/^sha256:[0-9a-f]{64}$/.test(reference)) { setFormError("删除需要完整 sha256 镜像标识，请重新读取库存。"); return; }
    if (action === "pull" && (username || password) && (!username.trim() || !password)) {
      setFormError("私有仓库授权需要同时填写账号与密码或访问令牌；无需认证时请全部留空。"); return;
    }
    const pending = records.find((value) => value.node_id === target && value.reference === reference.trim() && value.action === `image_${action}` && !terminal(value));
    if (pending) {
      trackRecord(pending); setPullOpen(false); onRemovalClosed(); setUsername(""); setPassword("");
      setError("同一节点已有此镜像的未决操作，已打开原操作回执，未重复提交。"); return;
    }
    const operationId = createOperationKey("image");
    const intent: Tracked = { node_id: target, operation_id: operationId, image: reference.trim(), action };
    submittingRef.current = true;
    setSubmitting(true); setFormError("");
    // Persist only routing IDs before submission. Registry credentials never enter browser storage.
    track(intent);
    try {
      const value = await post<Receipt | { operation: Receipt }>(`/images/${action}`, {
        node_id: target, operation_id: operationId,
        ...(action === "pull" ? { image: reference.trim(), ...(username || password ? { registry_auth: { username, password } } : {}) } : { image_id: reference }),
      }, operationId);
      if (!mounted.current) return;
      try { setReceipt(receiptFor(value, operationId)); }
      catch (reason) { throw Object.assign(reason as Error, { outcomeUnknown: true }); }
      setPullOpen(false); onRemovalClosed(); setUsername(""); setPassword("");
      operation.invalidate();
    } catch (reason) {
      if (!mounted.current) return;
      const failure = reason as Error & { outcomeUnknown?: boolean; status?: number; detail?: { existing_operation_id?: unknown } };
      const existing = failure.detail?.existing_operation_id;
      if (failure.status === 409 && typeof existing === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(existing)) {
        track({ node_id: target, operation_id: existing, image: reference.trim(), action });
        setError("同一节点已有此镜像的未决任务，已恢复原操作标识，未再次下发到 Agent。");
        setPullOpen(false); onRemovalClosed(); setUsername(""); setPassword(""); return;
      }
      setReceipt({ operation_id: operationId, operation_status: failure.outcomeUnknown ? "running" : "failed", status: failure.outcomeUnknown ? "unknown" : "failed", message: failure.message });
      setError(failure.outcomeUnknown ? `提交结果尚未确认：${failure.message}。请按原操作标识查询回执。` : failure.message);
      if (!failure.outcomeUnknown) { setTracked(null); try { localStorage.removeItem(storageKey); } catch { /* No accepted operation is recorded. */ } }
      setPullOpen(false); onRemovalClosed(); setUsername(""); setPassword("");
    } finally { submittingRef.current = false; if (mounted.current) { setSubmitting(false); history.invalidate(); } }
  };
  return <div className="image-operations">
    <div className="settings-actions">
      {canOperate && <Button kind="primary" disabled={!writable.length || submitting || (!!tracked && !terminal(receipt))} title={!writable.length ? "需要在线且已启用管理的 Docker Agent" : undefined} onClick={() => { setNodeId(writable[0]?.id ?? ""); setFormError(""); setPullOpen(true); }}><Download size={14} />拉取镜像</Button>}
      <Button disabled={submitting || !dockerNodes.length} onClick={() => { setNodeId(dockerNodes[0]?.id ?? ""); setLookupId(""); setLookupOpen(true); }}>查询操作回执</Button>
    </div>
    {error && <ErrorBox message={error} />}
    {tracked && <div className="image-operation-status" aria-live="polite">
      <div><strong>{receipt?.action === "image_remove" || tracked.action === "remove" ? "定向删除镜像" : tracked.action === "lookup" && !receipt?.action ? "镜像操作" : "镜像拉取"}</strong><Badge status={uncertain(receipt) ? "warn" : receipt?.operation_status ?? "pending"}>{statusLabel(receipt)}</Badge></div>
      <p><code className="break-word">{knownText(receipt?.result?.image_reference) ? receipt!.result!.image_reference : tracked.image}</code> · {nodes.find((node) => node.id === tracked.node_id)?.name ?? tracked.node_id}</p>
      <p>操作 ID：<code>{tracked.operation_id}</code><Copy value={tracked.operation_id} /><Button kind="text" onClick={() => operation.invalidate()} loading={operation.refreshing}><RefreshCw size={13} />核对回执</Button></p>
      {receipt?.message && <p>{receipt.message}</p>}
      {receipt?.result?.image_id && <p>本地镜像：<code className="break-word">{receipt.result.image_id}</code><Copy value={receipt.result.image_id} /></p>}
      <small>镜像检查仅确认镜像可读取；部署前请核对原生版本与模型配置。操作结果待核实时，请先查询原操作回执，避免重复提交。</small>
    </div>}
    {pullOpen && <Modal title="拉取节点镜像" onClose={() => { if (!submitting) { setPullOpen(false); setUsername(""); setPassword(""); } }} closeDisabled={submitting} footer={<><Button disabled={submitting} onClick={() => { setPullOpen(false); setUsername(""); setPassword(""); }}>取消</Button><Button kind="primary" loading={submitting} onClick={() => void submit("pull", nodeId, image)}>提交拉取</Button></>}>
      {formError && <ErrorBox message={formError} />}
      <Field label="目标 Docker Agent" required><Select value={nodeId} onChange={setNodeId} disabled={submitting}>{writable.map((node) => <option key={node.id} value={node.id}>{node.name} · {node.address}</option>)}</Select></Field>
      <Field label="镜像名称或摘要" required><input value={image} maxLength={256} onChange={(event) => setImage(event.target.value)} placeholder="registry.example.test/inference/motor:release" disabled={submitting} /></Field>
      <details><summary>私有仓库授权（可选）</summary><Field label="仓库账号"><input value={username} maxLength={256} autoComplete="off" disabled={submitting} onChange={(event) => setUsername(event.target.value)} /></Field><Field label="仓库密码或访问令牌"><input type="password" maxLength={4096} data-private="true" autoComplete="new-password" disabled={submitting} value={password} onChange={(event) => setPassword(event.target.value)} /></Field><p className="small-caption">仅发送到本次拉取，凭据不写浏览器存储或回执。</p></details>
      <InfoBox>只操作所选节点，拉取在 Agent 后台执行。关闭窗口或离开页面不会取消已受理任务；本页可凭操作标识继续核对。</InfoBox>
    </Modal>}
    {removal && <Confirm title="删除此节点镜像？" busy={submitting} error={formError} confirmText="删除镜像" onClose={() => { onRemovalClosed(); setFormError(""); }} onConfirm={() => void submit("remove", removal.node_id, removal.id)} description={<><p>{removal.node_name} · {removal.tags[0] ?? "未标记镜像"}</p><code className="break-word">{removal.id}</code><p>仅删除此节点的指定镜像。任何容器（包括已停止容器）仍引用时，后端会拒绝；不强制删除、不清理其他镜像。</p></>} />}
    {lookupOpen && <Modal title="查询镜像操作回执" onClose={() => setLookupOpen(false)} footer={<><Button onClick={() => setLookupOpen(false)}>取消</Button><Button kind="primary" disabled={!nodeId || !/^[A-Za-z0-9_.:-]{1,128}$/.test(lookupId)} onClick={() => { track({ node_id: nodeId, operation_id: lookupId, image: "按操作标识查询", action: "lookup" }); setLookupOpen(false); }}>读取回执</Button></>}>
      <Field label="Docker Agent"><Select value={nodeId} onChange={setNodeId}>{dockerNodes.map((node) => <option key={node.id} value={node.id}>{node.name}</option>)}</Select></Field>
      <Field label="操作 ID"><input value={lookupId} onChange={(event) => setLookupId(event.target.value.trim())} placeholder="image_…" /></Field>
    </Modal>}
    <details className="image-task-history">
      <summary>镜像任务记录{historyTotal > 0 ? ` · ${historyTotal}` : ""}</summary>
      <div className="image-task-history-actions"><span className="small-caption">任务保存在控制数据库；跨浏览器或管理员可按原操作标识继续核对。</span><Button loading={history.refreshing} onClick={() => void history.refresh()}><RefreshCw size={13} />刷新任务</Button></div>
      {historyError && <ErrorBox message={historyError} />}
      {history.hasSuccess && records.length > 0 ? <div className="table-wrap"><table><thead><tr><th>目标 / 操作标识</th><th>状态</th><th>操作</th></tr></thead><tbody>{records.map((record) => <tr key={record.operation_id}>
        <td><strong>{nodes.find((node) => node.id === record.node_id)?.name ?? record.node_id}</strong><span className="cell-secondary break-word">{record.reference}</span><code className="cell-secondary">{record.operation_id}</code><Copy value={record.operation_id} /></td>
        <td><Badge status={uncertain(record) ? "warn" : record.operation_status}>{statusLabel(record)}</Badge>{record.updated_at && <span className="cell-secondary">{when(record.updated_at)}</span>}</td>
        <td><Button disabled={submitting} onClick={() => trackRecord(record)}>核对任务</Button></td>
      </tr>)}</tbody></table></div> : !historyError && <p className="small-caption" role="status">{history.hasSuccess ? "尚无镜像任务记录。" : "正在读取镜像任务记录…"}</p>}
      {historyTotal > 20 && <div className="table-footer"><span>共 {historyTotal} 项 · 每页 20 项</span><Pagination page={historyPage} totalPages={historyPages} onChange={setHistoryPage} ariaLabel="镜像任务分页" /></div>}
      {historyTotal > 10_020 && <p className="small-caption">分页展示最近 10,020 项，更早记录可凭原操作 ID 查询。</p>}
    </details>
    <details className="settings-technical-detail"><summary>离线导入方式</summary><p>大型离线镜像由部署人员在目标宿主机使用 Docker CLI 导入，导入后刷新本页核对镜像 ID 与架构；此页面不上传多 GiB 镜像归档。</p><pre className="json-view">docker load --input /path/to/reviewed-image.tar</pre></details>
  </div>;
}
