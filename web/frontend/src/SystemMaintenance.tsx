import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import { useEffect, useRef, useState } from "react";
import { readCount, readNotice, type ReadState } from "./readState";
import {
  Archive,
  ArrowRight,
  CheckCircle2,
  Download,
  GitBranch,
  RefreshCw,
  ShieldCheck,
} from "./icons";
import { api, download, post, when } from "./api";
import type { Json } from "./api";
import {
  Badge,
  Button,
  Copy,
  Empty,
  ErrorBox,
  InfoBox,
  Modal,
  Spinner,
} from "./ui";

type Version = {
  console: { version: string; agent_protocol: number };
  storage: { engine: string; schema_version: number; mode: string };
  motor: { profile_id: string; commit: string; driver: string };
  upgrade: { mode: string; release_directory_configured: boolean };
  demo?: boolean;
  recovery_archive?: {
    configured: boolean;
    status: string;
    last_success_at: string | null;
    age_seconds: number | null;
    max_age_seconds: number;
    encrypted: boolean;
    off_host_verified: boolean;
    last_attempt_status: string | null;
    sha256: string | null;
    size_bytes: number | null;
  };
};
type Release = {
  id: string;
  version: string | null;
  image: string | null;
  status: "verified" | "blocked";
  errors: string[];
};
type UpgradePlan = {
  id: string;
  status: string;
  checks: { name: string; status: string; detail: string }[];
  current_version: string;
  target_version: string;
  image: string;
  web_image?: string;
  manifest_sha256: string;
  created_at: string;
  host_command: string;
  requires_host_execution: boolean;
  backup_required: boolean;
  demo?: boolean;
  error?: string;
  execution?: Json;
};
type Backup = {
  id: string;
  created_at: string;
  sha256: string;
  size_bytes: number;
  schema_version: number;
  verified: boolean;
};
type Verification = {
  id: string;
  verified: boolean;
  sha256: string;
  schema_version: number;
  verified_at: string;
};
type MaintenanceResource = "version" | "releases" | "history" | "backups";

