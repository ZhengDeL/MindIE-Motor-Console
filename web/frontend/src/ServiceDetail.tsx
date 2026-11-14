import type { ServiceDetailProps } from '@mindie-motor/ui-sdk';
import { UiElement, UiRegion, UiSlot, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
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
  Observations,
  Revision,
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

function DefaultServiceDetail({
  service,
  nodes,
  onBack,
  onEdit,
  onAction,
  notify,
  canOperate,
  stopChecking = false,
}: ServiceDetailProps) {
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
      <UiButton actionContext={{service, canOperate}} actionId="services.back" className="back-link" onClick={onBack}>
        <ArrowLeft size={14} />
        <UiText>{"返回推理服务"}</UiText>
      </UiButton>
      <UiElement as="div" className="page-heading service-detail-heading">
        <UiElement as="div">
          <UiElement as="div" className="heading-with-icon">
            <UiElement as="div" className="service-icon large">
              <Box size={27} />
            </UiElement>
            <UiElement as="div">
              <UiElement as="div" className="title-row">
                <UiElement as="h1">{service.name}</UiElement>
                <Badge status={displayedStatus}>
                  {readinessGate && liveStatus && !targetReady && servingReady
                    ? readinessGate.state === "degraded" ? "部分资源异常" : readinessGate.state === "unknown" ? "容量待确认" : "容量未齐"
                    : undefined}
                </Badge>
              </UiElement>
              <UiElement as="p">
                {knownText(service.model) && (
                  <>
                    {service.model}
                    <UiElement as="span" className="inline-divider" />
                  </>
                )}
                {service.runtime === "kubernetes" ? "Kubernetes" : "Docker"}
                {knownText(service.engine) && (
                  <>
                    <UiElement as="span" className="inline-divider" />
                    {service.engine}
                  </>
                )}
              </UiElement>
            </UiElement>
          </UiElement>
        </UiElement>
        <UiElement as="div" className="heading-actions">
          {service.runtime === "kubernetes" && (
            <Button actionContext={{service, canOperate}} actionId="services.undeploy"
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
              <UiText>{"卸载部署"}</UiText>
            </Button>
          )}
          {service.runtime === "kubernetes" && (
            <Button actionContext={{service, canOperate}} actionId="services.scale"
              disabled={
                !canExecute ||
                !["running", "starting", "partial"].includes(service.status)
              }
              title={readOnlyReason || "先预览原生实例数调整计划，再确认执行"}
              onClick={() => onAction(service, "scale")}
            >
              <SlidersHorizontal size={14} />
              <UiText>{"调整实例数"}</UiText>
            </Button>
          )}
          <Button actionContext={{service, canOperate}} actionId="services.export"
            disabled={!canOperate || service.config_redacted}
            onClick={exportService}
          >
            <Download size={14} />
            <UiText>{"导出配置"}</UiText>
          </Button>
          {templatesEnabled && <Button actionContext={{service, canOperate}} actionId="services.template.create"
            disabled={!canOperate || service.config_redacted}
            onClick={() => onAction(service, "template")}
          >
            <FileJson size={14} />
            <UiText>{"保存为模板"}</UiText>
          </Button>}
          <Button actionContext={{service, canOperate}} actionId="services.edit"
            onClick={() => onEdit(service)}
            disabled={
              !canOperate || !runtimeAccess.enabled || !["draft", "stopped"].includes(service.status)
            }
          >
            <Settings2 size={14} />
            <UiText>{"编辑"}</UiText>
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
              <Button actionContext={{service, canOperate}} actionId="services.restart"
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
                <UiText>{"重启"}</UiText>
              </Button>
              <Button actionContext={{service, canOperate}} actionId="services.stop"
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
            <Button actionContext={{service, canOperate}} actionId="services.deploy"
              kind="primary"
              disabled={!canExecute}
              title={readOnlyReason || undefined}
              onClick={() => onAction(service, "deploy")}
            >
              <Play size={14} />
              {service.needs_rebuild ? "重建并应用配置" : "部署服务"}
            </Button>
          )}
        </UiElement>
      </UiElement>
      <UiElement as="div" className="service-subheading">
        <UiElement as="span">
          <UiText>{"服务 ID："}</UiText><UiElement as="code">{service.id}</UiElement>
          <Copy value={service.id} />
        </UiElement>
        {recordTime(service.created_at) && (
          <UiElement as="span"><UiText>{"创建时间："}</UiText>{when(service.created_at)}</UiElement>
        )}
        {recordTime(service.updated_at) && (
          <UiElement as="span"><UiText>{"最近更新："}</UiText>{when(service.updated_at)}</UiElement>
        )}
        <Button actionContext={{service, canOperate}} actionId="services.job.open"
          kind="text"
          disabled={!service.last_job_id}
          title={service.last_job_id ? "读取已保存的最近任务" : "尚无部署任务"}
          onClick={() => onAction(service, "job")}
        >
          <FileClock size={13} />
          <UiText>{"查看最近任务"}</UiText>
        </Button>
      </UiElement>
      <UiElement as="div" className="detail-tab-bar">
        {tabs
          .filter(
            ([id]) =>
              (id !== "logs" || logsEnabled) && (id !== "aigw" || gatewaysEnabled)
              && (!["logs", "aigw", "monitor", "playground"].includes(id) || runtimeAccess.enabled)
              && (canOperate || !["config", "revisions", "playground"].includes(id)),
          )
          .map(([id, label, Icon]) => (
            <UiButton actionContext={{service, canOperate}} actionId="services.tab.select"
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
            </UiButton>
          ))}
      </UiElement>
      {error && <ErrorBox message={error} />}
      {readOnlyReason && <InfoBox>{readOnlyReason}</InfoBox>}
      {tabNotice && <InfoBox>{tabNotice}</InfoBox>}
      {service.last_error && <ErrorBox message={service.last_error} />}
      {tab === "aigw" && gatewaysEnabled && runtimeAccess.enabled && <UiSlot name="tab.service.aigw" model={{service, nodes, canOperate, canExecute, tab, setTab, prompt, setPrompt, maxTokens, setMaxTokens, runInference, answer, inferBusy, configTab, setConfigTab, revisions, logs, observations, onEdit, onAction, notify}}>{<NativeAigwModelsPanel service={service} />}</UiSlot>}
      {dockerRecoveryPending && (
        <InfoBox>
          <UiText>{"部署未完成，但可能已有受管资源。请先停止这些资源，确认停止后再重建并应用配置。"}</UiText>
        </InfoBox>
      )}
      {tab === "overview" && <UiSlot name="tab.service.overview" model={{service, nodes, canOperate, canExecute, tab, setTab, prompt, setPrompt, maxTokens, setMaxTokens, runInference, answer, inferBusy, configTab, setConfigTab, revisions, logs, observations, onEdit, onAction, notify}}>{(
        <UiElement as="div" className="detail-overview-grid">
          <UiRegion name="layout.panel" as="section" className="panel">
            <UiElement as="div" className="panel-head">
              <UiElement as="h2"><UiText>{"服务信息"}</UiText></UiElement>
              <UiElement as="span" className="small-caption"><UiText>{"声明配置"}</UiText></UiElement>
            </UiElement>
            <UiElement as="dl" className="description-list">
              <UiElement as="dt"><UiText>{"运行环境"}</UiText></UiElement>
              <UiElement as="dd">
                {service.runtime === "kubernetes"
                  ? "Kubernetes 集群"
                  : "多节点 Docker"}
              </UiElement>
              {knownText(configs.deploy_mode) && (
                <>
                  <UiElement as="dt"><UiText>{"部署模式"}</UiText></UiElement>
                  <UiElement as="dd">
                    {configs.deploy_mode}
                    <UiElement as="span" className="tag small">
                      {service.scenario === "hybrid" ? "PD 混部" : "PD 分离"}
                    </UiElement>
                  </UiElement>
                </>
              )}
              {knownText(service.engine) && (
                <>
                  <UiElement as="dt"><UiText>{"推理引擎"}</UiText></UiElement>
                  <UiElement as="dd">{service.engine}</UiElement>
                </>
              )}
              {knownText(service.motor_profile) && (
                <>
                  <UiElement as="dt"><UiText>{"引擎适配配置"}</UiText></UiElement>
                  <UiElement as="dd" className="mono">{service.motor_profile}</UiElement>
                </>
              )}
              {knownText(configs.hardware_type) && (
                <>
                  <UiElement as="dt"><UiText>{"硬件规格"}</UiText></UiElement>
                  <UiElement as="dd">{configs.hardware_type}</UiElement>
                </>
              )}
              {!!replicaPlan.length && (
                <>
                  <UiElement as="dt"><UiText>{"实例规划"}</UiText></UiElement>
                  <UiElement as="dd">
                    {replicaPlan.filter((item) => item.value > 0).length
                      ? replicaPlan
                          .filter((item) => item.value > 0)
                          .map((item) => (
                            <UiElement as="span"
                              key={item.key}
                              className="service-replica-item"
                            >
                              <UiElement as="span" className={`role-bullet ${item.key}`} />
                              {item.value} {item.label}
                            </UiElement>
                          ))
                      : "0 个实例"}
                  </UiElement>
                </>
              )}
              {isResourceInteger(service.device_count) && (
                <>
                  <UiElement as="dt"><UiText>{"NPU 逻辑设备"}</UiText></UiElement>
                  <UiElement as="dd">{service.device_count} <UiText>{"个"}</UiText></UiElement>
                </>
              )}
              {!!service.node_names?.length && (
                <>
                  <UiElement as="dt"><UiText>{"部署节点"}</UiText></UiElement>
                  <UiElement as="dd">{service.node_names.join("、")}</UiElement>
                </>
              )}
              {knownText(service.image) && (
                <>
                  <UiElement as="dt"><UiText>{"服务镜像"}</UiText></UiElement>
                  <UiElement as="dd" className="mono break-word">{service.image}</UiElement>
                </>
              )}
              {knownText(configs.weight_mount_path) && (
                <>
                  <UiElement as="dt"><UiText>{"权重目录"}</UiText></UiElement>
                  <UiElement as="dd" className="mono">{configs.weight_mount_path}</UiElement>
                </>
              )}
            </UiElement>
          </UiRegion>
          <UiElement as="div" className="detail-overview-side">
            {service.runtime === "kubernetes" ? runtimeAccess.enabled && <KubernetesEndpoints service={service} /> : <UiRegion name="layout.panel" as="section" className="panel">
              <UiElement as="div" className="panel-head">
                <UiElement as="h2"><UiText>{"访问入口"}</UiText></UiElement>
                <ArrowUpRight size={16} />
              </UiElement>
              {knownText(service.endpoint) ? (
                <>
                  <UiElement as="div" className="endpoint-box">
                    <UiElement as="code">{service.endpoint}</UiElement>
                    <Copy value={service.endpoint} />
                  </UiElement>
                  <UiElement as="p" className="panel-note">
                    {runtimeAccess.enabled ? "通过 Coordinator 访问推理接口，服务协议以实际配置为准。" : "此为已保存的地址；运行时未启用，平台当前不验证连通性。"}
                  </UiElement>
                  {canExecute && (
                    <Button actionContext={{service, canOperate}} actionId="inference.open" kind="text" onClick={() => setTab("playground")}>
                      <UiText>{"发起一次推理"}</UiText>
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
            </UiRegion>}
            {runtimeAccess.enabled && <UiRegion name="layout.panel" as="section" className="panel">
              <UiElement as="div" className="panel-head">
                <UiElement as="h2"><UiText>{"目标容量与就绪状态"}</UiText></UiElement>
                <UiButton actionContext={{service, canOperate}} actionId="services.readiness.refresh"
                  className="icon-btn"
                  onClick={() => void reload()}
                  aria-label="刷新服务就绪状态"
                >
                  <RefreshCw size={14} />
                </UiButton>
              </UiElement>
              {loading ? (
                <Spinner />
              ) : readinessGate ? (
                <>
                  <UiElement as="div" className="service-readiness-summary">
                    <UiElement as="div" className={`readiness-symbol ${targetReady ? "ready" : ""}`}>
                      {targetReady ? <CheckCircle2 size={26} /> : <Clock3 size={26} />}
                    </UiElement>
                    <UiElement as="div">
                      <UiElement as="b">{readinessTitle}</UiElement>
                      <UiElement as="p"><UiText>{"分别核对"}</UiText>{service.runtime === "kubernetes" ? "受管 Pod" : "受管容器"}<UiText>{"、目标角色数量和 Coordinator 原生就绪结果。"}</UiText></UiElement>
                      {service.runtime === "kubernetes" && readinessGate.expected_source === "native_owned_role_target" && <UiElement as="p"><UiText>{"当前目标来自已核验归属的原生角色资源；声明配置单独展示。"}</UiText></UiElement>}
                      {recordTime(readinessGate.observed_at) && <UiElement as="small"><UiText>{"读取时间"}</UiText> {when(readinessGate.observed_at)}</UiElement>}
                    </UiElement>
                  </UiElement>
                  <UiElement as="div" className="service-readiness-capacity" aria-label="目标角色容量">
                    {capacityRoles.filter(({ key }) => {
                      const expected = readinessGate.expected[key];
                      const observed = readinessGate.observed[key];
                      return key === "encode"
                        ? service.runtime === "kubernetes" && ((isResourceInteger(expected) && expected > 0) || (isResourceInteger(observed) && observed > 0))
                        : isResourceInteger(expected) && expected > 0;
                    }).map(({ key, label }) => (
                      <UiElement as="span" key={key}>
                        {label}
                        <UiElement as="b">{isResourceInteger(readinessGate.observed[key])
                          ? `${readinessGate.observed[key]} / ${isResourceInteger(readinessGate.expected[key]) ? readinessGate.expected[key] : "目标待确认"}`
                          : isResourceInteger(readinessGate.expected[key]) ? `等待核对（目标 ${readinessGate.expected[key]}）` : "等待核对"}</UiElement>
                      </UiElement>
                    ))}
                  </UiElement>
                  {!!readinessGate.issues.length && <UiElement as="ul" className="service-readiness-issues">{readinessGate.issues.map((issue, index) => (
                    <UiElement as="li" key={`${issue.source}-${index}`}>{issue.detail}</UiElement>
                  ))}</UiElement>}
                  <UiElement as="details" className="service-readiness-native">
                    <UiElement as="summary"><UiText>{"Coordinator 检查详情"}</UiText></UiElement>
                    <UiElement as="p">{readiness === true ? "原生就绪检查通过" : readiness === false ? "原生就绪检查尚未通过" : "本次未取得原生就绪结果"}<UiText>{"；该结果不单独代表目标实例数量已齐全。"}</UiText></UiElement>
                    {knownText(observations?.readiness?.message) && <UiElement as="p">{observations!.readiness!.message}</UiElement>}
                  </UiElement>
                </>
              ) : readiness !== null ? (
                <UiElement as="div" className="readiness-status">
                  <UiElement as="div"
                    className={`readiness-symbol ${readiness ? "ready" : ""}`}
                  >
                    {readiness ? (
                      <CheckCircle2 size={26} />
                    ) : (
                      <Clock3 size={26} />
                    )}
                  </UiElement>
                  <UiElement as="div">
                    <UiElement as="b">{readiness ? "Coordinator 原生检查通过" : "Coordinator 原生检查尚未通过"}</UiElement>
                    <UiElement as="p">
                      {knownText(observations?.readiness?.message)
                        ? observations!.readiness!.message
                        : "仅表示原生接口检查结果，目标实例容量与实际推理需分别核对。"}
                    </UiElement>
                    {readAt && <UiElement as="small"><UiText>{"读取时间"}</UiText> {when(readAt)}</UiElement>}
                  </UiElement>
                </UiElement>
              ) : (
                <UiElement as="div" className="not-observed">
                  <UiElement as="span" className="status-dot muted" />
                  {unsubmitted
                    ? "完成部署后可检查引擎就绪状态。"
                    : "本次就绪检查未完成，请核对服务状态后刷新。"}
                </UiElement>
              )}
              {!readinessGate && instanceSummary && (
                <UiElement as="div" className="observed-counts">
                  <UiElement as="span">
                    <UiText>{"已发现实例"}</UiText>
                    <UiElement as="b">{instanceSummary.registered}</UiElement>
                  </UiElement>
                  {instanceSummary.healthy !== null && (
                    <UiElement as="span">
                      <UiText>{"健康实例"}</UiText>
                      <UiElement as="b">{instanceSummary.healthy}</UiElement>
                    </UiElement>
                  )}
                </UiElement>
              )}
              {(observations?.errors?.length ?? 0) > 0 && (
                <UiElement as="div" className="inline-observation-error">
                  {observations?.errors
                    .map((x) => (typeof x === "string" ? x : JSON.stringify(x)))
                    .join("；")}
                </UiElement>
              )}
            </UiRegion>}
          </UiElement>
        </UiElement>
      )}</UiSlot>}
      {tab === "topology" && <UiSlot name="tab.service.topology" model={{service, nodes, canOperate, canExecute, tab, setTab, prompt, setPrompt, maxTokens, setMaxTokens, runInference, answer, inferBusy, configTab, setConfigTab, revisions, logs, observations, onEdit, onAction, notify}}>{(
        <UiRegion name="layout.panel" as="section" className="panel">
          <UiElement as="div" className="panel-head">
            <UiElement as="h2">
              <UiText>{"角色与节点布局"}</UiText>{" "}
              <UiElement as="span" className="count-pill">{service.placements.length}</UiElement>
            </UiElement>
            <UiElement as="span" className="small-caption">
              {service.runtime === "kubernetes"
                ? "执行入口与集群调度分开管理"
                : "声明布局 · 实际状态见 Motor 实例"}
            </UiElement>
          </UiElement>
          <UiElement as="div" className="topology-flow">
            <UiElement as="div" className="topology-entry">
              <UiElement as="div" className="topology-node-icon">
                <Send size={22} />
              </UiElement>
              <UiElement as="b"><UiText>{"客户端请求"}</UiText></UiElement>
              <UiElement as="small"><UiText>{"OpenAI / Anthropic API"}</UiText></UiElement>
            </UiElement>
            <UiElement as="div" className="topology-line" />
            <UiElement as="div" className="topology-central">
              <UiElement as="div" className="topology-node-icon red">
                <Layers3 size={24} />
              </UiElement>
              <UiElement as="b"><UiText>{"Motor 管理面"}</UiText></UiElement>
              <UiElement as="small"><UiText>{"Coordinator · Controller"}</UiText></UiElement>
            </UiElement>
            <UiElement as="div" className="topology-line" />
            <UiElement as="div" className="topology-engines">
              {service.placements
                .filter((p) => p.role !== "coordinator_controller")
                .map((p, i) => (
                  <UiElement as="div" className="topology-instance" key={i}>
                    <UiElement as="span" className={`role-chip role-${p.role}`}>
                      {roleName(p.role)}
                    </UiElement>
                    <UiElement as="b">{p.instance_name}</UiElement>
                    <UiElement as="small">
                      {nodes.find((n) => n.id === p.node_id)?.name ?? p.node_id}
                    </UiElement>
                  </UiElement>
                ))}
              {service.placements.every(
                (p) => p.role === "coordinator_controller",
              ) && (
                <UiElement as="span" className="small-caption"><UiText>{"由集群调度原生工作负载"}</UiText></UiElement>
              )}
            </UiElement>
          </UiElement>
          <UiElement as="div" className="table-wrap">
            <UiElement as="table">
              <UiElement as="thead">
                <UiElement as="tr">
                  <UiElement as="th"><UiText>{"角色 / 实例"}</UiText></UiElement>
                  <UiElement as="th">
                    {service.runtime === "kubernetes" ? "执行 Agent" : "节点"}
                  </UiElement>
                  <UiElement as="th"><UiText>{"NPU 设备"}</UiText></UiElement>
                  {hasPortPlan && <UiElement as="th"><UiText>{"端口配置"}</UiText></UiElement>}
                </UiElement>
              </UiElement>
              <UiElement as="tbody">
                {service.placements.map((p, i) => (
                  <UiElement as="tr" key={i}>
                    <UiElement as="td">
                      <UiElement as="span" className={`role-chip role-${p.role}`}>
                        {roleName(p.role)}
                      </UiElement>
                      <UiElement as="span" className="cell-secondary">{p.instance_name}</UiElement>
                    </UiElement>
                    <UiElement as="td">
                      <UiElement as="strong">
                        {nodes.find((n) => n.id === p.node_id)?.name ??
                          p.node_id}
                      </UiElement>
                      <UiElement as="span" className="cell-secondary">
                        {nodes.find((n) => n.id === p.node_id)?.address ?? ""}
                      </UiElement>
                    </UiElement>
                    <UiElement as="td">
                      {p.devices.length ? (
                        p.devices.map((d) => (
                          <UiElement as="span" className="device-tag" key={d}>
                            {d}
                          </UiElement>
                        ))
                      ) : (
                        <UiElement as="span" className="muted-text"><UiText>{"不分配"}</UiText></UiElement>
                      )}
                    </UiElement>
                    {hasPortPlan && (
                      <UiElement as="td">
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
                            <UiElement as="span"
                              key={label}
                              className="service-port-item mono small-caption"
                            >
                              {label} {value}
                            </UiElement>
                          ))}
                      </UiElement>
                    )}
                  </UiElement>
                ))}
              </UiElement>
            </UiElement>
          </UiElement>
        </UiRegion>
      )}</UiSlot>}
      {tab === "config" && <UiSlot name="tab.service.config" model={{service, nodes, canOperate, canExecute, tab, setTab, prompt, setPrompt, maxTokens, setMaxTokens, runInference, answer, inferBusy, configTab, setConfigTab, revisions, logs, observations, onEdit, onAction, notify}}>{(
        <UiRegion name="layout.panel" as="section" className="panel">
          <UiElement as="div" className="panel-head">
            <UiElement as="div" className="tab-list compact">
              <UiButton actionContext={{service, canOperate}} actionId="services.config.user"
                className={configTab === "user" ? "active" : ""}
                onClick={() => setConfigTab("user")}
              >
                <UiText>{"user_config.json"}</UiText>
              </UiButton>
              <UiButton actionContext={{service, canOperate}} actionId="services.config.env"
                className={configTab === "env" ? "active" : ""}
                onClick={() => setConfigTab("env")}
              >
                <UiText>{"env.json"}</UiText>
              </UiButton>
              {(service.runtime === "kubernetes" ||
                Boolean(service.extra_manifests?.length)) && (
                <UiButton actionContext={{service, canOperate}} actionId="services.config.extra"
                  className={configTab === "extra" ? "active" : ""}
                  onClick={() => setConfigTab("extra")}
                >
                  <UiText>{"高级清单"}</UiText>
                </UiButton>
              )}
            </UiElement>
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
          </UiElement>
          <UiElement as="pre" className="json-view">
            {JSON.stringify(
              configTab === "user"
                ? service.user_config
                : configTab === "env"
                  ? service.env
                  : (service.extra_manifests ?? []),
              null,
              2,
            )}
          </UiElement>
        </UiRegion>
      )}</UiSlot>}
      {tab === "revisions" && <UiSlot name="tab.service.revisions" model={{service, nodes, canOperate, canExecute, tab, setTab, prompt, setPrompt, maxTokens, setMaxTokens, runInference, answer, inferBusy, configTab, setConfigTab, revisions, logs, observations, onEdit, onAction, notify}}>{(
        <UiRegion name="layout.panel" as="section" className="panel">
          <UiElement as="div" className="panel-head">
            <UiElement as="h2">
              <UiText>{"配置修订"}</UiText> {hasSuccess && <UiElement as="span" className="count-pill">{revisions.length}</UiElement>}
            </UiElement>
            <Button actionContext={{service, canOperate}} actionId="services.revisions.refresh" onClick={() => void reload()}>
              <RefreshCw size={14} />
              <UiText>{"刷新"}</UiText>
            </Button>
          </UiElement>
          {loading && !hasSuccess ? (
            <Spinner />
          ) : hasSuccess && revisions.length ? (
            <UiElement as="div" className="revision-list">
              {revisions.map((r, i) => (
                <UiElement as="details" key={r.id}>
                  <UiElement as="summary">
                    <UiElement as="span" className="revision-mark">
                      <FileClock size={17} />
                    </UiElement>
                    <UiElement as="div">
                      <UiElement as="b"><UiText>{"修订"}</UiText> {revisions.length - i}</UiElement>
                      <UiElement as="small">
                        {when(r.created_at)} <UiText>{"·"}</UiText>{" "}
                        {r.runtime === "kubernetes" ? "Kubernetes" : "Docker"}
                      </UiElement>
                    </UiElement>
                    <UiElement as="span" className="mono muted-text">
                      {String(r.id).slice(0, 12)}
                    </UiElement>
                    <ChevronRight size={16} />
                  </UiElement>
                  <UiElement as="div" className="revision-actions">
                    <Button actionContext={{service, canOperate}} actionId="services.revisions.export"
                      onClick={() =>
                        download(r, `${service.name}-revision-${r.id}.json`)
                      }
                    >
                      <Download size={13} />
                      <UiText>{"导出此修订"}</UiText>
                    </Button>
                  </UiElement>
                  <UiElement as="pre" className="json-view">
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
                  </UiElement>
                </UiElement>
              ))}
            </UiElement>
          ) : (
            <Empty
              title={hasSuccess ? "暂无配置修订" : readNotice({ loaded: false, refreshing: loading, error })}
              description={hasSuccess ? "每次保存配置都会保留一个可追溯版本。" : "尚未取得本服务的配置修订，请重试。"}
            />
          )}
        </UiRegion>
      )}</UiSlot>}
      {tab === "logs" && logsEnabled && runtimeAccess.enabled && <UiSlot name="tab.service.logs" model={{service, nodes, canOperate, canExecute, tab, setTab, prompt, setPrompt, maxTokens, setMaxTokens, runInference, answer, inferBusy, configTab, setConfigTab, revisions, logs, observations, onEdit, onAction, notify}}>{(
        <UiRegion name="layout.panel" as="section" className="panel log-panel">
          <UiElement as="div" className="panel-head">
            <UiElement as="div">
              <UiElement as="h2">
                <UiText>{"运行日志"}</UiText> {hasSuccess && !unsubmitted && <UiElement as="span" className="count-pill">{logs.length}</UiElement>}
              </UiElement>
            </UiElement>
            <UiElement as="div" className="inline-controls">
              <RuntimeLogFilters service={service} nodes={nodes} nodeId={logNode} role={logRole} onChange={(nodeId, role) => setLogFilters({ serviceId: service.id, nodeId, role })} />
              <UiElement as="input"
                className="small-input"
                placeholder="搜索当前日志窗口"
                value={logQuery}
                onChange={(e) => setLogQuery(e.target.value)}
              />
              <UiElement as="label" className="checkbox-label">
                <UiElement as="input"
                  type="checkbox"
                  checked={autoLog}
                  disabled={unsubmitted}
                  onChange={(e) => setAutoLog(e.target.checked)}
                />
                <UiText>{"自动刷新"}</UiText>
              </UiElement>
              <Button actionContext={{service, canOperate}} actionId="logs.export"
                onClick={() => download(logs, `${service.name}-logs.json`)}
                disabled={!hasSuccess || !logs.length || unsubmitted}
              >
                <Download size={14} />
                <UiText>{"导出"}</UiText>
              </Button>
              <Button actionContext={{service, canOperate}} actionId="logs.refresh"
                onClick={() => void reload()}
                loading={loading}
                disabled={unsubmitted}
              >
                <RefreshCw size={14} />
                <UiText>{"刷新"}</UiText>
              </Button>
            </UiElement>
          </UiElement>
          {hasSuccess && logPartial && <InfoBox><UiText>{"部分日志来源未完整返回，当前窗口可能缺少记录；请按执行节点、角色筛选后重试，并查看 Agent 诊断。"}</UiText></InfoBox>}
          <UiElement as="div" className="log-view">
            {hasSuccess && logs.length ? (
              logs
                .filter((l) => matchesLog(l, logQuery))
                .map((l, i) => (
                  <RuntimeLogLine key={i} line={l} />
                ))
            ) : (
              <UiElement as="div" className="log-empty">
                <TerminalSquare size={30} />
                <UiElement as="span">
                  {unsubmitted
                    ? "服务尚未提交部署，暂无容器/Pod运行日志"
                    : !hasSuccess
                      ? readNotice({ loaded: false, refreshing: loading, error })
                      : "暂无可读取的运行日志"}
                </UiElement>
              </UiElement>
            )}
          </UiElement>
          <UiElement as="div" className="log-footer">
            <UiElement as="span"
              className={`status-dot ${autoLog && !unsubmitted ? "green" : "muted"}`}
            />
            {unsubmitted
              ? "尚未提交部署"
              : autoLog
                ? "每次读取完成后等待 4 秒"
                : "按需读取"}
            <UiElement as="span"><UiText>{"近期窗口上限 300 行，非完整归档 · 节点与角色在服务端筛选"}</UiText></UiElement>
          </UiElement>
        </UiRegion>
      )}</UiSlot>}
      {tab === "monitor" && runtimeAccess.enabled && <UiSlot name="tab.service.monitor" model={{service, nodes, canOperate, canExecute, tab, setTab, prompt, setPrompt, maxTokens, setMaxTokens, runInference, answer, inferBusy, configTab, setConfigTab, revisions, logs, observations, onEdit, onAction, notify}}>{(
        <ServiceMetricsPanel key={`${service.id}:${service.config_revision}:${service.deployed_digest}:${service.last_job_id}`} service={service} />
      )}</UiSlot>}
      {tab === "playground" && runtimeAccess.enabled && <UiSlot name="tab.service.playground" model={{service, nodes, canOperate, canExecute, tab, setTab, prompt, setPrompt, maxTokens, setMaxTokens, runInference, answer, inferBusy, configTab, setConfigTab, revisions, logs, observations, onEdit, onAction, notify}}>{(
        <>
        {readinessGate && !targetReady && <UiElement as="div" className="service-capacity-guidance"><InfoBox>
          <UiElement as="strong">{readinessTitle}</UiElement>
          <UiElement as="p">{servingReady
            ? "当前原生接口已有可用实例，可手动调试；可用容量尚未达到声明目标，调试成功不代表全部目标实例已就绪。"
            : nativeReady === true
              ? "Coordinator 接口已通过检查，尚未确认所需推理角色可用。平台保留手动调试入口，请结合实例状态判断实际返回。"
              : "当前尚未确认原生服务就绪。平台保留手动调试入口，请结合目标容量和运行日志判断实际返回。"}</UiElement>
        </InfoBox></UiElement>}
        <UiElement as="div" className="playground-grid">
          <UiRegion name="layout.panel" as="section" className="panel">
            <UiElement as="div" className="panel-head">
              <UiElement as="h2"><UiText>{"推理请求"}</UiText></UiElement>
              <UiElement as="span" className="tag"><UiText>{"Chat Completions"}</UiText></UiElement>
            </UiElement>
            <UiElement as="div" className="playground-form">
              {knownText(service.model) && (
                <UiElement as="label" className="field">
                  <UiElement as="span" className="field-label"><UiText>{"模型"}</UiText></UiElement>
                  <UiElement as="input" value={service.model} readOnly />
                </UiElement>
              )}
              <UiElement as="label" className="field">
                <UiElement as="span" className="field-label"><UiText>{"输入消息"}</UiText></UiElement>
                <UiElement as="textarea"
                  rows={8}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="输入你的问题…"
                />
              </UiElement>
              <UiElement as="label" className="field">
                <UiElement as="span" className="field-label"><UiText>{"最大生成 Token"}</UiText></UiElement>
                <UiElement as="input"
                  type="number"
                  value={maxTokens}
                  min="1"
                  max="8192"
                  onChange={(e) => setMaxTokens(Number(e.target.value))}
                />
              </UiElement>
              <Button actionContext={{service, canOperate}} actionId="inference.submit"
                kind="primary"
                disabled={!canExecute || !prompt.trim() || unsubmitted || (service.runtime === "docker" && !service.endpoint)}
                onClick={runInference}
                loading={inferBusy}
              >
                <Send size={15} />
                <UiText>{"发送请求"}</UiText>
              </Button>
              {inferBusy && <Button actionContext={{service, canOperate}} actionId="inference.cancel-wait" onClick={() => {
                inference.current?.abort();
                setError("已停止浏览器等待。服务端可能已接受请求，平台不会自动重发。");
              }}><UiText>{"停止等待"}</UiText></Button>}
              <UiElement as="p" className="small-caption">
                {service.runtime === "kubernetes" ? "请求经执行 Agent 解析并访问实际 Coordinator Pod 地址，不依赖尚未确认的外部入口。" : "请求由服务端访问已保存的 Coordinator 地址。"}
              </UiElement>
              <UiElement as="p" className="small-caption"><UiText>{"思考模型的思考与正文共用生成额度。仅返回思考或回答被截断时，可按需提高最大生成 Token；平台不会自动重发。"}</UiText></UiElement>
            </UiElement>
          </UiRegion>
          <UiRegion name="layout.panel" as="section" className="panel">
            <UiElement as="div" className="panel-head">
              <UiElement as="h2"><UiText>{"模型响应"}</UiText></UiElement>
            </UiElement>
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
          </UiRegion>
        </UiElement>
        </>
      )}</UiSlot>}
    </>
  );
}

const ServiceDetail = withUiSlot("component.ServiceDetail", DefaultServiceDetail);
export default ServiceDetail;

// Reference package implementation exports.
export { DefaultServiceDetail };
