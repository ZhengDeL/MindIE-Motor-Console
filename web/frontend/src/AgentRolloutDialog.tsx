import { useBranding } from "./brandingState";
import { useEffect, useRef, useState } from "react";
import { Check, Clock3, ShieldCheck, Upload } from "./icons";
import { Button, ErrorBox, InfoBox, Modal } from "./ui";
import {
  agentRolloutError,
  cancelAgentRollout,
  connectAgentRollout,
  createAgentRolloutPlan,
  discoverAgentHostKey,
  executeAgentRollout,
  getAgentRollout,
  listAgentArtifacts,
  listAgentRollouts,
  recoverAgentRollout,
} from "./agentRollout";
import type {
  AgentHostKey,
  AgentImageArtifact,
  AgentRolloutJob,
  AgentRolloutPlan,
  AgentRolloutSpec,
  AgentSSHConnection,
  AgentSSHCredentials,
} from "./agentRollout";
import {
  ConfigurationFields,
  CredentialFields,
  IdentityFields,
} from "./AgentRolloutForms";
import {
  PlanReview,
  RolloutHistory,
  RolloutJobDetails,
} from "./AgentRolloutStatus";
import "./agentRollout.css";

const steps = [
  ["目标与身份", "SSH 连接与主机指纹"],
  ["安装配置", "镜像、运行时与资源"],
  ["预检与计划", "只读检查目标环境"],
  ["确认与安装", "执行进度与连接结果"],
];
const emptyCredentials = (
  method: AgentSSHCredentials["method"] = "password",
): AgentSSHCredentials => ({
  method,
  password: "",
  private_key: "",
  passphrase: "",
});
const initialSpec: AgentRolloutSpec = {
  name: "npu-worker-01",
  address: "",
  runtime: "docker",
  agent_url: "",
  agent_port: 8002,
  nic_name: "",
  labels: [],
  installation_name: "npu-worker-01",
  tls_mode: "managed_tls",
  https_proxy_confirmed: false,
  image_source: "artifact",
  image: "",
  artifact_id: "",
  kubeconfig_path: "",
  cpus: "1",
  memory: "1g",
  pids: 256,
  native_concurrency: 1,
};
const terminal = (job: AgentRolloutJob) =>
  !["queued", "running"].includes(job.status);
const ipv4 = (value: string) =>
  /^\d+\.\d+\.\d+\.\d+$/.test(value) &&
  value
    .split(".")
    .every((part) => Number(part) <= 255 && String(Number(part)) === part);

