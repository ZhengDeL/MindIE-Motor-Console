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

export function PlanReview({
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
    <div className="rollout-section">
      <dl className="rollout-summary">
        <dt>目标</dt>
        <dd>
          {spec.name} · {spec.address} ·{" "}
          {spec.runtime === "docker" ? "Docker" : "Kubernetes"}
        </dd>
        <dt>安装标识</dt>
        <dd>
          <code>{spec.installation_name}</code>
        </dd>
        <dt>SSH 身份</dt>
        <dd>
          {connection.username}@{connection.host}:{connection.port} ·{" "}
          {connection.privilege === "root" ? "root" : "sudo -n"}
        </dd>
        <dt>Agent 地址</dt>
        <dd>
          <code>{spec.agent_url}</code>
        </dd>
        <dt>资源上限</dt>
        <dd>
          {spec.cpus} 核 · {spec.memory} 内存 · {spec.pids} 进程
        </dd>
      </dl>
      {!plan ? (
        <InfoBox>
          预检只检查 SSH 身份、运行时、端口、镜像与已存在安装，不创建 Agent
          容器。点击“执行预检”生成有时效的安装计划。
        </InfoBox>
      ) : (
        <>
          <div className="rollout-plan-heading">
            <h3>预检结果</h3>
            <Badge
              status={plan.can_execute && !expired ? "success" : "warning"}
            >
              {expired
                ? "计划已过期"
                : plan.can_execute
                  ? "可以安装"
                  : "已阻止安装"}
            </Badge>
          </div>
          <div className="check-list">
            {plan.checks.map((check) => (
              <div key={check.id}>
                <Badge
                  status={check.status === "passed" ? "success" : check.status}
                >
                  {check.status === "passed"
                    ? "通过"
                    : check.status === "warning"
                      ? "需关注"
                      : "失败"}
                </Badge>
                <div>
                  <b>{check.title}</b>
                  <p>{check.message}</p>
                </div>
              </div>
            ))}
          </div>
          {plan.existing_installation.length > 0 && (
            <div className="rollout-existing">
              <InfoBox>
                发现已有
                Agent，禁止覆盖。请检查原安装或通过“接入节点”连接，勿更换标识绕过预检。
              </InfoBox>
              <ul>
                {plan.existing_installation.map((item) => (
                  <li key={item.container_id}>
                    <b>{item.name}</b> · {item.runtime} · {item.state}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <h3>计划执行步骤</h3>
          <ol className="rollout-planned-steps">
            {plan.steps.map((item) => (
              <li key={item.id}>{item.title}</li>
            ))}
          </ol>
          <p className="rollout-muted">
            有效期至 {when(plan.expires_at)}；计划摘要{" "}
            <code>{plan.digest}</code>
          </p>
          {step === 3 && (
            <>
              <InfoBox>
                将安装 Agent 并验证 HTTPS
                连接。遇到失败可能保留已创建资源；请从任务记录核对结果。此操作不会重新部署现有推理服务。
              </InfoBox>
              <Field label={`输入节点名称“${plan.spec.name}”确认安装`} required>
                <input
                  autoComplete="off"
                  value={confirmation}
                  onChange={(event) => setConfirmation(event.target.value)}
                />
              </Field>
              <label className="rollout-checkbox">
                <input
                  type="checkbox"
                  checked={approved}
                  onChange={(event) => setApproved(event.target.checked)}
                />
                <span>
                  我已核对目标、主机指纹、镜像来源与预检计划，授权执行本次安装。
                </span>
              </label>
            </>
          )}
        </>
      )}
    </div>
  );
}

export function RolloutHistory({
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
    <div className="rollout-section">
      {submissionUncertain && (
        <InfoBox>
          安装请求的结果尚未确认。SSH
          凭据已清空，请先在任务记录中核对进度；确认目标状态前请勿再次推送。
        </InfoBox>
      )}
      <div className="rollout-plan-heading">
        <h3>最近 50 个安装任务</h3>
        <Button
          loading={busy === "history"}
          disabled={locked && busy !== "history"}
          onClick={() => void loadHistory()}
        >
          <RefreshCw size={14} />
          刷新记录
        </Button>
      </div>
      {!historyLoaded ? (
        <Spinner label="读取推送记录" />
      ) : jobs.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>节点 / 环境</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((item) => (
                <tr key={item.id}>
                  <td>
                    <b>{item.name}</b>
                    <span className="cell-secondary">
                      {item.address} ·{" "}
                      {item.runtime === "docker" ? "Docker" : "Kubernetes"}
                    </span>
                  </td>
                  <td>
                    <Badge status={badgeStatus(item)}>
                      {agentRolloutStatus(item)}
                    </Badge>
                  </td>
                  <td>{when(item.created_at)}</td>
                  <td>
                    <button
                      className="text-link"
                      disabled={locked}
                      onClick={() => void openJob(item.id)}
                    >
                      查看进度
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          title={error ? "尚未取得推送记录" : "暂无推送记录"}
          description={error ? "请检查错误提示后刷新记录；读取失败不代表任务不存在。" : "通过本向导安装的 Agent 会保留任务与连接结果。"}
        />
      )}
    </div>
  );
}

export function RolloutJobDetails({
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
    <div className="rollout-section" aria-live="polite">
      <div className="rollout-plan-heading">
        <h3>{job.name}</h3>
        <Badge status={badgeStatus(job)}>{agentRolloutStatus(job)}</Badge>
      </div>
      <dl className="rollout-summary">
        <dt>目标</dt>
        <dd>
          {job.address} · {job.runtime === "docker" ? "Docker" : "Kubernetes"}
        </dd>
        <dt>Agent 地址</dt>
        <dd>
          <code>{job.agent_url}</code>
        </dd>
        <dt>任务编号</dt>
        <dd>
          <code>{job.id}</code>
        </dd>
        <dt>最近更新</dt>
        <dd>{when(job.updated_at)}</dd>
      </dl>
      {job.connection && (
        <dl className="rollout-summary">
          <dt>原始 SSH 目标</dt>
          <dd>
            {job.connection.username}@{job.connection.host}:
            {job.connection.port}
          </dd>
          <dt>固定主机指纹</dt>
          <dd>
            <code>{job.connection.host_key_sha256}</code>
          </dd>
        </dl>
      )}
      <div className="job-steps rollout-job-steps">
        {job.steps.map((item, index) => (
          <div key={item.id}>
            <span className={`job-step-dot ${item.status}`}>
              {item.status === "completed" ? (
                <Check size={14} />
              ) : item.status === "running" ? (
                <Loader2 size={14} spin />
              ) : item.status === "failed" ? (
                <AlertCircle size={14} />
              ) : (
                index + 1
              )}
            </span>
            <div>
              <b>{item.title}</b>
              <p>
                {item.message ||
                  (item.status === "pending"
                    ? "等待执行"
                    : item.status === "running"
                      ? "正在执行"
                      : "")}
              </p>
            </div>
            <Badge status={item.status ?? "pending"}>
              {{
                pending: "等待执行",
                running: "执行中",
                completed: "已完成",
                failed: "失败",
                skipped: "已跳过",
              }[item.status ?? "pending"] ?? "待核对"}
            </Badge>
          </div>
        ))}
      </div>
      {job.status === "completed" && job.connection_verified && (
        <InfoBox>
          Agent 已完成安装并通过实际连接验证，可在节点列表中管理。
        </InfoBox>
      )}
      {job.status === "installed_unconnected" && (
        <InfoBox>
          Agent 已安装，控制台尚未完成连接。请检查 HTTPS
          入口、防火墙和证书信任，再点击“重试连接”；无需重新安装。
        </InfoBox>
      )}
      {job.resources_may_exist &&
        !(job.status === "completed" && job.connection_verified) && (
          <InfoBox>
            目标可能保留安装资源。取消或失败不会自动卸载，请按任务步骤核对后处理。
          </InfoBox>
        )}
      {job.error_code && (
        <p className="rollout-muted">
          错误代码：<code>{job.error_code}</code>
        </p>
      )}
      <div className="rollout-job-actions">
        <Button disabled={locked} onClick={() => void openJob(job.id)}>
          <RefreshCw size={14} />
          刷新状态
        </Button>
        {job.can_connect && (
          <Button
            kind="primary"
            loading={busy === "connect"}
            disabled={locked}
            onClick={() => void jobAction("connect")}
          >
            重试连接
          </Button>
        )}
        {job.can_cancel && (
          <Button
            loading={busy === "cancel"}
            disabled={locked}
            onClick={() => void jobAction("cancel")}
          >
            取消后续步骤
          </Button>
        )}
        {job.can_recover && !recovering && (
          <Button
            disabled={locked}
            onClick={() => {
              clearCredentials();
              setRecoveryConfirmed(false);
              setRecovering(true);
            }}
          >
            恢复核对
          </Button>
        )}
      </div>
      {recovering && job.can_recover && (
        <fieldset className="rollout-inputs rollout-recovery" disabled={locked}>
          <InfoBox>
            使用原始 SSH
            目标与固定主机指纹，只读核对既有安装并恢复连接；不会重装、重启或覆盖
            Agent。请重新提供本次使用的凭据。
          </InfoBox>
          {children}
          <label className="rollout-checkbox">
            <input
              type="checkbox"
              checked={recoveryConfirmed}
              onChange={(event) => setRecoveryConfirmed(event.target.checked)}
            />
            <span>已核对原始目标与固定主机指纹，同意只读检查现有安装。</span>
          </label>
          <div className="rollout-job-actions">
            <Button
              onClick={() => {
                clearCredentials();
                setRecovering(false);
              }}
            >
              取消核对
            </Button>
            <Button
              kind="primary"
              loading={busy === "recover"}
              disabled={!recoveryConfirmed || locked}
              onClick={() => void recover()}
            >
              执行只读核对
            </Button>
          </div>
        </fieldset>
      )}
    </div>
  );
}
