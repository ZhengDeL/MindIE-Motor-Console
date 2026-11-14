import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { useEffect, useRef, useState } from "react";
import { AlertCircle, Check, CheckCircle2, RefreshCw } from "./icons";
import { post, roleName, statusName, when } from "./api";
import type { AgentReceiptObservations, Job, JobReconciliation, JobStep, NodeInfo } from "./api";
import { Badge, Button, ErrorBox, InfoBox, Modal } from "./ui";
import { usePluginEnabled } from "./consolePlugins";
const operationStatusName = (status?: string) => ({ accepted: "已受理", submitting: "提交中", running: "执行中", completed: "节点操作完成", failed: "节点操作失败", interrupted: "执行中断" }[status || ""] ?? "结果未知");

function stepMessage(step: JobStep): string {
  if (typeof step.message === "string" && step.message.trim()) return step.message;
  switch (step.status) {
    case "completed":
    case "success":
    case "succeeded":
      return "节点操作已完成";
    case "failed":
    case "error":
      return "节点操作失败";
    case "cancelled":
    case "canceled":
      return "节点操作已取消";
    case "interrupted":
      return "节点操作已中断";
    case "running":
    case "deploying":
      return "节点操作执行中";
    case "queued":
    case "pending":
      return "等待节点执行";
    default:
      return "尚未取得明确的执行结果";
  }
}

