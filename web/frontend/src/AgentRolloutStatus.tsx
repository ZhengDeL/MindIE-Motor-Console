import { UiElement, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import type { ReactNode } from "react";
import { AlertCircle, Check, Loader2, RefreshCw } from "./icons";
import { when } from "./api";
import { Badge, Button, Empty, Field, InfoBox, Spinner } from "./ui";
import { agentRolloutStatus } from "./agentRollout";
import type {
  AgentRolloutJob,
  AgentRolloutPlan,
  AgentRolloutSpec,
  AgentSSHConnection,
} from "./agentRollout";
const badgeStatus = (job: AgentRolloutJob) =>
  job.status === "completed"
    ? job.connection_verified
      ? "online"
      : "warning"
    : ["failed", "interrupted"].includes(job.status)
      ? "error"
      : job.status === "installed_unconnected"
        ? "warning"
        : job.status;

function DefaultPlanReview({
  spec,
  connection,
  plan,
  expired,
  step,
  confirmation,
  setConfirmation,
  approved,
  setApproved,
}: {
  spec: AgentRolloutSpec;
  connection: AgentSSHConnection;
  plan: AgentRolloutPlan | null;
  expired: boolean;
  step: number;
  confirmation: string;
  setConfirmation: (value: string) => void;
  approved: boolean;
  setApproved: (value: boolean) => void;
}) {
  return (
    <UiElement as="div" className="rollout-section">
      <UiElement as="dl" className="rollout-summary">
        <UiElement as="dt"><UiText>{"目标"}</UiText></UiElement>
        <UiElement as="dd">
          {spec.name} <UiText>{"·"}</UiText> {spec.address} <UiText>{"·"}</UiText>{" "}
          {spec.runtime === "docker" ? "Docker" : "Kubernetes"}
        </UiElement>
        <UiElement as="dt"><UiText>{"安装标识"}</UiText></UiElement>
        <UiElement as="dd">
          <UiElement as="code">{spec.installation_name}</UiElement>
        </UiElement>
        <UiElement as="dt"><UiText>{"SSH 身份"}</UiText></UiElement>
        <UiElement as="dd">
          {connection.username}<UiText>{"@"}</UiText>{connection.host}<UiText>{":"}</UiText>{connection.port} <UiText>{"·"}</UiText>{" "}
          {connection.privilege === "root" ? "root" : "sudo -n"}
        </UiElement>
        <UiElement as="dt"><UiText>{"Agent 地址"}</UiText></UiElement>
        <UiElement as="dd">
          <UiElement as="code">{spec.agent_url}</UiElement>
        </UiElement>
        <UiElement as="dt"><UiText>{"资源上限"}</UiText></UiElement>
        <UiElement as="dd">
          {spec.cpus} <UiText>{"核 ·"}</UiText> {spec.memory} <UiText>{"内存 ·"}</UiText> {spec.pids} <UiText>{"进程"}</UiText>
        </UiElement>
      </UiElement>
      {!plan ? (
        <InfoBox>
          <UiText>{"预检只检查 SSH 身份、运行时、端口、镜像与已存在安装，不创建 Agent 容器。点击“执行预检”生成有时效的安装计划。"}</UiText>
        </InfoBox>
      ) : (
        <>
          <UiElement as="div" className="rollout-plan-heading">
            <UiElement as="h3"><UiText>{"预检结果"}</UiText></UiElement>
            <Badge
              status={plan.can_execute && !expired ? "success" : "warning"}
            >
              {expired
                ? "计划已过期"
                : plan.can_execute
                  ? "可以安装"
                  : "已阻止安装"}
            </Badge>
          </UiElement>
          <UiElement as="div" className="check-list">
            {plan.checks.map((check) => (
              <UiElement as="div" key={check.id}>
                <Badge
                  status={check.status === "passed" ? "success" : check.status}
                >
                  {check.status === "passed"
                    ? "通过"
                    : check.status === "warning"
                      ? "需关注"
                      : "失败"}
                </Badge>
                <UiElement as="div">
                  <UiElement as="b">{check.title}</UiElement>
                  <UiElement as="p">{check.message}</UiElement>
                </UiElement>
              </UiElement>
            ))}
          </UiElement>
          {plan.existing_installation.length > 0 && (
            <UiElement as="div" className="rollout-existing">
              <InfoBox>
                <UiText>{"发现已有 Agent，禁止覆盖。请检查原安装或通过“接入节点”连接，勿更换标识绕过预检。"}</UiText>
              </InfoBox>
              <UiElement as="ul">
                {plan.existing_installation.map((item) => (
                  <UiElement as="li" key={item.container_id}>
                    <UiElement as="b">{item.name}</UiElement> <UiText>{"·"}</UiText> {item.runtime} <UiText>{"·"}</UiText> {item.state}
                  </UiElement>
                ))}
              </UiElement>
            </UiElement>
          )}
          <UiElement as="h3"><UiText>{"计划执行步骤"}</UiText></UiElement>
          <UiElement as="ol" className="rollout-planned-steps">
            {plan.steps.map((item) => (
              <UiElement as="li" key={item.id}>{item.title}</UiElement>
            ))}
          </UiElement>
          <UiElement as="p" className="rollout-muted">
            <UiText>{"有效期至"}</UiText> {when(plan.expires_at)}<UiText>{"；计划摘要"}</UiText>{" "}
            <UiElement as="code">{plan.digest}</UiElement>
          </UiElement>
          {step === 3 && (
            <>
              <InfoBox>
                <UiText>{"将安装 Agent 并验证 HTTPS 连接。遇到失败可能保留已创建资源；请从任务记录核对结果。此操作不会重新部署现有推理服务。"}</UiText>
              </InfoBox>
              <Field label={`输入节点名称“${plan.spec.name}”确认安装`} required>
                <UiElement as="input"
                  autoComplete="off"
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                />
              </Field>
              <UiElement as="label" className="rollout-checkbox">
                <UiElement as="input"
                  type="checkbox"
                  checked={approved}
                  onChange={(event) => setApproved(event.target.checked)}
                />
                <UiElement as="span">
                  <UiText>{"我已核对目标、主机指纹、镜像来源与预检计划，授权执行本次安装。"}</UiText>
                </UiElement>
              </UiElement>
            </>
          )}
        </>
      )}
    </UiElement>
  );
}

function DefaultRolloutHistory({
  submissionUncertain,
  busy,
  locked,
  historyLoaded,
  error,
  jobs,
  loadHistory,
  openJob,
}: {
  submissionUncertain: boolean;
  busy: string;
  locked: boolean;
  historyLoaded: boolean;
  error: string;
  jobs: AgentRolloutJob[];
  loadHistory: () => Promise<void>;
  openJob: (id: string) => Promise<void>;
}) {
  return (
    <UiElement as="div" className="rollout-section">
      {submissionUncertain && (
        <InfoBox>
          <UiText>{"安装请求的结果尚未确认。SSH 凭据已清空，请先在任务记录中核对进度；确认目标状态前请勿再次推送。"}</UiText>
        </InfoBox>
      )}
      <UiElement as="div" className="rollout-plan-heading">
        <UiElement as="h3"><UiText>{"最近 50 个安装任务"}</UiText></UiElement>
        <Button actionId="action.AgentRolloutStatus.001"
          loading={busy === "history"}
          disabled={locked && busy !== "history"}
          onClick={() => void loadHistory()}
        >
          <RefreshCw size={14} />
          <UiText>{"刷新记录"}</UiText>
        </Button>
      </UiElement>
      {!historyLoaded ? (
        <Spinner label="读取推送记录" />
      ) : jobs.length ? (
        <UiElement as="div" className="table-wrap">
          <UiElement as="table">
            <UiElement as="thead">
              <UiElement as="tr">
                <UiElement as="th"><UiText>{"节点 / 环境"}</UiText></UiElement>
                <UiElement as="th"><UiText>{"状态"}</UiText></UiElement>
                <UiElement as="th"><UiText>{"创建时间"}</UiText></UiElement>
                <UiElement as="th"><UiText>{"操作"}</UiText></UiElement>
              </UiElement>
            </UiElement>
            <UiElement as="tbody">
              {jobs.map((item) => (
                <UiElement as="tr" key={item.id}>
                  <UiElement as="td">
                    <UiElement as="b">{item.name}</UiElement>
                    <UiElement as="span" className="cell-secondary">
                      {item.address} <UiText>{"·"}</UiText>{" "}
                      {item.runtime === "docker" ? "Docker" : "Kubernetes"}
                    </UiElement>
                  </UiElement>
                  <UiElement as="td">
                    <Badge status={badgeStatus(item)}>
                      {agentRolloutStatus(item)}
                    </Badge>
                  </UiElement>
                  <UiElement as="td">{when(item.created_at)}</UiElement>
                  <UiElement as="td">
                    <UiButton actionId="action.AgentRolloutStatus.002"
                      className="text-link"
                      disabled={locked}
                      onClick={() => void openJob(item.id)}
                    >
                      <UiText>{"查看进度"}</UiText>
                    </UiButton>
                  </UiElement>
                </UiElement>
              ))}
            </UiElement>
          </UiElement>
        </UiElement>
      ) : (
        <Empty
          title={error ? "尚未取得推送记录" : "暂无推送记录"}
          description={error ? "请检查错误提示后刷新记录；读取失败不代表任务不存在。" : "通过本向导安装的 Agent 会保留任务与连接结果。"}
        />
      )}
    </UiElement>
  );
}

function DefaultRolloutJobDetails({
  job,
  busy,
  locked,
  openJob,
  jobAction,
  recovering,
  setRecovering,
  recoveryConfirmed,
  setRecoveryConfirmed,
  clearCredentials,
  recover,
  children,
}: {
  job: AgentRolloutJob;
  busy: string;
  locked: boolean;
  openJob: (id: string) => Promise<void>;
  jobAction: (action: "connect" | "cancel") => Promise<void>;
  recovering: boolean;
  setRecovering: (value: boolean) => void;
  recoveryConfirmed: boolean;
  setRecoveryConfirmed: (value: boolean) => void;
  clearCredentials: () => void;
  recover: () => Promise<void>;
  children: ReactNode;
}) {
  return (
    <UiElement as="div" className="rollout-section" aria-live="polite">
      <UiElement as="div" className="rollout-plan-heading">
        <UiElement as="h3">{job.name}</UiElement>
        <Badge status={badgeStatus(job)}>{agentRolloutStatus(job)}</Badge>
      </UiElement>
      <UiElement as="dl" className="rollout-summary">
        <UiElement as="dt"><UiText>{"目标"}</UiText></UiElement>
        <UiElement as="dd">
          {job.address} <UiText>{"·"}</UiText> {job.runtime === "docker" ? "Docker" : "Kubernetes"}
        </UiElement>
        <UiElement as="dt"><UiText>{"Agent 地址"}</UiText></UiElement>
        <UiElement as="dd">
          <UiElement as="code">{job.agent_url}</UiElement>
        </UiElement>
        <UiElement as="dt"><UiText>{"任务编号"}</UiText></UiElement>
        <UiElement as="dd">
          <UiElement as="code">{job.id}</UiElement>
        </UiElement>
        <UiElement as="dt"><UiText>{"最近更新"}</UiText></UiElement>
        <UiElement as="dd">{when(job.updated_at)}</UiElement>
      </UiElement>
      {job.connection && (
        <UiElement as="dl" className="rollout-summary">
          <UiElement as="dt"><UiText>{"原始 SSH 目标"}</UiText></UiElement>
          <UiElement as="dd">
            {job.connection.username}<UiText>{"@"}</UiText>{job.connection.host}<UiText>{":"}</UiText>
            {job.connection.port}
          </UiElement>
          <UiElement as="dt"><UiText>{"固定主机指纹"}</UiText></UiElement>
          <UiElement as="dd">
            <UiElement as="code">{job.connection.host_key_sha256}</UiElement>
          </UiElement>
        </UiElement>
      )}
      <UiElement as="div" className="job-steps rollout-job-steps">
        {job.steps.map((item, index) => (
          <UiElement as="div" key={item.id}>
            <UiElement as="span" className={`job-step-dot ${item.status}`}>
              {item.status === "completed" ? (
                <Check size={14} />
              ) : item.status === "running" ? (
                <Loader2 size={14} spin />
              ) : item.status === "failed" ? (
                <AlertCircle size={14} />
              ) : (
                index + 1
              )}
            </UiElement>
            <UiElement as="div">
              <UiElement as="b">{item.title}</UiElement>
              <UiElement as="p">
                {item.message ||
                  (item.status === "pending"
                    ? "等待执行"
                    : item.status === "running"
                      ? "正在执行"
                      : "")}
              </UiElement>
            </UiElement>
            <Badge status={item.status ?? "pending"}>
              {{
                pending: "等待执行",
                running: "执行中",
                completed: "已完成",
                failed: "失败",
                skipped: "已跳过",
              }[item.status ?? "pending"] ?? "待核对"}
            </Badge>
          </UiElement>
        ))}
      </UiElement>
      {job.status === "completed" && job.connection_verified && (
        <InfoBox>
          <UiText>{"Agent 已完成安装并通过实际连接验证，可在节点列表中管理。"}</UiText>
        </InfoBox>
      )}
      {job.status === "installed_unconnected" && (
        <InfoBox>
          <UiText>{"Agent 已安装，控制台尚未完成连接。请检查 HTTPS 入口、防火墙和证书信任，再点击“重试连接”；无需重新安装。"}</UiText>
        </InfoBox>
      )}
      {job.resources_may_exist &&
        !(job.status === "completed" && job.connection_verified) && (
          <InfoBox>
            <UiText>{"目标可能保留安装资源。取消或失败不会自动卸载，请按任务步骤核对后处理。"}</UiText>
          </InfoBox>
        )}
      {job.error_code && (
        <UiElement as="p" className="rollout-muted">
          <UiText>{"错误代码："}</UiText><UiElement as="code">{job.error_code}</UiElement>
        </UiElement>
      )}
      <UiElement as="div" className="rollout-job-actions">
        <Button actionId="action.AgentRolloutStatus.003" disabled={locked} onClick={() => void openJob(job.id)}>
          <RefreshCw size={14} />
          <UiText>{"刷新状态"}</UiText>
        </Button>
        {job.can_connect && (
          <Button actionId="action.AgentRolloutStatus.004"
            kind="primary"
            loading={busy === "connect"}
            disabled={locked}
            onClick={() => void jobAction("connect")}
          >
            <UiText>{"重试连接"}</UiText>
          </Button>
        )}
        {job.can_cancel && (
          <Button actionId="action.AgentRolloutStatus.005"
            loading={busy === "cancel"}
            disabled={locked}
            onClick={() => void jobAction("cancel")}
          >
            <UiText>{"取消后续步骤"}</UiText>
          </Button>
        )}
        {job.can_recover && !recovering && (
          <Button actionId="action.AgentRolloutStatus.006"
            disabled={locked}
            onClick={() => {
              clearCredentials();
              setRecoveryConfirmed(false);
              setRecovering(true);
            }}
          >
            <UiText>{"恢复核对"}</UiText>
          </Button>
        )}
      </UiElement>
      {recovering && job.can_recover && (
        <UiElement as="fieldset" className="rollout-inputs rollout-recovery" disabled={locked}>
          <InfoBox>
            <UiText>{"使用原始 SSH 目标与固定主机指纹，只读核对既有安装并恢复连接；不会重装、重启或覆盖 Agent。请重新提供本次使用的凭据。"}</UiText>
          </InfoBox>
          {children}
          <UiElement as="label" className="rollout-checkbox">
            <UiElement as="input"
              type="checkbox"
              checked={recoveryConfirmed}
              onChange={(event) => setRecoveryConfirmed(event.target.checked)}
            />
            <UiElement as="span"><UiText>{"已核对原始目标与固定主机指纹，同意只读检查现有安装。"}</UiText></UiElement>
          </UiElement>
          <UiElement as="div" className="rollout-job-actions">
            <Button actionId="action.AgentRolloutStatus.007"
              onClick={() => {
                clearCredentials();
                setRecovering(false);
              }}
            >
              <UiText>{"取消核对"}</UiText>
            </Button>
            <Button actionId="action.AgentRolloutStatus.008"
              kind="primary"
              loading={busy === "recover"}
              disabled={!recoveryConfirmed || locked}
              onClick={() => void recover()}
            >
              <UiText>{"执行只读核对"}</UiText>
            </Button>
          </UiElement>
        </UiElement>
      )}
    </UiElement>
  );
}

export const PlanReview = withUiSlot("component.PlanReview", DefaultPlanReview);
export const RolloutHistory = withUiSlot("component.RolloutHistory", DefaultRolloutHistory);
export const RolloutJobDetails = withUiSlot("component.RolloutJobDetails", DefaultRolloutJobDetails);

// Reference package implementation exports.
export { DefaultPlanReview, DefaultRolloutHistory, DefaultRolloutJobDetails };
