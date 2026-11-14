import { useEffect, useMemo, useRef, useState } from "react";
import { usePollingQuery } from "./usePollingQuery";
import { readNotice } from "./readState";
import KubernetesEndpoints from "./KubernetesEndpoints";
import { RuntimeLogFilters, RuntimeLogLine, logPath, matchesLog, type LogFilters, type LogResponse } from "./RuntimeLogs";
import { useRefreshPreference } from "./refreshPreferences";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Box,
  Braces,
  CheckCircle2,
  Clock3,
  ChevronRight,
  Download,
  FileClock,
  FileJson,
  Layers3,
  Play,
  RefreshCw,
  Send,
  Settings2,
  SlidersHorizontal,
  Square,
  TerminalSquare,
  Trash2,
} from "./icons";
import { api, download, executionReadOnlyReason, isUnsubmittedDraft, roleName, when } from "./api";
import type {
  InferenceResult,
  Log,
  NodeInfo,
  Observations,
  Revision,
  Service,
} from "./api";
import { Badge, Button, Copy, Empty, ErrorBox, InfoBox, Spinner } from "./ui";
import NativeAigwModelsPanel from "./IntegrationModels";
import InferenceResponse from "./InferenceResponse";
import ServiceMetricsPanel from "./ServiceMetricsPanel";
import "./service-detail-feedback.css";
import { knownText } from "./resourcePresentation";
import { isResourceInteger } from "./resourceObservation";
import { recordTime } from "./operationRecords";
import { usePluginEnabled, useRuntimeAccess } from "./consolePlugins";
import {
  serviceInstanceSummary,
  serviceReplicaPlan,
} from "./servicePresentation";