function DefaultJobProgress({
  job,
  nodes,
  canOperate,
  canAdminister,
  demo,
  onUpdate,
  onClose,
  onAudit,
}: {
  job: Job;
  nodes: NodeInfo[];
  canOperate: boolean;
  canAdminister: boolean;
  demo: boolean;
  onUpdate: (job: Job) => void;
  onClose: () => void;
  onAudit: () => void;
}) {
  const auditEnabled = usePluginEnabled("audit");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  const latestJob = useRef(job);
  latestJob.current = job;
  useEffect(() => {
    active.current = true;
    setBusy(false);
    setError("");
    return () => {
      active.current = false;
    };
  }, [job.id]);
  const recoverable = ["failed", "interrupted"].includes(job.status);
  const executionSteps = [...(job.cleanup_steps ?? []), ...(job.steps ?? [])];
  const queryReceipts = async (offset = 0) => {
    const identifier = job.id;
    setBusy(true);
    setError("");
    try {
      const receipt_observations = await post<AgentReceiptObservations>(`/jobs/${encodeURIComponent(job.id)}/receipts?offset=${offset}`);
      if (active.current && latestJob.current.id === identifier) onUpdate({ ...latestJob.current, receipt_observations });
    } catch (error) {
      if (active.current && latestJob.current.id === identifier) setError((error as Error).message);
    } finally { if (active.current && latestJob.current.id === identifier) setBusy(false); }
  };
  const reconcile = async (offset = 0) => {
    const identifier = job.id;
    setBusy(true);
    setError("");
    try {
      const reconciliation = await post<JobReconciliation>(
        `/jobs/${encodeURIComponent(job.id)}/reconcile?offset=${offset}&limit=16`,
      );
      if (active.current && latestJob.current.id === identifier) onUpdate({ ...latestJob.current, reconciliation });
    } catch (error) {
      if (active.current && latestJob.current.id === identifier) setError((error as Error).message);
    } finally {
      if (active.current && latestJob.current.id === identifier) setBusy(false);
    }
  };
  return (
    <Modal
      title="部署与操作进度"
      subtitle={`任务 ${job.id}`}
      onClose={onClose}
      footer={
        <>
          {canAdminister && auditEnabled && <Button actionId="action.JobProgress.001" onClick={onAudit}><UiText>{"查看操作记录"}</UiText></Button>}
          <Button actionId="action.JobProgress.002" kind="primary" onClick={onClose}>
            <UiText>{"关闭"}</UiText>
          </Button>
        </>
      }
    >
      <UiElement as="div" className="job-summary">
        <UiElement as="div" className={`job-symbol ${job.status}`}>
          {["queued", "running", "pending", "deploying"].includes(job.status) ? (
            <RefreshCw className="spin" size={27} />
          ) : ["completed", "success", "succeeded"].includes(job.status) ? (
            <CheckCircle2 size={27} />
          ) : (
            <AlertCircle size={27} />
          )}
        </UiElement>
        <UiElement as="div">
          <UiElement as="h3">{statusName(job.status)}</UiElement>
          <UiElement as="p"><UiText>{"按节点与角色跟踪实际执行结果"}</UiText></UiElement>
        </UiElement>
        <Badge status={job.status} />
      </UiElement>
      {job.error && <ErrorBox message={job.error} />}
      {error && <ErrorBox message={error} />}
      <UiElement as="div" className="job-steps">
        {executionSteps.map((step, index) => (
          <UiElement as="div" key={index}>
            <UiElement as="span" className={`job-step-dot ${step.status}`}>
              {["completed", "success", "succeeded"].includes(step.status) ? (
                <Check size={12} />
              ) : (
                index + 1
              )}
            </UiElement>
            <UiElement as="div">
              <UiElement as="b">
                {nodes.find((node) => node.id === step.node_id)?.name ??
                  step.node_id}
                <UiElement as="span">{step.role === "cleanup" ? "重建前清理" : roleName(step.role)}</UiElement>
              </UiElement>
              <UiElement as="p">{stepMessage(step)}</UiElement>
              {step.operation_id && <UiElement as="small" className="muted-text"><UiText>{"操作标识"}</UiText> <UiElement as="code">{step.operation_id}</UiElement> <UiText>{"·"}</UiText> {operationStatusName(step.operation_status)}</UiElement>}
            </UiElement>
            <Badge status={step.status} />
          </UiElement>
        ))}
      </UiElement>
      {(executionSteps.some(step => step.operation_id) || job.receipt_observations) && (
        <UiRegion name="layout.panel" as="section" className="job-reconciliation" aria-label="Agent 持久回执">
          <UiElement as="div" className="reconciliation-heading"><UiElement as="h3"><UiText>{"Agent 持久回执"}</UiText></UiElement>
            {canOperate && !demo && <Button actionId="action.JobProgress.003" loading={busy} onClick={() => void queryReceipts()}><RefreshCw size={14} /><UiText>{"查询回执"}</UiText></Button>}
          </UiElement>
          <InfoBox><UiText>{"仅查询原操作，不会重放。Agent 操作完成后仍需验证模型加载、服务就绪和真实推理。"}</UiText></InfoBox>
          {job.receipt_observations && <>
            <UiElement as="p" className="muted-text"><UiText>{"最近查询"}</UiText> {when(job.receipt_observations.observed_at)} <UiText>{"· 当前第"}</UiText> {job.receipt_observations.offset + 1}<UiText>{"–"}</UiText>{job.receipt_observations.offset + job.receipt_observations.items.length} <UiText>{"项 / 共"}</UiText> {job.receipt_observations.target_count} <UiText>{"项"}</UiText></UiElement>
            <UiElement as="ul" className="reconciliation-resource-list">{job.receipt_observations.items.map(item => <UiElement as="li" key={item.operation_id}><UiElement as="code">{item.operation_id}</UiElement><UiElement as="span">{operationStatusName(item.operation_status)}{item.error && ` · ${item.error}`}</UiElement></UiElement>)}</UiElement>
            {canOperate && !demo && job.receipt_observations.next_offset !== null && <Button actionId="action.JobProgress.004" loading={busy} onClick={() => void queryReceipts(job.receipt_observations!.next_offset!)}><UiText>{"查询下一页回执"}</UiText></Button>}
          </>}
        </UiRegion>
      )}
      {(recoverable || job.reconciliation) && (
        <UiRegion name="layout.panel" as="section" className="job-reconciliation" aria-label="任务资源对账">
          <UiElement as="div" className="reconciliation-heading">
            <UiElement as="h3"><UiText>{"资源对账"}</UiText></UiElement>
            {canOperate && recoverable && !demo && (
              <Button actionId="action.JobProgress.005" loading={busy} onClick={() => void reconcile()}>
                <RefreshCw size={14} />
                {job.reconciliation ? "重新对账" : "检查实际资源"}
              </Button>
            )}
          </UiElement>
          <InfoBox>
            <UiText>{"对账只读取当前受管容器与 Kubernetes 资源，不会重放任务或修改服务状态。资源存在、Pod 就绪仅说明运行状态；请通过实际推理请求确认模型服务可用。"}</UiText>
            {demo && "演示环境不连接真实节点。"}
          </InfoBox>
          {job.reconciliation ? (
            <>
              <UiElement as="div" className="reconciliation-caption">
                <UiText>{"已检查"}</UiText> {job.reconciliation.checked_count} <UiText>{"/"}</UiText>{" "}
                {job.reconciliation.target_count} <UiText>{"个执行节点"}</UiText>
                <UiElement as="span"><UiText>{"最近观测"}</UiText> {when(job.reconciliation.observed_at)}</UiElement>
              </UiElement>
              <UiElement as="div" className="reconciliation-nodes">
                {job.reconciliation.nodes.map((node) => (
                  <UiElement as="article" key={node.node_id}>
                    <UiElement as="div" className="reconciliation-node-heading">
                      <UiElement as="b">{node.node_name || node.node_id}</UiElement>
                      <Badge
                        status={
                          node.result === "unknown"
                            ? "unknown"
                            : node.result === "present"
                              ? "starting"
                              : "stopped"
                        }
                      >
                        {node.result === "present"
                          ? "发现受管资源"
                          : node.result === "absent"
                            ? "未发现受管资源"
                            : "未能确认"}
                      </Badge>
                    </UiElement>
                    <UiElement as="p" className="muted-text">
                      {node.runtime === "kubernetes" ? "Kubernetes" : "Docker"}{" "}
                      <UiText>{"·"}</UiText> {when(node.observed_at)}
                    </UiElement>
                    {node.error && <ErrorBox message={node.error} />}
                    {node.state && (
                      <>
                        {node.state.namespace && (
                          <UiElement as="p">
                            <UiText>{"命名空间"}</UiText> <UiElement as="code">{node.state.namespace}</UiElement>
                          </UiElement>
                        )}
                        {node.state.resources && (
                          <UiElement as="p">
                            <UiText>{"已核验"}</UiText> {node.state.resources.length} <UiText>{"个资源身份；"}</UiText>
                            {node.state.items.length} <UiText>{"个 Pod"}</UiText>
                          </UiElement>
                        )}
                        <UiElement as="ul" className="reconciliation-resource-list">
                          {node.state.items.map((item, index) => (
                            <UiElement as="li" key={String(item.id ?? item.uid ?? index)}>
                              <UiElement as="code">
                                {String(item.name ?? item.id ?? "未命名资源")}
                              </UiElement>
                              <UiElement as="span">
                                {String(
                                  item.status ?? item.phase ?? "未知状态",
                                )}
                                {item.role ? ` · ${item.role}` : ""}
                              </UiElement>
                            </UiElement>
                          ))}
                        </UiElement>
                      </>
                    )}
                  </UiElement>
                ))}
              </UiElement>
              {canOperate &&
                recoverable &&
                !demo &&
                job.reconciliation.next_offset !== null && (
                  <Button actionId="action.JobProgress.006"
                    loading={busy}
                    onClick={() =>
                      void reconcile(job.reconciliation!.next_offset!)
                    }
                  >
                    <UiText>{"继续检查剩余节点"}</UiText>
                  </Button>
                )}
            </>
          ) : (
            <UiElement as="p" className="muted-text"><UiText>{"尚未执行资源对账。"}</UiText></UiElement>
          )}
        </UiRegion>
      )}
      <UiElement as="div" className="subtle-note"><UiText>{"关闭窗口不会中断已经提交的后台任务。"}</UiText></UiElement>
    </Modal>
  );
}

const JobProgress = withUiSlot("component.JobProgress", DefaultJobProgress);
export default JobProgress;

// Reference package implementation exports.
export { DefaultJobProgress };
