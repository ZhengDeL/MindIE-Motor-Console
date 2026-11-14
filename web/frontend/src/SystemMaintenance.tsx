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

export default function SystemMaintenance() {
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
      <div className="maintenance-toolbar">
        <span>平台升级由部署主机执行，Web 负责校验、生成计划和查询记录。</span>
        <Button loading={loading} onClick={() => void load()}>
          <RefreshCw size={14} />
          刷新
        </Button>
      </div>
      {error && <ErrorBox message={error} />}
      <div className="detail-tab-bar">
        <button
          className={tab === "upgrade" ? "active" : ""}
          onClick={() => setTab("upgrade")}
        >
          <GitBranch size={14} />
          版本与升级
        </button>
        <button
          className={tab === "backup" ? "active" : ""}
          onClick={() => setTab("backup")}
        >
          <Archive size={14} />
          备份与校验
        </button>
        <button
          className={tab === "history" ? "active" : ""}
          onClick={() => setTab("history")}
        >
          <ShieldCheck size={14} />
          升级记录
        </button>
      </div>
      {loading && !version && tab === "upgrade" && !reads.version.error ? (
        <Spinner />
      ) : tab === "upgrade" ? (
        <>
          <section className="panel maintenance-version">
            <div className="panel-head">
              <h2>当前系统</h2>
              {version?.demo && <span className="tag amber">演示模式</span>}
            </div>
            {version ? <dl className="system-version-grid">
              <div>
                <dt>控制台版本</dt>
                <dd>{version.console.version}</dd>
              </div>
              <div>
                <dt>Agent 协议</dt>
                <dd>{version.console.agent_protocol}</dd>
              </div>
              <div>
                <dt>数据库</dt>
                <dd>
                  {version.storage.engine} · schema {version.storage.schema_version}
                </dd>
              </div>
              <div>
                <dt>Motor 兼容配置</dt>
                <dd>{version.motor.profile_id}</dd>
              </div>
              <div className="system-version-wide">
                <dt>Motor 提交</dt>
                <dd className="mono">
                  {version.motor.commit}
                  {version?.motor.commit && (
                    <Copy value={version.motor.commit} />
                  )}
                </dd>
              </div>
            </dl> : <Empty title="系统信息未读取" description="请刷新系统信息，核对连接状态后再规划升级。" />}
          </section>
          <InfoBox>
            升级前需核对发布签名、版本方向、Agent
            协议与数据库兼容性；执行时需要停止控制台并完成备份。平台升级不自动升级
            Motor 推理工作负载。
          </InfoBox>
          <section className="panel">
            <div className="panel-head">
              <h2>发布候选</h2>
              <span className="small-caption">
                由部署人员预先放入受信发布目录
              </span>
            </div>
            {readNotice(reads.releases) && <p className="settings-context-note" role="status">{readNotice(reads.releases)}</p>}
            {!reads.releases.loaded ? null : releases.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>发布 ID / 版本</th>
                      <th>校验状态</th>
                      <th>镜像</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {releases.map((release) => (
                      <tr key={release.id}>
                        <td>
                          <b>{release.id}</b>
                          {release.version && <span className="cell-secondary tabular">
                            {release.version}
                          </span>}
                        </td>
                        <td>
                          <Badge status={release.status}>
                            {release.status === "verified"
                              ? "签名已验证"
                              : "已阻断"}
                          </Badge>
                          {release.errors.map((message, i) => (
                            <p className="release-error" key={i}>
                              {message}
                            </p>
                          ))}
                        </td>
                        <td className="release-image mono">
                          {release.image || <span className="small-caption">请核对发布清单</span>}
                          {release.image && (
                            <Copy value={release.image} label="复制发布镜像" />
                          )}
                        </td>
                        <td>
                          <Button
                            disabled={
                              release.status !== "verified" || Boolean(busy)
                            }
                            loading={busy === release.id}
                            onClick={() => void makePlan(release)}
                          >
                            生成升级计划
                            <ArrowRight size={14} />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
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
          </section>
        </>
      ) : tab === "backup" ? (
        <>
          <RecoveryArchiveStatus archive={version?.recovery_archive} />
          <InfoBox>
            创建一致的控制台数据库快照并校验完整性。备份不包含部署凭据的外部密钥、模型权重或运行环境；恢复由部署主机执行。
          </InfoBox>
          <section className="panel">
            <div className="table-toolbar">
              <h2>数据库备份</h2>
              <span className="toolbar-right-note">
                {readCount(reads.backups, backups.length, "条记录")}
              </span>
              <Button
                kind="primary"
                disabled={Boolean(busy)}
                onClick={() => {
                  setActionError("");
                  setBackupConfirm(true);
                }}
              >
                <Archive size={14} />
                创建备份
              </Button>
            </div>
            {!reads.backups.loaded ? <p className="settings-context-note" role="status">{readNotice(reads.backups)}</p> : backups.length ? (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>备份 ID</th>
                      <th>创建时间</th>
                      <th>大小 / 数据版本</th>
                      <th>校验状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {backups.map((backup) => (
                      <tr key={backup.id}>
                        <td className="mono">
                          {backup.id}
                          <span className="cell-secondary">
                            SHA-256{" "}
                            <span title={backup.sha256}>
                              {backup.sha256.slice(0, 12)}…
                            </span>
                            <Copy value={backup.sha256} />
                          </span>
                        </td>
                        <td className="tabular nowrap">
                          {when(backup.created_at)}
                        </td>
                        <td>
                          {(backup.size_bytes / 1024).toFixed(1)} KB
                          <span className="cell-secondary">
                            schema {backup.schema_version}
                          </span>
                        </td>
                        <td>
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
                        </td>
                        <td>
                          <Button
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
                            重新校验
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty
                title="暂无数据库备份"
                description="创建备份后显示快照标识、摘要和校验记录。"
              />
            )}
          </section>
        </>
      ) : (
        <section className="panel">
          <div className="panel-head">
            <h2>升级计划与执行记录</h2>
            <span className="small-caption">生成计划不表示升级完成</span>
          </div>
          {readNotice(reads.history) && <p className="settings-context-note" role="status">{readNotice(reads.history)}</p>}
          {!reads.history.loaded ? null : history.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>计划 ID</th>
                    <th>当前 → 目标版本</th>
                    <th>状态</th>
                    <th>创建时间</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((entry) => (
                    <tr key={entry.id}>
                      <td className="mono">{entry.id}</td>
                      <td>
                        {entry.current_version} → {entry.target_version}
                      </td>
                      <td>
                        <Badge status={entry.status} />
                      </td>
                      <td className="tabular nowrap">{when(entry.created_at)}</td>
                      <td>
                        <button
                          className="text-link"
                          onClick={() => setPlan(entry)}
                        >
                          查看计划
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="暂无升级记录"
              description="生成升级计划后在此查看；执行结果由部署主机写入。"
            />
          )}
        </section>
      )}
      {plan && (
        <Modal
          title="平台升级计划"
          subtitle="计划校验结果；尚未表示平台已升级"
          wide
          onClose={() => setPlan(null)}
          footer={
            <>
              <Button onClick={() => download(plan, `${plan.id}.json`)}>
                <Download size={14} />
                导出计划
              </Button>
              <Button onClick={() => setPlan(null)}>关闭</Button>
            </>
          }
        >
          <div className="upgrade-plan-summary">
            <span>{plan.current_version}</span>
            <ArrowRight size={18} />
            <b>{plan.target_version}</b>
            <Badge status={plan.status} />
            {plan.demo && <span className="tag amber">演示计划</span>}
          </div>
          <div className="check-list">
            {plan.checks.map((check, i) => (
              <div key={i}>
                <Badge status={check.status} />
                <div>
                  <b>{check.name}</b>
                  <p>{check.detail}</p>
                </div>
              </div>
            ))}
          </div>
          {plan.error && <ErrorBox message={plan.error} />}
          <dl className="description-list upgrade-plan-description">
            <dt>计划 ID</dt>
            <dd className="mono">{plan.id}</dd>
            <dt>{plan.web_image ? "API / Agent 镜像" : "目标镜像"}</dt>
            <dd className="mono break-word">{plan.image}</dd>
            {plan.web_image && <>
              <dt>Web 镜像</dt>
              <dd className="mono break-word">{plan.web_image}</dd>
            </>}
            <dt>发布摘要</dt>
            <dd className="mono break-word">
              {plan.manifest_sha256}
              <Copy value={plan.manifest_sha256} />
            </dd>
          </dl>
          {plan.status === "planned" && plan.requires_host_execution && (
            <div className="host-execution">
              <h3>下一步：在部署主机执行</h3>
              <p>
                核对计划后，由部署人员将命令中的项目与数据目录替换为实际绝对路径。主机工具负责停机、备份、切换与结果记录；此页面不会执行命令。
              </p>
              {plan.backup_required && (
                <p>执行前必须具备可验证备份，并保留与数据库对应的外部密钥。</p>
              )}
              <div>
                <pre>{plan.host_command}</pre>
                <Copy value={plan.host_command} label="复制主机执行命令" />
              </div>
            </div>
          )}
          {plan.status === "blocked" && (
            <InfoBox>
              计划存在阻断项，不能执行升级。处理检查结果后重新生成计划。
            </InfoBox>
          )}
          {plan.execution && (
            <details className="command-preview" open>
              <summary>主机执行记录</summary>
              <pre>{JSON.stringify(plan.execution, null, 2)}</pre>
            </details>
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
              <Button
                disabled={Boolean(busy)}
                onClick={() => setBackupConfirm(false)}
              >
                取消
              </Button>
              <Button
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
                创建并校验备份
              </Button>
            </>
          }
        >
          {actionError && <ErrorBox message={actionError} />}
          <p>
            将对当前控制台数据库创建一致性快照并进行完整性校验，保存在部署主机的数据目录。不会停止推理服务或备份模型权重。
          </p>
          <p className="maintenance-note">
            密钥、证书和其他外部凭据需由部署人员单独保留；数据库快照不能替代完整恢复演练。
          </p>
        </Modal>
      )}
      {verification && (
        <Modal
          title="数据库备份校验结果"
          onClose={() => setVerification(null)}
        >
          <div className="verification-heading">
            <CheckCircle2 size={22} />
            <h3>{verification.verified ? "校验通过" : "校验未通过"}</h3>
          </div>
          <dl className="description-list">
            <dt>备份 ID</dt>
            <dd className="mono">{verification.id}</dd>
            <dt>校验时间</dt>
            <dd>{when(verification.verified_at)}</dd>
            <dt>数据库版本</dt>
            <dd>{verification.schema_version}</dd>
            <dt>SHA-256</dt>
            <dd className="mono break-word">{verification.sha256}</dd>
          </dl>
        </Modal>
      )}
    </>
  );
}

function RecoveryArchiveStatus({
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
    <section
      className="panel recovery-archive-status"
      aria-label="加密恢复包状态"
    >
      <div className="panel-head">
        <h2>加密恢复包状态</h2>
        <Badge status={badgeStatus}>{labels[status] ?? "需要检查"}</Badge>
      </div>
      <p className="recovery-archive-description">
        由部署主机创建，可配置定时任务，包含控制台数据库、所选密钥与配置。下方数据库快照单独管理；
        Agent 状态、模型权重、镜像和宿主环境需另行保留。
      </p>
      {archive?.configured ? (
        <dl className="system-version-grid">
          {archive.last_success_at && <div>
            <dt>最近成功时间</dt>
            <dd>{when(archive.last_success_at)}</dd>
          </div>}
          <div>
            <dt>存储位置核验</dt>
            <dd>
              {archive.off_host_verified ? "网络挂载已核对" : "未核对网络挂载"}
            </dd>
          </div>
          {archive.encrypted && <div>
            <dt>加密状态</dt>
            <dd>已加密</dd>
          </div>}
          {typeof archive.size_bytes === "number" && Number.isFinite(archive.size_bytes) && archive.size_bytes >= 0 && <div>
            <dt>最近成功包大小</dt>
            <dd>{(archive.size_bytes / 1024 / 1024).toFixed(2)} MiB</dd>
          </div>}
          {archive.sha256 && (
            <div className="system-version-wide">
              <dt>SHA-256</dt>
              <dd className="mono recovery-archive-hash">
                {archive.sha256}
                <Copy value={archive.sha256} />
              </dd>
            </div>
          )}
        </dl>
      ) : (
        <p className="muted-text recovery-archive-description">
          {archive
            ? "请由部署人员配置独立加密密钥、备份目的地和定时任务；当前没有可用的加密恢复包记录。"
            : "版本信息读取后将显示实际备份状态。"}
        </p>
      )}
      <p className="muted-text recovery-archive-description">
        此状态来自最近一次备份记录；恢复能力需通过独立目录中的恢复演练确认。
      </p>
    </section>
  );
}
