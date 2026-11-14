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

export default function JobProgress({
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
          {canAdminister && auditEnabled && <Button onClick={onAudit}>查看操作记录</Button>}
          <Button kind="primary" onClick={onClose}>
            关闭
          </Button>
        </>
      }
    >
      <div className="job-summary">
        <div className={`job-symbol ${job.status}`}>
          {["queued", "running", "pending", "deploying"].includes(job.status) ? (
            <RefreshCw className="spin" size={27} />
          ) : ["completed", "success", "succeeded"].includes(job.status) ? (
            <CheckCircle2 size={27} />
          ) : (
            <AlertCircle size={27} />
          )}
        </div>
        <div>
          <h3>{statusName(job.status)}</h3>
          <p>按节点与角色跟踪实际执行结果</p>
        </div>
        <Badge status={job.status} />
      </div>
      {job.error && <ErrorBox message={job.error} />}
      {error && <ErrorBox message={error} />}
      <div className="job-steps">
        {executionSteps.map((step, index) => (
          <div key={index}>
            <span className={`job-step-dot ${step.status}`}>
              {["completed", "success", "succeeded"].includes(step.status) ? (
                <Check size={12} />
              ) : (
                index + 1
              )}
            </span>
            <div>
              <b>
                {nodes.find((node) => node.id === step.node_id)?.name ??
                  step.node_id}
                <span>{step.role === "cleanup" ? "重建前清理" : roleName(step.role)}</span>
              </b>
              <p>{stepMessage(step)}</p>
              {step.operation_id && <small className="muted-text">操作标识 <code>{step.operation_id}</code> · {operationStatusName(step.operation_status)}</small>}
            </div>
            <Badge status={step.status} />
          </div>
        ))}
      </div>
      {(executionSteps.some(step => step.operation_id) || job.receipt_observations) && (
        <section className="job-reconciliation" aria-label="Agent 持久回执">
          <div className="reconciliation-heading"><h3>Agent 持久回执</h3>
            {canOperate && !demo && <Button loading={busy} onClick={() => void queryReceipts()}><RefreshCw size={14} />查询回执</Button>}
          </div>
          <InfoBox>仅查询原操作，不会重放。Agent 操作完成后仍需验证模型加载、服务就绪和真实推理。</InfoBox>
          {job.receipt_observations && <>
            <p className="muted-text">最近查询 {when(job.receipt_observations.observed_at)} · 当前第 {job.receipt_observations.offset + 1}–{job.receipt_observations.offset + job.receipt_observations.items.length} 项 / 共 {job.receipt_observations.target_count} 项</p>
            <ul className="reconciliation-resource-list">{job.receipt_observations.items.map(item => <li key={item.operation_id}><code>{item.operation_id}</code><span>{operationStatusName(item.operation_status)}{item.error && ` · ${item.error}`}</span></li>)}</ul>
            {canOperate && !demo && job.receipt_observations.next_offset !== null && <Button loading={busy} onClick={() => void queryReceipts(job.receipt_observations!.next_offset!)}>查询下一页回执</Button>}
          </>}
        </section>
      )}
      {(recoverable || job.reconciliation) && (
        <section className="job-reconciliation" aria-label="任务资源对账">
          <div className="reconciliation-heading">
            <h3>资源对账</h3>
            {canOperate && recoverable && !demo && (
              <Button loading={busy} onClick={() => void reconcile()}>
                <RefreshCw size={14} />
                {job.reconciliation ? "重新对账" : "检查实际资源"}
              </Button>
            )}
          </div>
          <InfoBox>
            对账只读取当前受管容器与 Kubernetes
            资源，不会重放任务或修改服务状态。资源存在、Pod
            就绪仅说明运行状态；请通过实际推理请求确认模型服务可用。
            {demo && "演示环境不连接真实节点。"}
          </InfoBox>
          {job.reconciliation ? (
            <>
              <div className="reconciliation-caption">
                已检查 {job.reconciliation.checked_count} /{" "}
                {job.reconciliation.target_count} 个执行节点
                <span>最近观测 {when(job.reconciliation.observed_at)}</span>
              </div>
              <div className="reconciliation-nodes">
                {job.reconciliation.nodes.map((node) => (
                  <article key={node.node_id}>
                    <div className="reconciliation-node-heading">
                      <b>{node.node_name || node.node_id}</b>
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
                    </div>
                    <p className="muted-text">
                      {node.runtime === "kubernetes" ? "Kubernetes" : "Docker"}{" "}
                      · {when(node.observed_at)}
                    </p>
                    {node.error && <ErrorBox message={node.error} />}
                    {node.state && (
                      <>
                        {node.state.namespace && (
                          <p>
                            命名空间 <code>{node.state.namespace}</code>
                          </p>
                        )}
                        {node.state.resources && (
                          <p>
                            已核验 {node.state.resources.length} 个资源身份；
                            {node.state.items.length} 个 Pod
                          </p>
                        )}
                        <ul className="reconciliation-resource-list">
                          {node.state.items.map((item, index) => (
                            <li key={String(item.id ?? item.uid ?? index)}>
                              <code>
                                {String(item.name ?? item.id ?? "未命名资源")}
                              </code>
                              <span>
                                {String(
                                  item.status ?? item.phase ?? "未知状态",
                                )}
                                {item.role ? ` · ${item.role}` : ""}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </>
                    )}
                  </article>
                ))}
              </div>
              {canOperate &&
                recoverable &&
                !demo &&
                job.reconciliation.next_offset !== null && (
                  <Button
                    loading={busy}
                    onClick={() =>
                      void reconcile(job.reconciliation!.next_offset!)
                    }
                  >
                    继续检查剩余节点
                  </Button>
                )}
            </>
          ) : (
            <p className="muted-text">尚未执行资源对账。</p>
          )}
        </section>
      )}
      <div className="subtle-note">关闭窗口不会中断已经提交的后台任务。</div>
    </Modal>
  );
}