function DefaultSystemMaintenance() {
  const [tab, setTab] = useState("upgrade");
  const [version, setVersion] = useState<Version | null>(null);
  const [releases, setReleases] = useState<Release[]>([]);
  const [history, setHistory] = useState<UpgradePlan[]>([]);
  const [backups, setBackups] = useState<Backup[]>([]);
  const [loading, setLoading] = useState(true);
  const [reads, setReads] = useState<Record<MaintenanceResource, ReadState>>({
    version: { loaded: false, refreshing: true, error: "" },
    releases: { loaded: false, refreshing: true, error: "" },
    history: { loaded: false, refreshing: true, error: "" },
    backups: { loaded: false, refreshing: true, error: "" },
  });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [plan, setPlan] = useState<UpgradePlan | null>(null);
  const [backupConfirm, setBackupConfirm] = useState(false);
  const [verification, setVerification] = useState<Verification | null>(null);
  const verificationTrigger = useRef<HTMLButtonElement | null>(null);
  const [verificationErrors, setVerificationErrors] = useState<
    Record<string, string>
  >({});
  const loadGeneration = useRef(0);
  const loadRequest = useRef<AbortController | null>(null);
  const load = async () => {
    loadRequest.current?.abort();
    const controller = new AbortController();
    loadRequest.current = controller;
    const generation = ++loadGeneration.current;
    setLoading(true);
    setError("");
    const read = async <T,>(key: MaintenanceResource, request: () => Promise<T>, accept: (value: T) => void) => {
      setReads((previous) => ({ ...previous, [key]: { ...previous[key], refreshing: true, error: "" } }));
      try {
        const value = await request();
        if (controller.signal.aborted || generation !== loadGeneration.current) return;
        accept(value);
        setReads((previous) => ({ ...previous, [key]: { loaded: true, refreshing: false, error: "" } }));
      } catch (reason) {
        if (controller.signal.aborted || generation !== loadGeneration.current) return;
        setReads((previous) => ({ ...previous, [key]: { ...previous[key], refreshing: false, error: (reason as Error).message } }));
        throw reason;
      }
    };
    const requests = await Promise.allSettled([
      read("version", () => api<Version>("/system/version", { signal: controller.signal }), setVersion),
      read("releases", () => api<{ items: Release[] }>("/system/releases", { signal: controller.signal }), (value) => setReleases(value.items)),
      read("history", () => api<{ items: UpgradePlan[] }>("/system/upgrades", { signal: controller.signal }), (value) => setHistory(value.items)),
      read("backups", () => api<{ items: Backup[] }>("/system/backups", { signal: controller.signal }), (value) => setBackups(value.items)),
    ]);
    if (controller.signal.aborted || generation !== loadGeneration.current) return;
    setError(
      requests
        .filter((entry) => entry.status === "rejected")
        .map((entry) => String((entry as PromiseRejectedResult).reason.message))
        .join("；"),
    );
    setLoading(false);
  };
  useEffect(() => {
    void load();
    return () => {
      loadGeneration.current++;
      loadRequest.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (verification || !verificationTrigger.current) return;
    const trigger = verificationTrigger.current;
    verificationTrigger.current = null;
    // Verification disables its trigger while awaiting the response. Remember
    // that button before the request, then restore it after the closed state
    // commits and the dialog has removed its focus trap and background inertness.
    const frame = requestAnimationFrame(() => {
      if (
        trigger?.isConnected &&
        !trigger.matches(":disabled") &&
        !trigger.closest("[inert]") &&
        !document.querySelector('[role="dialog"][aria-modal="true"]')
      )
        trigger.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [verification]);
  const makePlan = async (release: Release) => {
    setBusy(release.id);
    setError("");
    try {
      setPlan(
        await post<UpgradePlan>("/system/upgrades/plan", {
          release_id: release.id,
        }),
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };
  return (
    <>
      <UiElement as="div" className="maintenance-toolbar">
        <UiElement as="span"><UiText>{"平台升级由部署主机执行，Web 负责校验、生成计划和查询记录。"}</UiText></UiElement>
        <Button actionId="action.SystemMaintenance.001" loading={loading} onClick={() => void load()}>
          <RefreshCw size={14} />
          <UiText>{"刷新"}</UiText>
        </Button>
      </UiElement>
      {error && <ErrorBox message={error} />}
      <UiElement as="div" className="detail-tab-bar">
        <UiButton actionId="action.SystemMaintenance.002"
          className={tab === "upgrade" ? "active" : ""}
          onClick={() => setTab("upgrade")}
        >
          <GitBranch size={14} />
          <UiText>{"版本与升级"}</UiText>
        </UiButton>
        <UiButton actionId="action.SystemMaintenance.003"
          className={tab === "backup" ? "active" : ""}
          onClick={() => setTab("backup")}
        >
          <Archive size={14} />
          <UiText>{"备份与校验"}</UiText>
        </UiButton>
        <UiButton actionId="action.SystemMaintenance.004"
          className={tab === "history" ? "active" : ""}
          onClick={() => setTab("history")}
        >
          <ShieldCheck size={14} />
          <UiText>{"升级记录"}</UiText>
        </UiButton>
      </UiElement>
      {loading && !version && tab === "upgrade" && !reads.version.error ? (
        <Spinner />
      ) : tab === "upgrade" ? (
        <>
          <UiRegion name="layout.panel" as="section" className="panel maintenance-version">
            <UiElement as="div" className="panel-head">
              <UiElement as="h2"><UiText>{"当前系统"}</UiText></UiElement>
              {version?.demo && <UiElement as="span" className="tag amber"><UiText>{"演示模式"}</UiText></UiElement>}
            </UiElement>
            {version ? <UiElement as="dl" className="system-version-grid">
              <UiElement as="div">
                <UiElement as="dt"><UiText>{"控制台版本"}</UiText></UiElement>
                <UiElement as="dd">{version.console.version}</UiElement>
              </UiElement>
              <UiElement as="div">
                <UiElement as="dt"><UiText>{"Agent 协议"}</UiText></UiElement>
                <UiElement as="dd">{version.console.agent_protocol}</UiElement>
              </UiElement>
              <UiElement as="div">
                <UiElement as="dt"><UiText>{"数据库"}</UiText></UiElement>
                <UiElement as="dd">
                  {version.storage.engine} <UiText>{"· schema"}</UiText> {version.storage.schema_version}
                </UiElement>
              </UiElement>
              <UiElement as="div">
                <UiElement as="dt"><UiText>{"Motor 兼容配置"}</UiText></UiElement>
                <UiElement as="dd">{version.motor.profile_id}</UiElement>
              </UiElement>
              <UiElement as="div" className="system-version-wide">
                <UiElement as="dt"><UiText>{"Motor 提交"}</UiText></UiElement>
                <UiElement as="dd" className="mono">
                  {version.motor.commit}
                  {version?.motor.commit && (
                    <Copy value={version.motor.commit} />
                  )}
                </UiElement>
              </UiElement>
            </UiElement> : <Empty title="系统信息未读取" description="请刷新系统信息，核对连接状态后再规划升级。" />}
          </UiRegion>
          <InfoBox>
            <UiText>{"升级前需核对发布签名、版本方向、Agent 协议与数据库兼容性；执行时需要停止控制台并完成备份。平台升级不自动升级 Motor 推理工作负载。"}</UiText>
          </InfoBox>
          <UiRegion name="layout.panel" as="section" className="panel">
            <UiElement as="div" className="panel-head">
              <UiElement as="h2"><UiText>{"发布候选"}</UiText></UiElement>
              <UiElement as="span" className="small-caption">
                <UiText>{"由部署人员预先放入受信发布目录"}</UiText>
              </UiElement>
            </UiElement>
            {readNotice(reads.releases) && <UiElement as="p" className="settings-context-note" role="status">{readNotice(reads.releases)}</UiElement>}
            {!reads.releases.loaded ? null : releases.length ? (
              <UiElement as="div" className="table-wrap">
                <UiElement as="table">
                  <UiElement as="thead">
                    <UiElement as="tr">
                      <UiElement as="th"><UiText>{"发布 ID / 版本"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"校验状态"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"镜像"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"操作"}</UiText></UiElement>
                    </UiElement>
                  </UiElement>
                  <UiElement as="tbody">
                    {releases.map((release) => (
                      <UiElement as="tr" key={release.id}>
                        <UiElement as="td">
                          <UiElement as="b">{release.id}</UiElement>
                          {release.version && <UiElement as="span" className="cell-secondary tabular">
                            {release.version}
                          </UiElement>}
                        </UiElement>
                        <UiElement as="td">
                          <Badge status={release.status}>
                            {release.status === "verified"
                              ? "签名已验证"
                              : "已阻断"}
                          </Badge>
                          {release.errors.map((message, i) => (
                            <UiElement as="p" className="release-error" key={i}>
                              {message}
                            </UiElement>
                          ))}
                        </UiElement>
                        <UiElement as="td" className="release-image mono">
                          {release.image || <UiElement as="span" className="small-caption"><UiText>{"请核对发布清单"}</UiText></UiElement>}
                          {release.image && (
                            <Copy value={release.image} label="复制发布镜像" />
                          )}
                        </UiElement>
                        <UiElement as="td">
                          <Button actionId="action.SystemMaintenance.005"
                            disabled={
                              release.status !== "verified" || Boolean(busy)
                            }
                            loading={busy === release.id}
                            onClick={() => void makePlan(release)}
                          >
                            <UiText>{"生成升级计划"}</UiText>
                            <ArrowRight size={14} />
                          </Button>
                        </UiElement>
                      </UiElement>
                    ))}
                  </UiElement>
                </UiElement>
              </UiElement>
            ) : (
              <Empty
                title="暂无可用发布包"
                description={
                  version?.upgrade.release_directory_configured
                    ? "请由部署人员将签名发布清单放入受信发布目录后刷新。"
                    : "尚未配置受信发布公钥；请由部署人员配置发布目录与公钥。"
                }
              />
            )}
          </UiRegion>
        </>
      ) : tab === "backup" ? (
        <>
          <RecoveryArchiveStatus archive={version?.recovery_archive} />
          <InfoBox>
            <UiText>{"创建一致的控制台数据库快照并校验完整性。备份不包含部署凭据的外部密钥、模型权重或运行环境；恢复由部署主机执行。"}</UiText>
          </InfoBox>
          <UiRegion name="layout.panel" as="section" className="panel">
            <UiElement as="div" className="table-toolbar">
              <UiElement as="h2"><UiText>{"数据库备份"}</UiText></UiElement>
              <UiElement as="span" className="toolbar-right-note">
                {readCount(reads.backups, backups.length, "条记录")}
              </UiElement>
              <Button actionId="action.SystemMaintenance.006"
                kind="primary"
                disabled={Boolean(busy)}
                onClick={() => {
                  setActionError("");
                  setBackupConfirm(true);
                }}
              >
                <Archive size={14} />
                <UiText>{"创建备份"}</UiText>
              </Button>
            </UiElement>
            {!reads.backups.loaded ? <UiElement as="p" className="settings-context-note" role="status">{readNotice(reads.backups)}</UiElement> : backups.length ? (
              <UiElement as="div" className="table-wrap">
                <UiElement as="table">
                  <UiElement as="thead">
                    <UiElement as="tr">
                      <UiElement as="th"><UiText>{"备份 ID"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"创建时间"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"大小 / 数据版本"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"校验状态"}</UiText></UiElement>
                      <UiElement as="th"><UiText>{"操作"}</UiText></UiElement>
                    </UiElement>
                  </UiElement>
                  <UiElement as="tbody">
                    {backups.map((backup) => (
                      <UiElement as="tr" key={backup.id}>
                        <UiElement as="td" className="mono">
                          {backup.id}
                          <UiElement as="span" className="cell-secondary">
                            <UiText>{"SHA-256"}</UiText>{" "}
                            <UiElement as="span" title={backup.sha256}>
                              {backup.sha256.slice(0, 12)}<UiText>{"…"}</UiText>
                            </UiElement>
                            <Copy value={backup.sha256} />
                          </UiElement>
                        </UiElement>
                        <UiElement as="td" className="tabular nowrap">
                          {when(backup.created_at)}
                        </UiElement>
                        <UiElement as="td">
                          {(backup.size_bytes / 1024).toFixed(1)} <UiText>{"KB"}</UiText>
                          <UiElement as="span" className="cell-secondary">
                            <UiText>{"schema"}</UiText> {backup.schema_version}
                          </UiElement>
                        </UiElement>
                        <UiElement as="td">
                          <Badge
                            status={
                              verificationErrors[backup.id]
                                ? "failed"
                                : backup.verified
                                  ? "pass"
                                  : "unknown"
                            }
                          >
                            {verificationErrors[backup.id]
                              ? "最近校验失败"
                              : backup.verified
                                ? "创建时校验通过"
                                : "未验证"}
                          </Badge>
                        </UiElement>
                        <UiElement as="td">
                          <Button actionId="action.SystemMaintenance.007"
                            loading={busy === backup.id}
                            disabled={Boolean(busy)}
                            onClick={async (event) => {
                              verificationTrigger.current = event.currentTarget;
                              setBusy(backup.id);
                              setError("");
                              try {
                                setVerification(
                                  await post<Verification>(
                                    `/system/backups/${backup.id}/verify`,
                                  ),
                                );
                                setVerificationErrors((values) => {
                                  const next = { ...values };
                                  delete next[backup.id];
                                  return next;
                                });
                              } catch (e) {
                                const message = (e as Error).message;
                                setError(message);
                                setVerificationErrors((values) => ({
                                  ...values,
                                  [backup.id]: message,
                                }));
                              } finally {
                                setBusy("");
                              }
                            }}
                          >
                            <ShieldCheck size={14} />
                            <UiText>{"重新校验"}</UiText>
                          </Button>
                        </UiElement>
                      </UiElement>
                    ))}
                  </UiElement>
                </UiElement>
              </UiElement>
            ) : (
              <Empty
                title="暂无数据库备份"
                description="创建备份后显示快照标识、摘要和校验记录。"
              />
            )}
          </UiRegion>
        </>
      ) : (
        <UiRegion name="layout.panel" as="section" className="panel">
          <UiElement as="div" className="panel-head">
            <UiElement as="h2"><UiText>{"升级计划与执行记录"}</UiText></UiElement>
            <UiElement as="span" className="small-caption"><UiText>{"生成计划不表示升级完成"}</UiText></UiElement>
          </UiElement>
          {readNotice(reads.history) && <UiElement as="p" className="settings-context-note" role="status">{readNotice(reads.history)}</UiElement>}
          {!reads.history.loaded ? null : history.length ? (
            <UiElement as="div" className="table-wrap">
              <UiElement as="table">
                <UiElement as="thead">
                  <UiElement as="tr">
                    <UiElement as="th"><UiText>{"计划 ID"}</UiText></UiElement>
                    <UiElement as="th"><UiText>{"当前 → 目标版本"}</UiText></UiElement>
                    <UiElement as="th"><UiText>{"状态"}</UiText></UiElement>
                    <UiElement as="th"><UiText>{"创建时间"}</UiText></UiElement>
                    <UiElement as="th"><UiText>{"详情"}</UiText></UiElement>
                  </UiElement>
                </UiElement>
                <UiElement as="tbody">
                  {history.map((entry) => (
                    <UiElement as="tr" key={entry.id}>
                      <UiElement as="td" className="mono">{entry.id}</UiElement>
                      <UiElement as="td">
                        {entry.current_version} <UiText>{"→"}</UiText> {entry.target_version}
                      </UiElement>
                      <UiElement as="td">
                        <Badge status={entry.status} />
                      </UiElement>
                      <UiElement as="td" className="tabular nowrap">{when(entry.created_at)}</UiElement>
                      <UiElement as="td">
                        <UiButton actionId="action.SystemMaintenance.008"
                          className="text-link"
                          onClick={() => setPlan(entry)}
                        >
                          <UiText>{"查看计划"}</UiText>
                        </UiButton>
                      </UiElement>
                    </UiElement>
                  ))}
                </UiElement>
              </UiElement>
            </UiElement>
          ) : (
            <Empty
              title="暂无升级记录"
              description="生成升级计划后在此查看；执行结果由部署主机写入。"
            />
          )}
        </UiRegion>
      )}
      {plan && (
        <Modal
          title="平台升级计划"
          subtitle="计划校验结果；尚未表示平台已升级"
          wide
          onClose={() => setPlan(null)}
          footer={
            <>
              <Button actionId="action.SystemMaintenance.009" onClick={() => download(plan, `${plan.id}.json`)}>
                <Download size={14} />
                <UiText>{"导出计划"}</UiText>
              </Button>
              <Button actionId="action.SystemMaintenance.010" onClick={() => setPlan(null)}><UiText>{"关闭"}</UiText></Button>
            </>
          }
        >
          <UiElement as="div" className="upgrade-plan-summary">
            <UiElement as="span">{plan.current_version}</UiElement>
            <ArrowRight size={18} />
            <UiElement as="b">{plan.target_version}</UiElement>
            <Badge status={plan.status} />
            {plan.demo && <UiElement as="span" className="tag amber"><UiText>{"演示计划"}</UiText></UiElement>}
          </UiElement>
          <UiElement as="div" className="check-list">
            {plan.checks.map((check, i) => (
              <UiElement as="div" key={i}>
                <Badge status={check.status} />
                <UiElement as="div">
                  <UiElement as="b">{check.name}</UiElement>
                  <UiElement as="p">{check.detail}</UiElement>
                </UiElement>
              </UiElement>
            ))}
          </UiElement>
          {plan.error && <ErrorBox message={plan.error} />}
          <UiElement as="dl" className="description-list upgrade-plan-description">
            <UiElement as="dt"><UiText>{"计划 ID"}</UiText></UiElement>
            <UiElement as="dd" className="mono">{plan.id}</UiElement>
            <UiElement as="dt">{plan.web_image ? "API / Agent 镜像" : "目标镜像"}</UiElement>
            <UiElement as="dd" className="mono break-word">{plan.image}</UiElement>
            {plan.web_image && <>
              <UiElement as="dt"><UiText>{"Web 镜像"}</UiText></UiElement>
              <UiElement as="dd" className="mono break-word">{plan.web_image}</UiElement>
            </>}
            <UiElement as="dt"><UiText>{"发布摘要"}</UiText></UiElement>
            <UiElement as="dd" className="mono break-word">
              {plan.manifest_sha256}
              <Copy value={plan.manifest_sha256} />
            </UiElement>
          </UiElement>
          {plan.status === "planned" && plan.requires_host_execution && (
            <UiElement as="div" className="host-execution">
              <UiElement as="h3"><UiText>{"下一步：在部署主机执行"}</UiText></UiElement>
              <UiElement as="p">
                <UiText>{"核对计划后，由部署人员将命令中的项目与数据目录替换为实际绝对路径。主机工具负责停机、备份、切换与结果记录；此页面不会执行命令。"}</UiText>
              </UiElement>
              {plan.backup_required && (
                <UiElement as="p"><UiText>{"执行前必须具备可验证备份，并保留与数据库对应的外部密钥。"}</UiText></UiElement>
              )}
              <UiElement as="div">
                <UiElement as="pre">{plan.host_command}</UiElement>
                <Copy value={plan.host_command} label="复制主机执行命令" />
              </UiElement>
            </UiElement>
          )}
          {plan.status === "blocked" && (
            <InfoBox>
              <UiText>{"计划存在阻断项，不能执行升级。处理检查结果后重新生成计划。"}</UiText>
            </InfoBox>
          )}
          {plan.execution && (
            <UiElement as="details" className="command-preview" open>
              <UiElement as="summary"><UiText>{"主机执行记录"}</UiText></UiElement>
              <UiElement as="pre">{JSON.stringify(plan.execution, null, 2)}</UiElement>
            </UiElement>
          )}
        </Modal>
      )}
      {backupConfirm && (
        <Modal
          title="创建控制台数据库备份"
          onClose={() => setBackupConfirm(false)}
          closeDisabled={Boolean(busy)}
          footer={
            <>
              <Button actionId="action.SystemMaintenance.011"
                disabled={Boolean(busy)}
                onClick={() => setBackupConfirm(false)}
              >
                <UiText>{"取消"}</UiText>
              </Button>
              <Button actionId="action.SystemMaintenance.012"
                kind="primary"
                loading={busy === "backup"}
                onClick={async () => {
                  setBusy("backup");
                  setActionError("");
                  try {
                    await post<Backup>("/system/backups");
                    await load();
                    setBackupConfirm(false);
                  } catch (e) {
                    setActionError((e as Error).message);
                  } finally {
                    setBusy("");
                  }
                }}
              >
                <UiText>{"创建并校验备份"}</UiText>
              </Button>
            </>
          }
        >
          {actionError && <ErrorBox message={actionError} />}
          <UiElement as="p">
            <UiText>{"将对当前控制台数据库创建一致性快照并进行完整性校验，保存在部署主机的数据目录。不会停止推理服务或备份模型权重。"}</UiText>
          </UiElement>
          <UiElement as="p" className="maintenance-note">
            <UiText>{"密钥、证书和其他外部凭据需由部署人员单独保留；数据库快照不能替代完整恢复演练。"}</UiText>
          </UiElement>
        </Modal>
      )}
      {verification && (
        <Modal
          title="数据库备份校验结果"
          onClose={() => setVerification(null)}
        >
          <UiElement as="div" className="verification-heading">
            <CheckCircle2 size={22} />
            <UiElement as="h3">{verification.verified ? "校验通过" : "校验未通过"}</UiElement>
          </UiElement>
          <UiElement as="dl" className="description-list">
            <UiElement as="dt"><UiText>{"备份 ID"}</UiText></UiElement>
            <UiElement as="dd" className="mono">{verification.id}</UiElement>
            <UiElement as="dt"><UiText>{"校验时间"}</UiText></UiElement>
            <UiElement as="dd">{when(verification.verified_at)}</UiElement>
            <UiElement as="dt"><UiText>{"数据库版本"}</UiText></UiElement>
            <UiElement as="dd">{verification.schema_version}</UiElement>
            <UiElement as="dt"><UiText>{"SHA-256"}</UiText></UiElement>
            <UiElement as="dd" className="mono break-word">{verification.sha256}</UiElement>
          </UiElement>
        </Modal>
      )}
    </>
  );
}

function DefaultRecoveryArchiveStatus({
  archive,
}: {
  archive: Version["recovery_archive"];
}) {
  const status = archive?.status ?? "unavailable";
  const labels: Record<string, string> = {
    not_configured: "未配置",
    fresh: "近期备份成功",
    stale: "备份已过期",
    failed: "最近备份失败",
    invalid: "状态记录无效",
    unavailable: "尚未获取状态",
  };
  const badgeStatus =
    status === "fresh"
      ? "verified"
      : status === "stale"
        ? "pending"
        : ["failed", "invalid"].includes(status)
          ? "failed"
          : "unknown";
  return (
    <UiRegion name="layout.panel" as="section"
      className="panel recovery-archive-status"
      aria-label="加密恢复包状态"
    >
      <UiElement as="div" className="panel-head">
        <UiElement as="h2"><UiText>{"加密恢复包状态"}</UiText></UiElement>
        <Badge status={badgeStatus}>{labels[status] ?? "需要检查"}</Badge>
      </UiElement>
      <UiElement as="p" className="recovery-archive-description">
        <UiText>{"由部署主机创建，可配置定时任务，包含控制台数据库、所选密钥与配置。下方数据库快照单独管理； Agent 状态、模型权重、镜像和宿主环境需另行保留。"}</UiText>
      </UiElement>
      {archive?.configured ? (
        <UiElement as="dl" className="system-version-grid">
          {archive.last_success_at && <UiElement as="div">
            <UiElement as="dt"><UiText>{"最近成功时间"}</UiText></UiElement>
            <UiElement as="dd">{when(archive.last_success_at)}</UiElement>
          </UiElement>}
          <UiElement as="div">
            <UiElement as="dt"><UiText>{"存储位置核验"}</UiText></UiElement>
            <UiElement as="dd">
              {archive.off_host_verified ? "网络挂载已核对" : "未核对网络挂载"}
            </UiElement>
          </UiElement>
          {archive.encrypted && <UiElement as="div">
            <UiElement as="dt"><UiText>{"加密状态"}</UiText></UiElement>
            <UiElement as="dd"><UiText>{"已加密"}</UiText></UiElement>
          </UiElement>}
          {typeof archive.size_bytes === "number" && Number.isFinite(archive.size_bytes) && archive.size_bytes >= 0 && <UiElement as="div">
            <UiElement as="dt"><UiText>{"最近成功包大小"}</UiText></UiElement>
            <UiElement as="dd">{(archive.size_bytes / 1024 / 1024).toFixed(2)} <UiText>{"MiB"}</UiText></UiElement>
          </UiElement>}
          {archive.sha256 && (
            <UiElement as="div" className="system-version-wide">
              <UiElement as="dt"><UiText>{"SHA-256"}</UiText></UiElement>
              <UiElement as="dd" className="mono recovery-archive-hash">
                {archive.sha256}
                <Copy value={archive.sha256} />
              </UiElement>
            </UiElement>
          )}
        </UiElement>
      ) : (
        <UiElement as="p" className="muted-text recovery-archive-description">
          {archive
            ? "请由部署人员配置独立加密密钥、备份目的地和定时任务；当前没有可用的加密恢复包记录。"
            : "版本信息读取后将显示实际备份状态。"}
        </UiElement>
      )}
      <UiElement as="p" className="muted-text recovery-archive-description">
        <UiText>{"此状态来自最近一次备份记录；恢复能力需通过独立目录中的恢复演练确认。"}</UiText>
      </UiElement>
    </UiRegion>
  );
}

const SystemMaintenance = withUiSlot("component.SystemMaintenance", DefaultSystemMaintenance);
export default SystemMaintenance;
const RecoveryArchiveStatus = withUiSlot("component.RecoveryArchiveStatus", DefaultRecoveryArchiveStatus);

// Reference package implementation exports.
export { DefaultSystemMaintenance, DefaultRecoveryArchiveStatus };