export default function ServiceDetail({
  service,
  nodes,
  onBack,
  onEdit,
  onAction,
  notify,
  canOperate,
  stopChecking = false,
}: {
  service: Service;
  nodes: NodeInfo[];
  onBack: () => void;
  onEdit: (s: Service) => void;
  onAction: (s: Service, action: string) => void;
  notify: (message: string) => void;
  canOperate: boolean;
  stopChecking?: boolean;
}) {
  const logsEnabled = usePluginEnabled("runtime-logs");
  const templatesEnabled = usePluginEnabled("templates");
  const gatewaysEnabled = usePluginEnabled("gateways");
  const runtimeAccess = useRuntimeAccess(service.runtime);
  const [tab, setTab] = useState("overview");
  const [tabNotice, setTabNotice] = useState("");
  const readOnlyReason = runtimeAccess.reason || executionReadOnlyReason(service, nodes);
  const canExecute = canOperate && !readOnlyReason;
  const continueStop = service.runtime === "kubernetes" && service.status === "stopping";
  const [logs, setLogs] = useState<Log[]>([]);
  const [observedState, setObservations] = useState<Observations | null>(null);
  const observations = runtimeAccess.enabled ? observedState : null;
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [error, setError] = useState("");
  const [configTab, setConfigTab] = useState("user");
  const [prompt, setPrompt] = useState("你好，请介绍一下你自己。");
  const [maxTokens, setMaxTokens] = useState(128);
  const [answer, setAnswer] = useState<InferenceResult | null>(null);
  const [inferBusy, setInferBusy] = useState(false);
  const [autoLog, setAutoLog] = useState(false);
  const [logQuery, setLogQuery] = useState("");
  const [logFilters, setLogFilters] = useState<LogFilters>({ serviceId: "", nodeId: "", role: "" });
  const [logPartial, setLogPartial] = useState(false);
  const logNode = logFilters.serviceId === service.id ? logFilters.nodeId : "";
  const logRole = logFilters.serviceId === service.id ? logFilters.role : "";
  const inference = useRef<AbortController | null>(null);
  useEffect(() => () => { inference.current?.abort(); inference.current = null; }, [service.id]);
  const [refreshInterval] = useRefreshPreference();
  const unsubmitted = isUnsubmittedDraft(service);
  const { refresh: reload, refreshing: loading, hasSuccess } = usePollingQuery(
    `service-detail:${service.id}:${tab}:${canOperate}:${runtimeAccess.enabled}:${unsubmitted}:${tab === "logs" ? `${logNode}:${logRole}` : ""}`,
    async (signal): Promise<{ logs?: LogResponse; revisions?: Revision[]; observations?: Observations }> => {
      if (tab === "logs") {
        if (unsubmitted) return { logs: { items: [] } };
        return { logs: await api<LogResponse>(logPath(service.id, 300, logNode, logRole), { signal }) };
      }
      if (tab === "revisions") {
        const result = await api<{ items: Revision[] }>(`/services/${service.id}/revisions`, { signal });
        return { revisions: result.items };
      }
      return { observations: await api<Observations>(`/services/${service.id}/observations`, { signal }) };
    }, {
      enabled: ["overview", "logs", "revisions", "playground"].includes(tab) && (tab === "revisions" || runtimeAccess.enabled) && (tab !== "logs" || logsEnabled) &&
        (canOperate || !["revisions", "playground"].includes(tab)),
      intervalMs: tab === "logs" ? (autoLog && !unsubmitted ? 4000 : 0) : tab === "overview" ? refreshInterval : 0,
      onSuccess: (result) => {
        setError("");
        if (result.logs !== undefined) { setLogs(result.logs.items); setLogPartial(result.logs.partial === true); }
        if (result.revisions !== undefined) setRevisions(result.revisions);
        if (result.observations !== undefined) setObservations(result.observations);
      },
      onError: (failure) => {
        setError(failure.message);
        // A failed readiness read must not continue to present an earlier green verdict.
        if (["overview", "playground"].includes(tab)) setObservations(null);
      },
    },
  );
  useEffect(() => {
    if (!canOperate && ["config", "revisions", "playground"].includes(tab)) setTab("overview");
  }, [canOperate, tab]);
  useEffect(() => {
    const revoked = tab === "aigw" && !gatewaysEnabled || tab === "logs" && !logsEnabled
      || ["aigw", "logs", "monitor", "playground"].includes(tab) && !runtimeAccess.enabled;
    if (revoked) {
      setTab("overview");
      setTabNotice("当前页签所需插件已不可用，已返回概览并暂停读取。本页消息输入与日志筛选仍保留。");
    }
    if (!runtimeAccess.enabled) { inference.current?.abort(); setObservations(null); }
  }, [tab, gatewaysEnabled, logsEnabled, runtimeAccess.enabled]);
  useEffect(() => {
    setLogs([]); setLogPartial(false); setObservations(null); setRevisions([]); setAnswer(null); setError("");
  }, [service.id]);
  const exportService = async () => {
    try {
      download(
        await api(`/services/${service.id}/export`),
        `${service.name}.json`,
      );
      notify("配置已导出");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const runInference = async () => {
    if (!canExecute) { setError(readOnlyReason || "当前会话没有执行权限"); return; }
    if (inference.current || !Number.isSafeInteger(maxTokens) || maxTokens < 1 || maxTokens > 8192) {
      if (!inference.current) setError("最大生成 Token 须为 1–8192 的整数");
      return;
    }
    const controller = new AbortController();
    inference.current = controller;
    setError("");
    setInferBusy(true);
    setAnswer(null);
    try {
      const result = await api<InferenceResult>(`/services/${service.id}/inference`, {
          method: "POST", signal: controller.signal,
          body: JSON.stringify({ prompt, max_tokens: maxTokens }),
        });
      if (!controller.signal.aborted && inference.current === controller) setAnswer(result);
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      if (inference.current === controller) { inference.current = null; setInferBusy(false); }
    }
  };
  const tabs = [
    ["overview", "概览", Box],
    ["topology", "实例拓扑", Layers3],
    ["config", "服务配置", Braces],
    ["revisions", "配置修订", FileClock],
    ["logs", "运行日志", TerminalSquare],
    ["monitor", "监控指标", Activity],
    ["aigw", "AIGW 模型发现", Layers3],
    ["playground", "推理调试", Send],
  ] as const;
  const instanceSummary = useMemo(
    () => serviceInstanceSummary(observations),
    [observations?.instances],
  );
  const readAt = recordTime(observations?.observed_at);
  const readiness =
    typeof observations?.readiness?.ready === "boolean"
      ? observations.readiness.ready
      : null;
  const readinessGate = observations?.readiness_gate;
  const targetReady = readinessGate?.state === "ready" && readinessGate.ready === true;
  const nativeReady = readinessGate ? readinessGate.native_ready === true : readiness;
  const servingReady = readinessGate?.serving_ready === true;
  const liveStatus = ["running", "starting", "partial"].includes(service.status);
  const displayedStatus = readinessGate && liveStatus
    ? targetReady ? "running" : readinessGate.state === "degraded" ? "partial" : "starting"
    : service.status;
  const readinessTitle = targetReady
    ? "目标实例已全部就绪"
    : readinessGate?.state === "degraded"
      ? "部分运行资源异常"
      : servingReady
        ? readinessGate?.state === "unknown" ? "已有实例可用，目标容量待确认" : "已有实例可用，目标容量未齐"
        : nativeReady === true
          ? readinessGate?.state === "unknown" ? "Coordinator 已就绪，目标容量待确认" : "Coordinator 已就绪，推理实例仍在准备"
          : readinessGate?.state === "starting" ? "目标实例正在准备" : "整体就绪状态待确认";
  const capacityRoles = [
    { key: "encode", label: "Encode" },
    { key: "prefill", label: "Prefill" },
    { key: "decode", label: "Decode" },
    { key: "union", label: "混部" },
  ] as const;
  const configs = service.user_config.motor_deploy_config ?? {};
  const replicaPlan = serviceReplicaPlan(service);
  const hasPortPlan = service.placements.some((placement) =>
    Object.values(placement.ports ?? {}).some(
      (value) => isResourceInteger(value) && value > 0 && value <= 65535,
    ),
  );
  const dockerRecoveryPending =
    service.runtime === "docker" &&
    service.status === "error" &&
    service.resources_may_exist === true;
  return (
    <>
      <button className="back-link" onClick={onBack}>
        <ArrowLeft size={14} />
        返回推理服务
      </button>
      <div className="page-heading service-detail-heading">
        <div>
          <div className="heading-with-icon">
            <div className="service-icon large">
              <Box size={27} />
            </div>
            <div>
              <div className="title-row">
                <h1>{service.name}</h1>
                <Badge status={displayedStatus}>
                  {readinessGate && liveStatus && !targetReady && servingReady
                    ? readinessGate.state === "degraded" ? "部分资源异常" : readinessGate.state === "unknown" ? "容量待确认" : "容量未齐"
                    : undefined}
                </Badge>
              </div>
              <p>
                {knownText(service.model) && (
                  <>
                    {service.model}
                    <span className="inline-divider" />
                  </>
                )}
                {service.runtime === "kubernetes" ? "Kubernetes" : "Docker"}
                {knownText(service.engine) && (
                  <>
                    <span className="inline-divider" />
                    {service.engine}
                  </>
                )}
              </p>
            </div>
          </div>
        </div>
        <div className="heading-actions">
          {service.runtime === "kubernetes" && (
            <Button
              disabled={
                !canExecute ||
                !service.resources_may_exist ||
                ![
                  "running",
                  "error",
                  "starting",
                  "stopped",
                  "partial",
                ].includes(service.status)
              }
              title={readOnlyReason || "仅移除本服务受管部署，保留 Namespace、PVC、权重与配置"}
              onClick={() => onAction(service, "undeploy")}
            >
              <Trash2 size={14} />
              卸载部署
            </Button>
          )}
          {service.runtime === "kubernetes" && (
            <Button
              disabled={
                !canExecute ||
                !["running", "starting", "partial"].includes(service.status)
              }
              title={readOnlyReason || "先预览原生实例数调整计划，再确认执行"}
              onClick={() => onAction(service, "scale")}
            >
              <SlidersHorizontal size={14} />
              调整实例数
            </Button>
          )}
          <Button
            disabled={!canOperate || service.config_redacted}
            onClick={exportService}
          >
            <Download size={14} />
            导出配置
          </Button>
          {templatesEnabled && <Button
            disabled={!canOperate || service.config_redacted}
            onClick={() => onAction(service, "template")}
          >
            <FileJson size={14} />
            保存为模板
          </Button>}
          <Button
            onClick={() => onEdit(service)}
            disabled={
              !canOperate || !runtimeAccess.enabled || !["draft", "stopped"].includes(service.status)
            }
          >
            <Settings2 size={14} />
            编辑
          </Button>
          {[
            "running",
            "starting",
            "partial",
            "deploying",
            "stopping",
            "restarting",
            "scaling",
            "removing",
          ].includes(service.status) || dockerRecoveryPending ? (
            <>
              <Button
                disabled={
                  !canExecute ||
                  dockerRecoveryPending ||
                  service.needs_rebuild ||
                  [
                    "stopping",
                    "deploying",
                    "restarting",
                    "scaling",
                    "removing",
                  ].includes(service.status)
                }
                title={
                  readOnlyReason || (dockerRecoveryPending
                    ? "部署未完成，请先停止受管资源后重建"
                    : service.needs_rebuild
                      ? "请先重建并应用配置"
                      : undefined)
                }
                onClick={() => onAction(service, "restart")}
              >
                <RefreshCw size={14} />
                重启
              </Button>
              <Button
                disabled={
                  !canExecute ||
                  (continueStop && stopChecking) ||
                  [
                    "deploying",
                    "restarting",
                    "scaling",
                    "removing",
                  ].includes(service.status) || (service.status === "stopping" && !continueStop)
                }
                loading={continueStop && stopChecking}
                onClick={() => onAction(service, "stop")}
                title={readOnlyReason || undefined}
              >
                <Square size={14} />
                {continueStop
                  ? "继续停止"
                  : service.status === "stopping"
                  ? "停止中"
                  : dockerRecoveryPending
                    ? "停止受管资源"
                    : "停止"}
              </Button>
            </>
          ) : (
            <Button
              kind="primary"
              disabled={!canExecute}
              title={readOnlyReason || undefined}
              onClick={() => onAction(service, "deploy")}
            >
              <Play size={14} />
              {service.needs_rebuild ? "重建并应用配置" : "部署服务"}
            </Button>
          )}
        </div>
      </div>
      <div className="service-subheading">
        <span>
          服务 ID：<code>{service.id}</code>
          <Copy value={service.id} />
        </span>
        {recordTime(service.created_at) && (
          <span>创建时间：{when(service.created_at)}</span>
        )}
        {recordTime(service.updated_at) && (
          <span>最近更新：{when(service.updated_at)}</span>
        )}
        <Button
          kind="text"
          disabled={!service.last_job_id}
          title={service.last_job_id ? "读取已保存的最近任务" : "尚无部署任务"}
          onClick={() => onAction(service, "job")}
        >
          <FileClock size={13} />
          查看最近任务
        </Button>
      </div>
      <div className="detail-tab-bar">
        {tabs
          .filter(
            ([id]) =>
              (id !== "logs" || logsEnabled) && (id !== "aigw" || gatewaysEnabled)
              && (!["logs", "aigw", "monitor", "playground"].includes(id) || runtimeAccess.enabled)
              && (canOperate || !["config", "revisions", "playground"].includes(id)),
          )
          .map(([id, label, Icon]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => {
                setTab(id);
                setTabNotice("");
                setError("");
              }}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
      </div>
      {error && <ErrorBox message={error} />}
      {readOnlyReason && <InfoBox>{readOnlyReason}</InfoBox>}
      {tabNotice && <InfoBox>{tabNotice}</InfoBox>}
      {service.last_error && <ErrorBox message={service.last_error} />}
      {tab === "aigw" && gatewaysEnabled && runtimeAccess.enabled && <NativeAigwModelsPanel service={service} />}
      {dockerRecoveryPending && (
        <InfoBox>
          部署未完成，但可能已有受管资源。请先停止这些资源，确认停止后再重建并应用配置。
        </InfoBox>
      )}
      {tab === "overview" && (
        <div className="detail-overview-grid">
          <section className="panel">
            <div className="panel-head">
              <h2>服务信息</h2>
              <span className="small-caption">声明配置</span>
            </div>
            <dl className="description-list">
              <dt>运行环境</dt>
              <dd>
                {service.runtime === "kubernetes"
                  ? "Kubernetes 集群"
                  : "多节点 Docker"}
              </dd>
              {knownText(configs.deploy_mode) && (
                <>
                  <dt>部署模式</dt>
                  <dd>
                    {configs.deploy_mode}
                    <span className="tag small">
                      {service.scenario === "hybrid" ? "PD 混部" : "PD 分离"}
                    </span>
                  </dd>
                </>
              )}
              {knownText(service.engine) && (
                <>
                  <dt>推理引擎</dt>
                  <dd>{service.engine}</dd>
                </>
              )}
              {knownText(service.motor_profile) && (
                <>
                  <dt>引擎适配配置</dt>
                  <dd className="mono">{service.motor_profile}</dd>
                </>
              )}
              {knownText(configs.hardware_type) && (
                <>
                  <dt>硬件规格</dt>
                  <dd>{configs.hardware_type}</dd>
                </>
              )}
              {!!replicaPlan.length && (
                <>
                  <dt>实例规划</dt>
                  <dd>
                    {replicaPlan.filter((item) => item.value > 0).length
                      ? replicaPlan
                          .filter((item) => item.value > 0)
                          .map((item) => (
                            <span
                              key={item.key}
                              className="service-replica-item"
                            >
                              <span className={`role-bullet ${item.key}`} />
                              {item.value} {item.label}
                            </span>
                          ))
                      : "0 个实例"}
                  </dd>
                </>
              )}
              {isResourceInteger(service.device_count) && (
                <>
                  <dt>NPU 逻辑设备</dt>
                  <dd>{service.device_count} 个</dd>
                </>
              )}
              {!!service.node_names?.length && (
                <>
                  <dt>部署节点</dt>
                  <dd>{service.node_names.join("、")}</dd>
                </>
              )}
              {knownText(service.image) && (
                <>
                  <dt>服务镜像</dt>
                  <dd className="mono break-word">{service.image}</dd>
                </>
              )}
              {knownText(configs.weight_mount_path) && (
                <>
                  <dt>权重目录</dt>
                  <dd className="mono">{configs.weight_mount_path}</dd>
                </>
              )}
            </dl>
          </section>
          <div className="detail-overview-side">
            {service.runtime === "kubernetes" ? runtimeAccess.enabled && <KubernetesEndpoints service={service} /> : <section className="panel">
              <div className="panel-head">
                <h2>访问入口</h2>
                <ArrowUpRight size={16} />
              </div>
              {knownText(service.endpoint) ? (
                <>
                  <div className="endpoint-box">
                    <code>{service.endpoint}</code>
                    <Copy value={service.endpoint} />
                  </div>
                  <p className="panel-note">
                    {runtimeAccess.enabled ? "通过 Coordinator 访问推理接口，服务协议以实际配置为准。" : "此为已保存的地址；运行时未启用，平台当前不验证连通性。"}
                  </p>
                  {canExecute && (
                    <Button kind="text" onClick={() => setTab("playground")}>
                      发起一次推理
                      <ChevronRight size={14} />
                    </Button>
                  )}
                </>
              ) : (
                <Empty
                  title="暂无访问入口"
                  description="完成管理面部署后显示实际服务地址。"
                />
              )}
            </section>}
            {runtimeAccess.enabled && <section className="panel">
              <div className="panel-head">
                <h2>目标容量与就绪状态</h2>
                <button
                  className="icon-btn"
                  onClick={() => void reload()}
                  aria-label="刷新服务就绪状态"
                >
                  <RefreshCw size={14} />
                </button>
              </div>
              {loading ? (
                <Spinner />
              ) : readinessGate ? (
                <>
                  <div className="service-readiness-summary">
                    <div className={`readiness-symbol ${targetReady ? "ready" : ""}`}>
                      {targetReady ? <CheckCircle2 size={26} /> : <Clock3 size={26} />}
                    </div>
                    <div>
                      <b>{readinessTitle}</b>
                      <p>分别核对{service.runtime === "kubernetes" ? "受管 Pod" : "受管容器"}、目标角色数量和 Coordinator 原生就绪结果。</p>
                      {service.runtime === "kubernetes" && readinessGate.expected_source === "native_owned_role_target" && <p>当前目标来自已核验归属的原生角色资源；声明配置单独展示。</p>}
                      {recordTime(readinessGate.observed_at) && <small>读取时间 {when(readinessGate.observed_at)}</small>}
                    </div>
                  </div>
                  <div className="service-readiness-capacity" aria-label="目标角色容量">
                    {capacityRoles.filter(({ key }) => {
                      const expected = readinessGate.expected[key];
                      const observed = readinessGate.observed[key];
                      return key === "encode"
                        ? service.runtime === "kubernetes" && ((isResourceInteger(expected) && expected > 0) || (isResourceInteger(observed) && observed > 0))
                        : isResourceInteger(expected) && expected > 0;
                    }).map(({ key, label }) => (
                      <span key={key}>
                        {label}
                        <b>{isResourceInteger(readinessGate.observed[key])
                          ? `${readinessGate.observed[key]} / ${isResourceInteger(readinessGate.expected[key]) ? readinessGate.expected[key] : "目标待确认"}`
                          : isResourceInteger(readinessGate.expected[key]) ? `等待核对（目标 ${readinessGate.expected[key]}）` : "等待核对"}</b>
                      </span>
                    ))}
                  </div>
                  {!!readinessGate.issues.length && <ul className="service-readiness-issues">{readinessGate.issues.map((issue, index) => (
                    <li key={`${issue.source}-${index}`}>{issue.detail}</li>
                  ))}</ul>}
                  <details className="service-readiness-native">
                    <summary>Coordinator 检查详情</summary>
                    <p>{readiness === true ? "原生就绪检查通过" : readiness === false ? "原生就绪检查尚未通过" : "本次未取得原生就绪结果"}；该结果不单独代表目标实例数量已齐全。</p>
                    {knownText(observations?.readiness?.message) && <p>{observations!.readiness!.message}</p>}
                  </details>
                </>
              ) : readiness !== null ? (
                <div className="readiness-status">
                  <div
                    className={`readiness-symbol ${readiness ? "ready" : ""}`}
                  >
                    {readiness ? (
                      <CheckCircle2 size={26} />
                    ) : (
                      <Clock3 size={26} />
                    )}
                  </div>
                  <div>
                    <b>{readiness ? "Coordinator 原生检查通过" : "Coordinator 原生检查尚未通过"}</b>
                    <p>
                      {knownText(observations?.readiness?.message)
                        ? observations!.readiness!.message
                        : "仅表示原生接口检查结果，目标实例容量与实际推理需分别核对。"}
                    </p>
                    {readAt && <small>读取时间 {when(readAt)}</small>}
                  </div>
                </div>
              ) : (
                <div className="not-observed">
                  <span className="status-dot muted" />
                  {unsubmitted
                    ? "完成部署后可检查引擎就绪状态。"
                    : "本次就绪检查未完成，请核对服务状态后刷新。"}
                </div>
              )}
              {!readinessGate && instanceSummary && (
                <div className="observed-counts">
                  <span>
                    已发现实例
                    <b>{instanceSummary.registered}</b>
                  </span>
                  {instanceSummary.healthy !== null && (
                    <span>
                      健康实例
                      <b>{instanceSummary.healthy}</b>
                    </span>
                  )}
                </div>
              )}
              {(observations?.errors?.length ?? 0) > 0 && (
                <div className="inline-observation-error">
                  {observations?.errors
                    .map((x) => (typeof x === "string" ? x : JSON.stringify(x)))
                    .join("；")}
                </div>
              )}
            </section>}
          </div>
        </div>
      )}
      {tab === "topology" && (
        <section className="panel">
          <div className="panel-head">
            <h2>
              角色与节点布局{" "}
              <span className="count-pill">{service.placements.length}</span>
            </h2>
            <span className="small-caption">
              {service.runtime === "kubernetes"
                ? "执行入口与集群调度分开管理"
                : "声明布局 · 实际状态见 Motor 实例"}
            </span>
          </div>
          <div className="topology-flow">
            <div className="topology-entry">
              <div className="topology-node-icon">
                <Send size={22} />
              </div>
              <b>客户端请求</b>
              <small>OpenAI / Anthropic API</small>
            </div>
            <div className="topology-line" />
            <div className="topology-central">
              <div className="topology-node-icon red">
                <Layers3 size={24} />
              </div>
              <b>Motor 管理面</b>
              <small>Coordinator · Controller</small>
            </div>
            <div className="topology-line" />
            <div className="topology-engines">
              {service.placements
                .filter((p) => p.role !== "coordinator_controller")
                .map((p, i) => (
                  <div className="topology-instance" key={i}>
                    <span className={`role-chip role-${p.role}`}>
                      {roleName(p.role)}
                    </span>
                    <b>{p.instance_name}</b>
                    <small>
                      {nodes.find((n) => n.id === p.node_id)?.name ?? p.node_id}
                    </small>
                  </div>
                ))}
              {service.placements.every(
                (p) => p.role === "coordinator_controller",
              ) && (
                <span className="small-caption">由集群调度原生工作负载</span>
              )}
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>角色 / 实例</th>
                  <th>
                    {service.runtime === "kubernetes" ? "执行 Agent" : "节点"}
                  </th>
                  <th>NPU 设备</th>
                  {hasPortPlan && <th>端口配置</th>}
                </tr>
              </thead>
              <tbody>
                {service.placements.map((p, i) => (
                  <tr key={i}>
                    <td>
                      <span className={`role-chip role-${p.role}`}>
                        {roleName(p.role)}
                      </span>
                      <span className="cell-secondary">{p.instance_name}</span>
                    </td>
                    <td>
                      <strong>
                        {nodes.find((n) => n.id === p.node_id)?.name ??
                          p.node_id}
                      </strong>
                      <span className="cell-secondary">
                        {nodes.find((n) => n.id === p.node_id)?.address ?? ""}
                      </span>
                    </td>
                    <td>
                      {p.devices.length ? (
                        p.devices.map((d) => (
                          <span className="device-tag" key={d}>
                            {d}
                          </span>
                        ))
                      ) : (
                        <span className="muted-text">不分配</span>
                      )}
                    </td>
                    {hasPortPlan && (
                      <td>
                        {(
                          [
                            ["NodeManager", p.ports?.node_manager],
                            ["RPC", p.ports?.rpc],
                            ["Engine", p.ports?.base],
                            ["KV", p.ports?.kv],
                          ] as const
                        )
                          .filter(
                            ([, value]) =>
                              isResourceInteger(value) &&
                              value > 0 &&
                              value <= 65535,
                          )
                          .map(([label, value]) => (
                            <span
                              key={label}
                              className="service-port-item mono small-caption"
                            >
                              {label} {value}
                            </span>
                          ))}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {tab === "config" && (
        <section className="panel">
          <div className="panel-head">
            <div className="tab-list compact">
              <button
                className={configTab === "user" ? "active" : ""}
                onClick={() => setConfigTab("user")}
              >
                user_config.json
              </button>
              <button
                className={configTab === "env" ? "active" : ""}
                onClick={() => setConfigTab("env")}
              >
                env.json
              </button>
              {(service.runtime === "kubernetes" ||
                Boolean(service.extra_manifests?.length)) && (
                <button
                  className={configTab === "extra" ? "active" : ""}
                  onClick={() => setConfigTab("extra")}
                >
                  高级清单
                </button>
              )}
            </div>
            <Copy
              value={JSON.stringify(
                configTab === "user"
                  ? service.user_config
                  : configTab === "env"
                    ? service.env
                    : (service.extra_manifests ?? []),
                null,
                2,
              )}
              label="复制完整配置"
            />
          </div>
          <pre className="json-view">
            {JSON.stringify(
              configTab === "user"
                ? service.user_config
                : configTab === "env"
                  ? service.env
                  : (service.extra_manifests ?? []),
              null,
              2,
            )}
          </pre>
        </section>
      )}
      {tab === "revisions" && (
        <section className="panel">
          <div className="panel-head">
            <h2>
              配置修订 {hasSuccess && <span className="count-pill">{revisions.length}</span>}
            </h2>
            <Button onClick={() => void reload()}>
              <RefreshCw size={14} />
              刷新
            </Button>
          </div>
          {loading && !hasSuccess ? (
            <Spinner />
          ) : hasSuccess && revisions.length ? (
            <div className="revision-list">
              {revisions.map((r, i) => (
                <details key={r.id}>
                  <summary>
                    <span className="revision-mark">
                      <FileClock size={17} />
                    </span>
                    <div>
                      <b>修订 {revisions.length - i}</b>
                      <small>
                        {when(r.created_at)} ·{" "}
                        {r.runtime === "kubernetes" ? "Kubernetes" : "Docker"}
                      </small>
                    </div>
                    <span className="mono muted-text">
                      {String(r.id).slice(0, 12)}
                    </span>
                    <ChevronRight size={16} />
                  </summary>
                  <div className="revision-actions">
                    <Button
                      onClick={() =>
                        download(r, `${service.name}-revision-${r.id}.json`)
                      }
                    >
                      <Download size={13} />
                      导出此修订
                    </Button>
                  </div>
                  <pre className="json-view">
                    {JSON.stringify(
                      {
                        user_config: r.user_config,
                        extra_manifests: r.extra_manifests ?? [],
                        ...(r.motor_profile
                          ? { motor_profile: r.motor_profile }
                          : {}),
                        env: r.env,
                        placements: r.placements,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              ))}
            </div>
          ) : (
            <Empty
              title={hasSuccess ? "暂无配置修订" : readNotice({ loaded: false, refreshing: loading, error })}
              description={hasSuccess ? "每次保存配置都会保留一个可追溯版本。" : "尚未取得本服务的配置修订，请重试。"}
            />
          )}
        </section>
      )}
      {tab === "logs" && logsEnabled && runtimeAccess.enabled && (
        <section className="panel log-panel">
          <div className="panel-head">
            <div>
              <h2>
                运行日志 {hasSuccess && !unsubmitted && <span className="count-pill">{logs.length}</span>}
              </h2>
            </div>
            <div className="inline-controls">
              <RuntimeLogFilters service={service} nodes={nodes} nodeId={logNode} role={logRole} onChange={(nodeId, role) => setLogFilters({ serviceId: service.id, nodeId, role })} />
              <input
                className="small-input"
                placeholder="搜索当前日志窗口"
                value={logQuery}
                onChange={(e) => setLogQuery(e.target.value)}
              />
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={autoLog}
                  disabled={unsubmitted}
                  onChange={(e) => setAutoLog(e.target.checked)}
                />
                自动刷新
              </label>
              <Button
                onClick={() => download(logs, `${service.name}-logs.json`)}
                disabled={!hasSuccess || !logs.length || unsubmitted}
              >
                <Download size={14} />
                导出
              </Button>
              <Button
                onClick={() => void reload()}
                loading={loading}
                disabled={unsubmitted}
              >
                <RefreshCw size={14} />
                刷新
              </Button>
            </div>
          </div>
          {hasSuccess && logPartial && <InfoBox>部分日志来源未完整返回，当前窗口可能缺少记录；请按执行节点、角色筛选后重试，并查看 Agent 诊断。</InfoBox>}
          <div className="log-view">
            {hasSuccess && logs.length ? (
              logs
                .filter((l) => matchesLog(l, logQuery))
                .map((l, i) => (
                  <RuntimeLogLine key={i} line={l} />
                ))
            ) : (
              <div className="log-empty">
                <TerminalSquare size={30} />
                <span>
                  {unsubmitted
                    ? "服务尚未提交部署，暂无容器/Pod运行日志"
                    : !hasSuccess
                      ? readNotice({ loaded: false, refreshing: loading, error })
                      : "暂无可读取的运行日志"}
                </span>
              </div>
            )}
          </div>
          <div className="log-footer">
            <span
              className={`status-dot ${autoLog && !unsubmitted ? "green" : "muted"}`}
            />
            {unsubmitted
              ? "尚未提交部署"
              : autoLog
                ? "每次读取完成后等待 4 秒"
                : "按需读取"}
            <span>近期窗口上限 300 行，非完整归档 · 节点与角色在服务端筛选</span>
          </div>
        </section>
      )}
      {tab === "monitor" && runtimeAccess.enabled && (
        <ServiceMetricsPanel key={`${service.id}:${service.config_revision}:${service.deployed_digest}:${service.last_job_id}`} service={service} />
      )}
      {tab === "playground" && runtimeAccess.enabled && (
        <>
        {readinessGate && !targetReady && <div className="service-capacity-guidance"><InfoBox>
          <strong>{readinessTitle}</strong>
          <p>{servingReady
            ? "当前原生接口已有可用实例，可手动调试；可用容量尚未达到声明目标，调试成功不代表全部目标实例已就绪。"
            : nativeReady === true
              ? "Coordinator 接口已通过检查，尚未确认所需推理角色可用。平台保留手动调试入口，请结合实例状态判断实际返回。"
              : "当前尚未确认原生服务就绪。平台保留手动调试入口，请结合目标容量和运行日志判断实际返回。"}</p>
        </InfoBox></div>}
        <div className="playground-grid">
          <section className="panel">
            <div className="panel-head">
              <h2>推理请求</h2>
              <span className="tag">Chat Completions</span>
            </div>
            <div className="playground-form">
              {knownText(service.model) && (
                <label className="field">
                  <span className="field-label">模型</span>
                  <input value={service.model} readOnly />
                </label>
              )}
              <label className="field">
                <span className="field-label">输入消息</span>
                <textarea
                  rows={8}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="输入你的问题…"
                />
              </label>
              <label className="field">
                <span className="field-label">最大生成 Token</span>
                <input
                  type="number"
                  value={maxTokens}
                  min="1"
                  max="8192"
                  onChange={(e) => setMaxTokens(Number(e.target.value))}
                />
              </label>
              <Button
                kind="primary"
                disabled={!canExecute || !prompt.trim() || unsubmitted || (service.runtime === "docker" && !service.endpoint)}
                onClick={runInference}
                loading={inferBusy}
              >
                <Send size={15} />
                发送请求
              </Button>
              {inferBusy && <Button onClick={() => {
                inference.current?.abort();
                setError("已停止浏览器等待。服务端可能已接受请求，平台不会自动重发。");
              }}>停止等待</Button>}
              <p className="small-caption">
                {service.runtime === "kubernetes" ? "请求经执行 Agent 解析并访问实际 Coordinator Pod 地址，不依赖尚未确认的外部入口。" : "请求由服务端访问已保存的 Coordinator 地址。"}
              </p>
              <p className="small-caption">思考模型的思考与正文共用生成额度。仅返回思考或回答被截断时，可按需提高最大生成 Token；平台不会自动重发。</p>
            </div>
          </section>
          <section className="panel">
            <div className="panel-head">
              <h2>模型响应</h2>
            </div>
            {inferBusy ? (
              <Spinner label="正在等待模型响应" />
            ) : answer ? (
              <InferenceResponse answer={answer} />
            ) : (
              <Empty
                title="等待你的第一条消息"
                description="模型的真实响应与耗时将在这里展示。"
                icon={<Send size={26} />}
              />
            )}
          </section>
        </div>
        </>
      )}
    </>
  );
}
