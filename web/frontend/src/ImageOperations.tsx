import { UiElement, withUiSlot, UiText } from "./uiPluginSdk";
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

function DefaultImageOperations({ nodes, canOperate, principal, removal, onRemovalClosed, onCompleted }: {
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
  return <UiElement as="div" className="image-operations">
    <UiElement as="div" className="settings-actions">
      {canOperate && <Button actionId="action.ImageOperations.001" kind="primary" disabled={!writable.length || submitting || (!!tracked && !terminal(receipt))} title={!writable.length ? "需要在线且已启用管理的 Docker Agent" : undefined} onClick={() => { setNodeId(writable[0]?.id ?? ""); setFormError(""); setPullOpen(true); }}><Download size={14} /><UiText>{"拉取镜像"}</UiText></Button>}
      <Button actionId="action.ImageOperations.002" disabled={submitting || !dockerNodes.length} onClick={() => { setNodeId(dockerNodes[0]?.id ?? ""); setLookupId(""); setLookupOpen(true); }}><UiText>{"查询操作回执"}</UiText></Button>
    </UiElement>
    {error && <ErrorBox message={error} />}
    {tracked && <UiElement as="div" className="image-operation-status" aria-live="polite">
      <UiElement as="div"><UiElement as="strong">{receipt?.action === "image_remove" || tracked.action === "remove" ? "定向删除镜像" : tracked.action === "lookup" && !receipt?.action ? "镜像操作" : "镜像拉取"}</UiElement><Badge status={uncertain(receipt) ? "warn" : receipt?.operation_status ?? "pending"}>{statusLabel(receipt)}</Badge></UiElement>
      <UiElement as="p"><UiElement as="code" className="break-word">{knownText(receipt?.result?.image_reference) ? receipt!.result!.image_reference : tracked.image}</UiElement> <UiText>{"·"}</UiText> {nodes.find((node) => node.id === tracked.node_id)?.name ?? tracked.node_id}</UiElement>
      <UiElement as="p"><UiText>{"操作 ID："}</UiText><UiElement as="code">{tracked.operation_id}</UiElement><Copy value={tracked.operation_id} /><Button actionId="action.ImageOperations.003" kind="text" onClick={() => operation.invalidate()} loading={operation.refreshing}><RefreshCw size={13} /><UiText>{"核对回执"}</UiText></Button></UiElement>
      {receipt?.message && <UiElement as="p">{receipt.message}</UiElement>}
      {receipt?.result?.image_id && <UiElement as="p"><UiText>{"本地镜像："}</UiText><UiElement as="code" className="break-word">{receipt.result.image_id}</UiElement><Copy value={receipt.result.image_id} /></UiElement>}
      <UiElement as="small"><UiText>{"镜像检查仅确认镜像可读取；部署前请核对原生版本与模型配置。操作结果待核实时，请先查询原操作回执，避免重复提交。"}</UiText></UiElement>
    </UiElement>}
    {pullOpen && <Modal title="拉取节点镜像" onClose={() => { if (!submitting) { setPullOpen(false); setUsername(""); setPassword(""); } }} closeDisabled={submitting} footer={<><Button actionId="action.ImageOperations.004" disabled={submitting} onClick={() => { setPullOpen(false); setUsername(""); setPassword(""); }}><UiText>{"取消"}</UiText></Button><Button actionId="action.ImageOperations.005" kind="primary" loading={submitting} onClick={() => void submit("pull", nodeId, image)}><UiText>{"提交拉取"}</UiText></Button></>}>
      {formError && <ErrorBox message={formError} />}
      <Field label="目标 Docker Agent" required><Select value={nodeId} onChange={setNodeId} disabled={submitting}>{writable.map((node) => <UiElement as="option" key={node.id} value={node.id}>{node.name} <UiText>{"·"}</UiText> {node.address}</UiElement>)}</Select></Field>
      <Field label="镜像名称或摘要" required><UiElement as="input" value={image} maxLength={256} onChange={(event) => setImage(event.target.value)} placeholder="registry.example.test/inference/motor:release" disabled={submitting} /></Field>
      <UiElement as="details"><UiElement as="summary"><UiText>{"私有仓库授权（可选）"}</UiText></UiElement><Field label="仓库账号"><UiElement as="input" value={username} maxLength={256} autoComplete="off" disabled={submitting} onChange={(event) => setUsername(event.target.value)} /></Field><Field label="仓库密码或访问令牌"><UiElement as="input" type="password" maxLength={4096} data-private="true" autoComplete="new-password" disabled={submitting} value={password} onChange={(event) => setPassword(event.target.value)} /></Field><UiElement as="p" className="small-caption"><UiText>{"仅发送到本次拉取，凭据不写浏览器存储或回执。"}</UiText></UiElement></UiElement>
      <InfoBox><UiText>{"只操作所选节点，拉取在 Agent 后台执行。关闭窗口或离开页面不会取消已受理任务；本页可凭操作标识继续核对。"}</UiText></InfoBox>
    </Modal>}
    {removal && <Confirm title="删除此节点镜像？" busy={submitting} error={formError} confirmText="删除镜像" onClose={() => { onRemovalClosed(); setFormError(""); }} onConfirm={() => void submit("remove", removal.node_id, removal.id)} description={<><UiElement as="p">{removal.node_name} <UiText>{"·"}</UiText> {removal.tags[0] ?? "未标记镜像"}</UiElement><UiElement as="code" className="break-word">{removal.id}</UiElement><UiElement as="p"><UiText>{"仅删除此节点的指定镜像。任何容器（包括已停止容器）仍引用时，后端会拒绝；不强制删除、不清理其他镜像。"}</UiText></UiElement></>} />}
    {lookupOpen && <Modal title="查询镜像操作回执" onClose={() => setLookupOpen(false)} footer={<><Button actionId="action.ImageOperations.006" onClick={() => setLookupOpen(false)}><UiText>{"取消"}</UiText></Button><Button actionId="action.ImageOperations.007" kind="primary" disabled={!nodeId || !/^[A-Za-z0-9_.:-]{1,128}$/.test(lookupId)} onClick={() => { track({ node_id: nodeId, operation_id: lookupId, image: "按操作标识查询", action: "lookup" }); setLookupOpen(false); }}><UiText>{"读取回执"}</UiText></Button></>}>
      <Field label="Docker Agent"><Select value={nodeId} onChange={setNodeId}>{dockerNodes.map((node) => <UiElement as="option" key={node.id} value={node.id}>{node.name}</UiElement>)}</Select></Field>
      <Field label="操作 ID"><UiElement as="input" value={lookupId} onChange={(event) => setLookupId(event.target.value.trim())} placeholder="image_…" /></Field>
    </Modal>}
    <UiElement as="details" className="image-task-history">
      <UiElement as="summary"><UiText>{"镜像任务记录"}</UiText>{historyTotal > 0 ? ` · ${historyTotal}` : ""}</UiElement>
      <UiElement as="div" className="image-task-history-actions"><UiElement as="span" className="small-caption"><UiText>{"任务保存在控制数据库；跨浏览器或管理员可按原操作标识继续核对。"}</UiText></UiElement><Button actionId="action.ImageOperations.008" loading={history.refreshing} onClick={() => void history.refresh()}><RefreshCw size={13} /><UiText>{"刷新任务"}</UiText></Button></UiElement>
      {historyError && <ErrorBox message={historyError} />}
      {history.hasSuccess && records.length > 0 ? <UiElement as="div" className="table-wrap"><UiElement as="table"><UiElement as="thead"><UiElement as="tr"><UiElement as="th"><UiText>{"目标 / 操作标识"}</UiText></UiElement><UiElement as="th"><UiText>{"状态"}</UiText></UiElement><UiElement as="th"><UiText>{"操作"}</UiText></UiElement></UiElement></UiElement><UiElement as="tbody">{records.map((record) => <UiElement as="tr" key={record.operation_id}>
        <UiElement as="td"><UiElement as="strong">{nodes.find((node) => node.id === record.node_id)?.name ?? record.node_id}</UiElement><UiElement as="span" className="cell-secondary break-word">{record.reference}</UiElement><UiElement as="code" className="cell-secondary">{record.operation_id}</UiElement><Copy value={record.operation_id} /></UiElement>
        <UiElement as="td"><Badge status={uncertain(record) ? "warn" : record.operation_status}>{statusLabel(record)}</Badge>{record.updated_at && <UiElement as="span" className="cell-secondary">{when(record.updated_at)}</UiElement>}</UiElement>
        <UiElement as="td"><Button actionId="action.ImageOperations.009" disabled={submitting} onClick={() => trackRecord(record)}><UiText>{"核对任务"}</UiText></Button></UiElement>
      </UiElement>)}</UiElement></UiElement></UiElement> : !historyError && <UiElement as="p" className="small-caption" role="status">{history.hasSuccess ? "尚无镜像任务记录。" : "正在读取镜像任务记录…"}</UiElement>}
      {historyTotal > 20 && <UiElement as="div" className="table-footer"><UiElement as="span"><UiText>{"共"}</UiText> {historyTotal} <UiText>{"项 · 每页 20 项"}</UiText></UiElement><Pagination page={historyPage} totalPages={historyPages} onChange={setHistoryPage} ariaLabel="镜像任务分页" /></UiElement>}
      {historyTotal > 10_020 && <UiElement as="p" className="small-caption"><UiText>{"分页展示最近 10,020 项，更早记录可凭原操作 ID 查询。"}</UiText></UiElement>}
    </UiElement>
    <UiElement as="details" className="settings-technical-detail"><UiElement as="summary"><UiText>{"离线导入方式"}</UiText></UiElement><UiElement as="p"><UiText>{"大型离线镜像由部署人员在目标宿主机使用 Docker CLI 导入，导入后刷新本页核对镜像 ID 与架构；此页面不上传多 GiB 镜像归档。"}</UiText></UiElement><UiElement as="pre" className="json-view"><UiText>{"docker load --input /path/to/reviewed-image.tar"}</UiText></UiElement></UiElement>
  </UiElement>;
}

const ImageOperations = withUiSlot("component.ImageOperations", DefaultImageOperations);
export default ImageOperations;

// Reference package implementation exports.
export { DefaultImageOperations };