export default function AgentRolloutDialog({
  initialView = "new",
  canAdminister,
  onClose,
  onConnected,
}: {
  initialView?: "new" | "history";
  canAdminister: boolean;
  onClose: () => void;
  onConnected: () => void;
}) {
  const branding = useBranding();
  const [view, setView] = useState<"wizard" | "history" | "job">(
    initialView === "history" ? "history" : "wizard",
  );
  const [step, setStep] = useState(0);
  const [spec, setSpec] = useState<AgentRolloutSpec>({ ...initialSpec });
  const [connection, setConnection] = useState<AgentSSHConnection>({
    host: "",
    port: 22,
    username: "root",
    host_key_sha256: "",
    privilege: "root",
  });
  const [fingerprintConfirmed, setFingerprintConfirmed] = useState(false);
  const [observedKey, setObservedKey] = useState<AgentHostKey | null>(null);
  const [method, setMethod] =
    useState<AgentSSHCredentials["method"]>("password");
  const credentials = useRef<AgentSSHCredentials>(emptyCredentials());
  const [credentialEpoch, setCredentialEpoch] = useState(0);
  const [keyFilename, setKeyFilename] = useState("");
  const [labelsText, setLabelsText] = useState("");
  const [artifacts, setArtifacts] = useState<AgentImageArtifact[]>([]);
  const [artifactLoading, setArtifactLoading] = useState(true);
  const [artifactIssues, setArtifactIssues] = useState(false);
  const [plan, setPlan] = useState<AgentRolloutPlan | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [approved, setApproved] = useState(false);
  const [job, setJob] = useState<AgentRolloutJob | null>(null);
  const [jobs, setJobs] = useState<AgentRolloutJob[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [submissionUncertain, setSubmissionUncertain] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [recoveryConfirmed, setRecoveryConfirmed] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const alive = useRef(true);
  const connected = useRef(new Set<string>());
  const readKeyVersion = useRef(0);
  const shell = useRef<HTMLDivElement>(null);
  const expired = Boolean(
    plan &&
      (!Number.isFinite(Date.parse(plan.expires_at)) ||
        Date.parse(plan.expires_at) <= now),
  );
  const locked = Boolean(busy);

  function clearCredentials(nextMethod = method) {
    readKeyVersion.current++;
    credentials.current = emptyCredentials(nextMethod);
    setKeyFilename("");
    setCredentialEpoch((value) => value + 1);
  }
  function close() {
    clearCredentials();
    onClose();
  }
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      readKeyVersion.current++;
      credentials.current = emptyCredentials();
    };
  }, []);
  useEffect(() => {
    shell.current?.closest(".modal-body")?.scrollTo({ top: 0 });
  }, [step, view]);
  useEffect(() => {
    if (!canAdminister) return;
    const controller = new AbortController();
    listAgentArtifacts(controller.signal)
      .then((response) => {
        if (!alive.current) return;
        setArtifacts(response.items);
        setArtifactIssues(Boolean(response.errors?.length));
        if (response.items.length)
          setSpec((value) =>
            value.image_source === "artifact" && !value.artifact_id
              ? { ...value, artifact_id: response.items[0].id }
              : value,
          );
      })
      .catch((cause) => {
        if (!controller.signal.aborted && alive.current)
          setError(agentRolloutError(cause));
      })
      .finally(() => {
        if (alive.current) setArtifactLoading(false);
      });
    return () => controller.abort();
  }, [canAdminister]);
  useEffect(() => {
    if (!plan || view !== "wizard") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [plan, view]);
  useEffect(() => {
    if (
      !job ||
      job.status !== "completed" ||
      !job.connection_verified ||
      connected.current.has(job.id)
    )
      return;
    connected.current.add(job.id);
    onConnected();
  }, [job, onConnected]);
  useEffect(() => {
    if (!canAdminister || view !== "job" || !job || terminal(job)) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      getAgentRollout(job.id, controller.signal)
        .then((value) => {
          if (alive.current) {
            setJob(value);
            setError("");
          }
        })
        .catch((cause) => {
          if (!controller.signal.aborted && alive.current) {
            setError(agentRolloutError(cause));
            // Preserve the last known state. A lost poll never means installation failed.
            setJob((value) => (value ? { ...value } : value));
          }
        });
    }, 2000);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [job, view, canAdminister]);

  function changeSpec<K extends keyof AgentRolloutSpec>(
    key: K,
    value: AgentRolloutSpec[K],
  ) {
    setSpec((current) => {
      const next = { ...current, [key]: value };
      if (next.tls_mode === "managed_tls")
        next.agent_url = next.address
          ? `https://${next.address}:${next.agent_port}`
          : "";
      return next;
    });
    setPlan(null);
    setApproved(false);
    setError("");
  }
  function changeConnection<K extends keyof AgentSSHConnection>(
    key: K,
    value: AgentSSHConnection[K],
  ) {
    setConnection((current) => ({
      ...current,
      [key]: value,
      ...(["host", "port"].includes(key) ? { host_key_sha256: "" } : {}),
    }));
    setFingerprintConfirmed(false);
    setPlan(null);
    setApproved(false);
    setError("");
    if (["host", "port"].includes(key)) setObservedKey(null);
  }
  function checkIdentity() {
    if (!spec.name.trim()) return "请输入节点名称。";
    if (!ipv4(spec.address)) return "请输入目标节点的管理 IPv4 地址。";
    if (
      !connection.host.trim() ||
      !Number.isInteger(connection.port) ||
      connection.port < 1 ||
      connection.port > 65535
    )
      return "请填写有效的 SSH 主机与端口。";
    if (!/^[A-Za-z_][A-Za-z0-9_.-]{0,63}\$?$/.test(connection.username))
      return "SSH 用户名格式不正确。";
    if (connection.privilege === "root" && connection.username !== "root")
      return "非 root 用户请选择 sudo -n 免密提权。";
    if (!/^SHA256:[A-Za-z0-9+/]{43}$/.test(connection.host_key_sha256))
      return "请输入通过独立渠道核对的完整 SHA256 主机指纹。";
    if (!fingerprintConfirmed) return "请先独立核对 SSH 主机指纹并确认。";
    if (observedKey && observedKey.fingerprint !== connection.host_key_sha256)
      return "期望指纹与当前探测结果不一致，已阻止发送凭据。";
    if (method === "password" && !credentials.current.password)
      return "请输入 SSH 登录密码。";
    if (method === "private_key" && !credentials.current.private_key)
      return "请选择不超过 64 KiB 的 SSH 私钥文件。";
    return "";
  }
  function checkConfiguration() {
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(spec.installation_name))
      return "安装标识须为 1–63 位小写字母、数字或连字符，首位为字母或数字。";
    if (
      !Number.isInteger(spec.agent_port) ||
      spec.agent_port < 1024 ||
      spec.agent_port > 65535
    )
      return "Agent 端口须为 1024–65535。";
    if (
      spec.image_source === "artifact" &&
      !artifacts.some((item) => item.id === spec.artifact_id)
    )
      return "请选择已准备的 Agent 镜像包，或改用固定摘要的镜像仓库。";
    if (
      spec.image_source === "registry" &&
      !/^[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}$/.test(spec.image)
    )
      return "仓库镜像须使用 repo@sha256:完整摘要，不能只填标签。";
    if (
      spec.runtime === "kubernetes" &&
      (!spec.kubeconfig_path.startsWith("/") ||
        spec.kubeconfig_path.split("/").includes(".."))
    )
      return "请填写目标节点上已授权 kubeconfig 的绝对路径。";
    if (spec.tls_mode === "existing_https" && !spec.https_proxy_confirmed)
      return "请确认现有 HTTPS 入口已正确转发至该 Agent。";
    try {
      const url = new URL(spec.agent_url);
      if (
        url.protocol !== "https:" ||
        url.username ||
        url.password ||
        url.search ||
        url.hash ||
        (url.pathname && url.pathname !== "/")
      )
        throw Error();
    } catch {
      return "Agent 地址须为不含路径和凭据的 HTTPS 入口。";
    }
    if (
      !/^\d+(?:\.\d{1,3})?$/.test(spec.cpus) ||
      Number(spec.cpus) < 0.25 ||
      Number(spec.cpus) > 32
    )
      return "CPU 限额须为 0.25–32 核。";
    const memory = /^([1-9]\d*)([kmg])$/.exec(spec.memory);
    const bytes = memory
      ? Number(memory[1]) *
        ({ k: 1024, m: 1024 ** 2, g: 1024 ** 3 }[memory[2]] ?? 0)
      : 0;
    if (bytes < 512 * 1024 ** 2 || bytes > 64 * 1024 ** 3)
      return "内存限额须为 512m–64g，例如 1g。";
    if (
      !Number.isInteger(spec.pids) ||
      spec.pids < 64 ||
      spec.pids > 4096 ||
      !Number.isInteger(spec.native_concurrency) ||
      spec.native_concurrency < 1 ||
      spec.native_concurrency > 4
    )
      return "进程数须为 64–4096，原生任务并发须为 1–4。";
    return "";
  }
  async function discover() {
    if (
      !connection.host.trim() ||
      !Number.isInteger(connection.port) ||
      connection.port < 1 ||
      connection.port > 65535
    ) {
      setError("请先填写有效的 SSH 主机与端口。");
      return;
    }
    setBusy("host-key");
    setError("");
    try {
      const value = await discoverAgentHostKey(
        connection.host.trim(),
        connection.port,
      );
      if (alive.current) setObservedKey(value);
    } catch (cause) {
      if (alive.current) setError(agentRolloutError(cause));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function readKey(file?: File) {
    const version = ++readKeyVersion.current;
    credentials.current.private_key = "";
    setKeyFilename("");
    if (!file) return;
    if (file.size > 65536 || file.size === 0) {
      setError("私钥文件须为 1 字节至 64 KiB。");
      return;
    }
    try {
      const value = await file.text();
      if (!alive.current || version !== readKeyVersion.current) return;
      if (!value.includes("PRIVATE KEY-----")) {
        setError("文件未包含受支持的 SSH 私钥头，请选择正确文件。");
        return;
      }
      credentials.current.private_key = value;
      setKeyFilename(file.name);
      setError("");
    } catch {
      if (alive.current) setError("无法读取私钥文件。");
    }
  }
  async function preflight() {
    const issue = checkIdentity() || checkConfiguration();
    if (issue) {
      setError(issue);
      return;
    }
    setBusy("preflight");
    setError("");
    setPlan(null);
    setApproved(false);
    try {
      const value = await createAgentRolloutPlan(
        { ...spec, name: spec.name.trim() },
        { ...connection, host: connection.host.trim() },
        { ...credentials.current },
      );
      if (alive.current) {
        setPlan(value);
        setNow(Date.now());
      }
    } catch (cause) {
      if (alive.current) setError(agentRolloutError(cause));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function execute() {
    if (
      !canAdminister ||
      !plan ||
      !plan.can_execute ||
      expired ||
      confirmation !== plan.spec.name ||
      !approved
    )
      return;
    const issue = checkIdentity();
    if (issue) {
      setError(issue);
      setStep(0);
      return;
    }
    setBusy("execute");
    setError("");
    const request = executeAgentRollout(
      plan,
      { ...credentials.current },
      confirmation,
    );
    clearCredentials();
    try {
      const result = await request;
      if (!alive.current) return;
      const value = await getAgentRollout(result.job_id);
      if (alive.current) {
        setView("job");
        setJob(value);
        setPlan(null);
      }
    } catch (cause) {
      if (alive.current) {
        setError(agentRolloutError(cause));
        setPlan(null);
        setSubmissionUncertain(true);
        setView("history");
        setHistoryLoaded(false);
      }
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function loadHistory() {
    setBusy("history");
    setError("");
    try {
      const result = await listAgentRollouts();
      if (alive.current) {
        setJobs(result.items);
        setHistoryLoaded(true);
      }
    } catch (cause) {
      if (alive.current) setError(agentRolloutError(cause));
    } finally {
      if (alive.current) {
        setHistoryLoaded(true);
        setBusy("");
      }
    }
  }
  useEffect(() => {
    if (canAdminister && view === "history" && !historyLoaded && !busy)
      void loadHistory();
  }, [canAdminister, view, historyLoaded, busy]);
  async function openJob(id: string) {
    setBusy("job");
    setError("");
    try {
      const value = await getAgentRollout(id);
      if (alive.current) {
        clearCredentials();
        setRecovering(false);
        setJob(value);
        setView("job");
      }
    } catch (cause) {
      if (alive.current) setError(agentRolloutError(cause));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function jobAction(action: "connect" | "cancel") {
    if (
      !job ||
      !canAdminister ||
      (action === "connect" ? !job.can_connect : !job.can_cancel)
    )
      return;
    setBusy(action);
    setError("");
    try {
      if (action === "connect") await connectAgentRollout(job.id);
      else await cancelAgentRollout(job.id);
      const value = await getAgentRollout(job.id);
      if (alive.current) setJob(value);
    } catch (cause) {
      if (alive.current) setError(agentRolloutError(cause));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  async function recover() {
    if (!canAdminister || !job?.can_recover || !recoveryConfirmed) return;
    if (
      method === "password"
        ? !credentials.current.password
        : !credentials.current.private_key
    ) {
      setError("请重新提供本次恢复核对使用的 SSH 凭据。");
      return;
    }
    setBusy("recover");
    setError("");
    const request = recoverAgentRollout(job.id, { ...credentials.current });
    clearCredentials();
    try {
      await request;
      const value = await getAgentRollout(job.id);
      if (alive.current) {
        setJob(value);
        setRecovering(false);
        setRecoveryConfirmed(false);
      }
    } catch (cause) {
      if (alive.current) setError(agentRolloutError(cause));
    } finally {
      if (alive.current) setBusy("");
    }
  }
  function credentialFields() {
    return (
      <CredentialFields
        method={method}
        epoch={credentialEpoch}
        keyFilename={keyFilename}
        onMethod={(value) => {
          setMethod(value);
          clearCredentials(value);
        }}
        onKey={(file) => {
          void readKey(file);
        }}
        onPassword={(value) => {
          credentials.current.password = value;
        }}
        onPassphrase={(value) => {
          credentials.current.passphrase = value;
        }}
      />
    );
  }
  function showHistory() {
    clearCredentials();
    setRecovering(false);
    setView("history");
    setHistoryLoaded(false);
    setError("");
  }
  function newRollout() {
    clearCredentials();
    setPlan(null);
    setJob(null);
    setApproved(false);
    setConfirmation("");
    setStep(0);
    setView("wizard");
    setError("");
    setSubmissionUncertain(false);
    setRecovering(false);
  }

  if (!canAdminister)
    return (
      <Modal title="Agent 推送" onClose={close}>
        <InfoBox>只有系统管理员可以推送和管理 Agent 安装任务。</InfoBox>
      </Modal>
    );
  return (
    <Modal
      wide
      title={
        view === "history"
          ? "Agent 推送记录"
          : view === "job"
            ? "Agent 安装进度"
            : "推送 Agent"
      }
      subtitle={`将 ${branding.name} Agent 安装到目标节点，保留原生 Motor 的部署行为。`}
      onClose={close}
      footer={
        <>
          <span className="rollout-footer-note">
            {view === "job"
              ? "关闭窗口后，后台任务继续执行。"
              : "SSH 凭据仅在本窗口内存中使用。"}
          </span>
          <Button onClick={close}>关闭</Button>
          {view === "wizard" && step > 0 && (
            <Button
              disabled={locked}
              onClick={() => {
                setStep(step - 1);
                setError("");
              }}
            >
              上一步
            </Button>
          )}
          {view === "wizard" && step < 2 && (
            <Button
              kind="primary"
              disabled={locked}
              onClick={() => {
                const issue =
                  step === 0 ? checkIdentity() : checkConfiguration();
                if (issue) setError(issue);
                else {
                  setError("");
                  setStep(step + 1);
                }
              }}
            >
              下一步
            </Button>
          )}
          {view === "wizard" && step === 2 && (
            <>
              <Button
                loading={busy === "preflight"}
                disabled={locked && busy !== "preflight"}
                kind={plan?.can_execute && !expired ? "default" : "primary"}
                onClick={() => void preflight()}
              >
                <ShieldCheck size={15} />
                {plan ? "重新预检" : "执行预检"}
              </Button>
              {plan && (
                <Button
                  kind="primary"
                  disabled={locked || !plan.can_execute || expired}
                  onClick={() => {
                    setConfirmation("");
                    setApproved(false);
                    setStep(3);
                  }}
                >
                  下一步：确认安装
                </Button>
              )}
            </>
          )}
          {view === "wizard" && step === 3 && (
            <Button
              kind="primary"
              loading={busy === "execute"}
              disabled={
                !plan?.can_execute ||
                expired ||
                confirmation !== plan?.spec.name ||
                !approved ||
                locked
              }
              onClick={() => void execute()}
            >
              <Upload size={15} />
              安装并接入
            </Button>
          )}
        </>
      }
    >
      <div className="rollout-shell" ref={shell}>
        <div className="rollout-tabs" role="group" aria-label="Agent 推送视图">
          <Button
            kind={view !== "history" ? "primary" : "default"}
            disabled={locked}
            onClick={newRollout}
          >
            <Upload size={14} />
            新建推送
          </Button>
          <Button
            kind={view === "history" ? "primary" : "default"}
            disabled={locked}
            onClick={showHistory}
          >
            <Clock3 size={14} />
            推送记录
          </Button>
        </div>
        {view === "wizard" && (
          <div
            className="wizard-steps rollout-wizard-steps"
            aria-label="安装步骤"
          >
            {steps.map(([title, subtitle], index) => (
              <div
                className={`${step === index ? "current" : ""} ${step > index ? "done" : ""}`}
                key={title}
                aria-current={step === index ? "step" : undefined}
              >
                <span className="step-dot">
                  {step > index ? <Check size={16} /> : index + 1}
                </span>
                <span>
                  <b>{title}</b>
                  <small>{subtitle}</small>
                </span>
                {index < 3 && <i className="step-line" />}
              </div>
            ))}
          </div>
        )}
        {error && <ErrorBox message={error} />}
        {view === "wizard" && (
          <fieldset className="rollout-inputs" disabled={locked}>
            {view === "wizard" && step === 0 && (
              <IdentityFields
                spec={spec}
                connection={connection}
                changeSpec={changeSpec}
                changeConnection={changeConnection}
                busy={busy}
                locked={locked}
                observedKey={observedKey}
                fingerprintConfirmed={fingerprintConfirmed}
                setFingerprintConfirmed={setFingerprintConfirmed}
                discover={discover}
              >
                {credentialFields()}
              </IdentityFields>
            )}
            {view === "wizard" && step === 1 && (
              <ConfigurationFields
                spec={spec}
                changeSpec={changeSpec}
                artifacts={artifacts}
                artifactLoading={artifactLoading}
                artifactIssues={artifactIssues}
                labelsText={labelsText}
                setLabelsText={setLabelsText}
              />
            )}
            {view === "wizard" && step >= 2 && (
              <PlanReview
                spec={spec}
                connection={connection}
                plan={plan}
                expired={expired}
                step={step}
                confirmation={confirmation}
                setConfirmation={setConfirmation}
                approved={approved}
                setApproved={setApproved}
              />
            )}
          </fieldset>
        )}
        {view === "history" && (
          <RolloutHistory
            submissionUncertain={submissionUncertain}
            busy={busy}
            locked={locked}
            historyLoaded={historyLoaded}
            error={error}
            jobs={jobs}
            loadHistory={loadHistory}
            openJob={openJob}
          />
        )}
        {view === "job" && job && (
          <RolloutJobDetails
            job={job}
            busy={busy}
            locked={locked}
            openJob={openJob}
            jobAction={jobAction}
            recovering={recovering}
            setRecovering={setRecovering}
            recoveryConfirmed={recoveryConfirmed}
            setRecoveryConfirmed={setRecoveryConfirmed}
            clearCredentials={clearCredentials}
            recover={recover}
          >
            {credentialFields()}
          </RolloutJobDetails>
        )}
      </div>
    </Modal>
  );
}
