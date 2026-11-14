import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import { useEffect, useId, useRef, useState } from "react";
import { isValidElement } from "react";
import type { ReactNode } from "react";
import { api, isReadOnlyAgent, when } from "./api";
import type { NodeDetail, NodeEditableField, NodeInfo } from "./api";
import { useUnsavedChanges } from "./unsavedChanges";
import { Activity, Network, RefreshCw, Server, Settings2 } from "./icons";
import {
  Badge,
  Button,
  Confirm,
  ErrorBox,
  Field,
  InfoBox,
  Modal,
  Spinner,
} from "./ui";
import {
  nodeObservation,
  observationAge,
  useObservationClock,
} from "./resourceObservation";
import "./agent-details.css";
import { knownText, resourceNotice } from "./resourcePresentation";
import { isResourceInteger } from "./resourceObservation";

type Draft = {
  name: string;
  address: string;
  nic_name: string;
  labels: string;
};
type Tab = "registration" | "connection" | "runtime";
const tabs = [
  { id: "registration", label: "登记信息", icon: Settings2 },
  { id: "connection", label: "连接信息", icon: Network },
  { id: "runtime", label: "运行上报", icon: Activity },
] as const;
const draftOf = (node: NodeDetail): Draft => ({
  name: node.name,
  address: node.address,
  nic_name: node.configured_nic_name,
  labels: node.labels.join(", "),
});
const parseLabels = (text: string) => [
  ...new Set(
    text
      .split(/[,，]/)
      .map((value) => value.trim())
      .filter(Boolean),
  ),
];
function changesOf(draft: Draft, node: NodeDetail) {
  const patch: Partial<{
    name: string;
    address: string;
    nic_name: string;
    labels: string[];
  }> = {};
  for (const key of ["name", "address", "nic_name"] as const) {
    const saved = key === "nic_name" ? node.configured_nic_name : node[key];
    if (draft[key].trim() !== saved) patch[key] = draft[key].trim();
  }
  const labels = parseLabels(draft.labels);
  if (JSON.stringify(labels) !== JSON.stringify(node.labels))
    patch.labels = labels;
  return patch;
}
function isDetail(value: NodeDetail, id: string): boolean {
  return (
    value?.id === id &&
    Number.isSafeInteger(value.configuration_revision) &&
    value.configuration_revision >= 0 &&
    typeof value.name === "string" &&
    typeof value.address === "string" &&
    typeof value.agent_url === "string" &&
    typeof value.nic_name === "string" &&
    typeof value.credential_configured === "boolean" &&
    typeof value.configured_nic_name === "string" &&
    typeof value.reported_nic_name === "string" &&
    Array.isArray(value.labels) &&
    value.labels.every((item) => typeof item === "string") &&
    !!value.edit_policy &&
    ["name", "labels", "address", "nic_name", "credentials", "agent_url"].every(
      (key) => {
        const rule = value.edit_policy[key as NodeEditableField];
        return (
          typeof rule?.allowed === "boolean" && typeof rule.reason === "string"
        );
      },
    ) &&
    Array.isArray(value.references?.services) &&
    value.references.services.every(
      (item) =>
        item &&
        typeof item.id === "string" &&
        typeof item.name === "string" &&
        typeof item.status === "string" &&
        typeof item.resources_may_exist === "boolean",
    ) &&
    Array.isArray(value.references?.jobs) &&
    value.references.jobs.every(
      (item) =>
        item &&
        typeof item.id === "string" &&
        typeof item.action === "string" &&
        typeof item.status === "string" &&
        typeof item.service_id === "string",
    )
  );
}

function DefaultAgentDetails({
  node,
  canAdminister,
  demo,
  reloadRevision = 0,
  connectionSummary,
  onCredentials,
  onSaved,
  onClose,
}: {
  node: NodeInfo;
  canAdminister: boolean;
  demo: boolean;
  reloadRevision?: number;
  connectionSummary: (node: NodeInfo) => ReactNode;
  onCredentials: (node: NodeDetail) => void;
  onSaved: (node: NodeDetail) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("registration");
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [review, setReview] = useState<"none" | "reload" | "confirm">("none");
  const [confirmed, setConfirmed] = useState(false);
  const [saved, setSaved] = useState(false);
  const [matchedReadback, setMatchedReadback] = useState(false);
  const [discard, setDiscard] = useState(false);
  const current = useRef({ detail, draft, review });
  current.current = { detail, draft, review };
  const active = useRef(true);
  const request = useRef<AbortController | null>(null);
  const mutationRequest = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const id = useId();
  const now = useObservationClock();
  const changes = detail && draft ? changesOf(draft, detail) : {};
  const dirty = Object.keys(changes).length > 0;
  useUnsavedChanges(dirty);
  const allowed = (field: NodeEditableField) =>
    canAdminister &&
    detail?.edit_policy[field]?.allowed === true &&
    !loading &&
    !saving;
  const deniedChanges =
    detail &&
    Object.keys(changes).some(
      (key) => !detail.edit_policy[key as NodeEditableField]?.allowed,
    );

  const load = async (preserve: boolean) => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const serial = ++sequence.current;
    setLoading(true);
    setError("");
    setSaved(false);
    setMatchedReadback(false);
    const timer = setTimeout(() => controller.abort(), 20_000);
    try {
      const value = await api<NodeDetail>(
        `/nodes/${encodeURIComponent(node.id)}`,
        { signal: controller.signal },
      );
      if (!active.current || sequence.current !== serial) return;
      if (!isDetail(value, node.id))
        throw Error("Agent 详情格式不完整，未启用配置编辑。请检查控制台版本。");
      setDetail(value);
      if (!preserve || !current.current.draft) setDraft(draftOf(value));
      if (current.current.review !== "none") {
        const matches =
          !!current.current.draft &&
          Object.keys(changesOf(current.current.draft, value)).length === 0;
        setReview(matches ? "none" : "confirm");
        setMatchedReadback(matches);
        setConfirmed(false);
      }
    } catch (reason) {
      if (!active.current || sequence.current !== serial) return;
      setError(
        controller.signal.aborted
          ? "读取 Agent 详情超时，请重试。"
          : (reason as Error).message,
      );
    } finally {
      clearTimeout(timer);
      if (active.current && sequence.current === serial) setLoading(false);
    }
  };
  useEffect(() => {
    active.current = true;
    void load(reloadRevision > 0);
    return () => {
      active.current = false;
      ++sequence.current;
      request.current?.abort();
      mutationRequest.current?.abort();
    };
  }, [node.id, reloadRevision]);

  const update = (field: keyof Draft, value: string) => {
    setDraft((previous) =>
      previous ? { ...previous, [field]: value } : previous,
    );
    setSaved(false);
    setMatchedReadback(false);
  };
  const reset = () => {
    if (detail) setDraft(draftOf(detail));
    setError("");
    setReview("none");
    setConfirmed(false);
    setSaved(false);
    setMatchedReadback(false);
  };
  const save = async () => {
    if (!detail || !draft || !canAdminister || mutationRequest.current || loading || !dirty)
      return;
    setError("");
    setSaved(false);
    if (!draft.name.trim()) {
      setError("请填写 Agent 名称。");
      return;
    }
    if (deniedChanges) {
      setError("当前输入包含已被锁定的字段，请还原修改后重新编辑。");
      return;
    }
    if (review === "reload" || (review === "confirm" && !confirmed)) return;
    setSaving(true);
    const controller = new AbortController();
    mutationRequest.current = controller;
    const timer = setTimeout(() => controller.abort(), 20_000);
    try {
      const value = await api<NodeDetail>(
        `/nodes/${encodeURIComponent(node.id)}`,
        {
          method: "PATCH",
          signal: controller.signal,
          body: JSON.stringify({
            expected_revision: detail.configuration_revision,
            ...changes,
          }),
        },
      );
      if (!active.current) return;
      if (!isDetail(value, node.id))
        throw Object.assign(
          Error("配置可能已保存，但响应格式不完整。请读取最新配置核对。"),
          { outcomeUnknown: true },
        );
      setDetail(value);
      setDraft(draftOf(value));
      setReview("none");
      setConfirmed(false);
      setSaved(true);
      onSaved(value);
    } catch (reason) {
      if (!active.current) return;
      const failure = reason as Error & {
        status?: number;
        outcomeUnknown?: boolean;
      };
      if (failure.status === 409 || failure.outcomeUnknown) {
        setReview("reload");
        setConfirmed(false);
        setError(
          failure.status === 409
            ? `${failure.message} 输入已保留，请读取最新配置并核对后再保存。`
            : "请求结果尚未确认，输入已保留。请读取最新配置核对，系统不会自动重发。",
        );
      } else setError(failure.message);
    } finally {
      clearTimeout(timer);
      if (mutationRequest.current === controller)
        mutationRequest.current = null;
      if (active.current) setSaving(false);
    }
  };
  const switchTab = (next: Tab) => {
    setTab(next);
  };
  const close = () => {
    if (mutationRequest.current) return;
    if (dirty) setDiscard(true);
    else onClose();
  };
  return (
    <>
      <Modal
        title="Agent 详情与配置"
        contentKey={`${node.id}:${tab}`}
        wide
        onClose={close}
        closeDisabled={saving}
        footer={
          canAdminister && tab === "registration" ? (
            <UiElement as="div" className="agent-details-footer">
              <UiElement as="span" className="agent-details-footer-state" role="status">
                {saving
                  ? "正在保存平台登记信息…"
                  : saved
                    ? "登记信息已保存"
                    : matchedReadback
                      ? "最新登记记录与当前输入一致"
                      : dirty
                        ? "有未保存的登记信息"
                        : detail
                          ? "登记信息未修改"
                          : "详情尚未加载"}
              </UiElement>
              <UiElement as="div" className="agent-details-footer-actions">
                <Button actionId="action.AgentDetails.001" onClick={close} disabled={saving}>
                  <UiText>{"取消"}</UiText>
                </Button>
                <Button actionId="action.AgentDetails.002"
                  onClick={reset}
                  disabled={
                    !detail ||
                    saving ||
                    loading ||
                    (!dirty && review === "none")
                  }
                >
                  <UiText>{"还原修改"}</UiText>
                </Button>
                <Button actionId="action.AgentDetails.003"
                  kind="primary"
                  loading={saving}
                  disabled={
                    !detail ||
                    loading ||
                    !dirty ||
                    !!deniedChanges ||
                    review === "reload" ||
                    (review === "confirm" && !confirmed)
                  }
                  onClick={() => void save()}
                >
                  <UiText>{"保存登记信息"}</UiText>
                </Button>
              </UiElement>
            </UiElement>
          ) : undefined
        }
      >
        <UiElement as="div" className="agent-details">
          <UiElement as="div" className="agent-details-summary">
            <UiElement as="span" className="agent-details-symbol">
              <Server size={24} />
            </UiElement>
            <UiElement as="div">
              <UiElement as="b">{detail?.name || node.name}</UiElement>
              <UiElement as="span">
                <UiText>{"执行 Agent ·"}</UiText> <UiElement as="code">{node.id}</UiElement>
                {detail && ` · 配置版本 ${detail.configuration_revision}`}
              </UiElement>
            </UiElement>
            <Badge status={detail?.status || node.status} />
          </UiElement>
          <UiElement as="div"
            className="agent-details-tabs"
            role="tablist"
            aria-label="Agent 详情分类"
          >
            {tabs.map((item, index) => (
              <UiButton actionId="action.AgentDetails.004"
                key={item.id}
                id={`${id}-${item.id}`}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                aria-controls={`${id}-panel`}
                tabIndex={tab === item.id ? 0 : -1}
                onClick={() => switchTab(item.id)}
                onKeyDown={(event) => {
                  let target = index;
                  if (event.key === "ArrowRight")
                    target = (index + 1) % tabs.length;
                  else if (event.key === "ArrowLeft")
                    target = (index + tabs.length - 1) % tabs.length;
                  else if (event.key === "Home") target = 0;
                  else if (event.key === "End") target = tabs.length - 1;
                  else return;
                  event.preventDefault();
                  switchTab(tabs[target].id);
                  document.getElementById(`${id}-${tabs[target].id}`)?.focus();
                }}
              >
                <item.icon size={15} />
                {item.label}
              </UiButton>
            ))}
            <UiButton actionId="action.AgentDetails.005"
              className="agent-details-reload"
              type="button"
              disabled={loading || saving}
              onClick={() => void load(true)}
              aria-label="读取最新 Agent 配置"
            >
              <RefreshCw size={15} />
              <UiElement as="span"><UiText>{"读取最新"}</UiText></UiElement>
            </UiButton>
          </UiElement>
          {dirty && tab !== "registration" && (
            <UiElement as="div" className="agent-details-pending" role="status">
              <UiElement as="span"><UiText>{"登记信息有未保存的修改。"}</UiText></UiElement>
              <UiButton actionId="action.AgentDetails.006" type="button" onClick={() => switchTab("registration")}>
                <UiText>{"返回登记信息"}</UiText>
              </UiButton>
            </UiElement>
          )}
          {error && <ErrorBox message={error} />}
          {loading && <Spinner label="正在读取 Agent 详情" />}
          {!loading && !detail && (
            <UiElement as="div" className="agent-details-load-failure">
              <UiElement as="p"><UiText>{"未取得可信的配置详情，暂不允许编辑。"}</UiText></UiElement>
              <Button actionId="action.AgentDetails.007" onClick={() => void load(false)}><UiText>{"重新读取详情"}</UiText></Button>
            </UiElement>
          )}
          {detail && draft && !loading && (
            <UiElement as="div"
              id={`${id}-panel`}
              role="tabpanel"
              aria-labelledby={`${id}-${tab}`}
              tabIndex={0}
              className="agent-details-content"
              key={tab}
            >
              {tab === "registration" && (
                <>
                  <InfoBox>
                    <UiText>{"这里修改平台登记信息，不会更改远端 Agent 的运行参数、现有容器名称或原生模型配置。"}</UiText>
                    {!canAdminister &&
                      " 当前账号仅可查看，修改需要系统管理员权限。"}
                  </InfoBox>
                  <UiElement as="div" className="form-two">
                    <Field
                      label="Agent 名称"
                      required
                      hint="用于控制台识别，保留节点 ID 与已有服务关联"
                    >
                      <UiElement as="input"
                        aria-label="Agent 名称"
                        value={draft.name}
                        maxLength={100}
                        disabled={!allowed("name")}
                        onChange={(event) => update("name", event.target.value)}
                      />
                    </Field>
                    <Field label="节点标签" hint="用逗号分隔，可用于分类与搜索">
                      <UiElement as="input"
                        aria-label="节点标签"
                        value={draft.labels}
                        maxLength={2200}
                        disabled={!allowed("labels")}
                        onChange={(event) =>
                          update("labels", event.target.value)
                        }
                        placeholder="production, ascend-a3"
                      />
                    </Field>
                  </UiElement>
                  <UiRegion name="layout.panel" as="section" className="agent-details-section">
                    <UiElement as="h3"><UiText>{"模型部署网络"}</UiText></UiElement>
                    <UiElement as="p">
                      <UiText>{"模型的部署与通信使用以下 IP 和网卡；Agent 的管理连接 URL 在“连接信息”中单独维护。"}</UiText>
                    </UiElement>
                    <UiElement as="div" className="form-two">
                      <Field
                        label="模型部署 IPv4 地址"
                        required
                        hint={
                          detail.edit_policy.address.allowed
                            ? "供后续部署与网络校验使用，不是 Agent 访问 URL"
                            : detail.edit_policy.address.reason ||
                              "当前不可修改部署地址"
                        }
                      >
                        <UiElement as="input"
                          aria-label="模型部署 IPv4 地址"
                          value={draft.address}
                          maxLength={64}
                          disabled={!allowed("address")}
                          onChange={(event) =>
                            update("address", event.target.value)
                          }
                        />
                      </Field>
                      <Field
                        label="指定通信网卡"
                        hint={
                          detail.edit_policy.nic_name.allowed
                            ? "留空表示自动使用 Agent 最近上报的网卡"
                            : detail.edit_policy.nic_name.reason ||
                              "当前不可修改网卡"
                        }
                      >
                        <UiElement as="input"
                          aria-label="指定通信网卡"
                          value={draft.nic_name}
                          maxLength={15}
                          disabled={!allowed("nic_name")}
                          onChange={(event) =>
                            update("nic_name", event.target.value)
                          }
                          placeholder="自动使用上报网卡"
                        />
                      </Field>
                    </UiElement>
                    <UiElement as="dl" className="agent-details-facts compact">
                      <Fact
                        label="网卡选择策略"
                        value={
                          knownText(detail.configured_nic_name)
                            ? "使用登记配置"
                            : "跟随 Agent 上报"
                        }
                      />
                      <Fact
                        label="登记网卡"
                        value={detail.configured_nic_name}
                      />
                      <Fact
                        label="Agent 上报网卡"
                        value={detail.reported_nic_name}
                      />
                    </UiElement>
                    {knownText(detail.configured_nic_name) &&
                      !knownText(detail.reported_nic_name) && (
                        <UiElement as="p" className="agent-details-network-note">
                          <UiText>{"登记值用于后续部署配置；宿主接口的实际采样由 Agent 提供。"}</UiText>
                        </UiElement>
                      )}
                    {(!detail.edit_policy.address.allowed ||
                      !detail.edit_policy.nic_name.allowed) && (
                      <UiElement as="p" className="agent-details-policy">
                        <UiText>{"锁定部署 IP 或网卡是为了保护后续部署与管理指向；Agent 不在正常推理请求链路中。"}</UiText>
                      </UiElement>
                    )}
                  </UiRegion>
                  {(detail.references.services.length > 0 ||
                    detail.references.jobs.length > 0 ||
                    (detail.references.resource_claim_count ?? 0) > 0) && (
                    <UiRegion name="layout.panel" as="section" className="agent-details-section">
                      <UiElement as="h3"><UiText>{"已有引用"}</UiText></UiElement>
                      <UiElement as="p">
                        <UiText>{"名称与标签可独立维护；部署网络的修改条件由服务与活动任务状态共同校验。"}</UiText>
                      </UiElement>
                      <UiElement as="p">
                        <UiText>{"服务引用"}</UiText>{" "}
                        {detail.references.service_count ??
                          detail.references.services.length}{" "}
                        <UiText>{"项 · 活动任务"}</UiText>{" "}
                        {detail.references.job_count ??
                          detail.references.jobs.length}{" "}
                        <UiText>{"项"}</UiText>
                        {typeof detail.references.resource_claim_count ===
                          "number" &&
                          ` · 未释放的资源声明 ${detail.references.resource_claim_count} 项`}
                      </UiElement>
                      <UiElement as="div" className="agent-details-references">
                        {detail.references.services.map((service) => (
                          <UiElement as="div" key={service.id}>
                            <UiElement as="span"><UiText>{"推理服务"}</UiText></UiElement>
                            <UiElement as="b">{service.name || service.id}</UiElement>
                            <Badge status={service.status} />
                            {service.resources_may_exist && (
                              <UiElement as="small"><UiText>{"可能保留资源"}</UiText></UiElement>
                            )}
                          </UiElement>
                        ))}
                        {detail.references.jobs.map((job) => (
                          <UiElement as="div" key={job.id}>
                            <UiElement as="span"><UiText>{"活动任务"}</UiText></UiElement>
                            <UiElement as="code">{job.id}</UiElement>
                            <Badge status={job.status} />
                          </UiElement>
                        ))}
                      </UiElement>
                    </UiRegion>
                  )}
                  {review === "confirm" && (
                    <UiElement as="label" className="agent-details-review">
                      <UiElement as="input"
                        type="checkbox"
                        checked={confirmed}
                        onChange={(event) => setConfirmed(event.target.checked)}
                      />
                      <UiText>{"已读取最新版本并核对，按当前输入保存"}</UiText>
                    </UiElement>
                  )}
                </>
              )}
              {tab === "connection" && (
                <>
                  <InfoBox>
                    <UiText>{"Agent 提供管理、部署与观测接口，通常不参与模型正常推理请求转发。连接 URL 与模型部署 IP 可属于不同的网络。"}</UiText>
                  </InfoBox>
                  <UiElement as="dl" className="agent-details-facts">
                    <Fact
                      label="Agent 连接地址（URL）"
                      value={
                        <UiElement as="span" className="tabular">{detail.agent_url}</UiElement>
                      }
                    />
                    <Fact
                      label="访问令牌"
                      value={
                        detail.credential_configured
                          ? "已配置 · 不回显"
                          : "未配置"
                      }
                    />
                    {typeof detail.agent_metadata?.transport_security?.tls_enabled === "boolean" &&
                      <Fact label="Agent 传输安全" value={
                        !detail.agent_metadata.transport_security.tls_enabled ? "HTTP · Agent 未启用 TLS" :
                          detail.agent_metadata.transport_security.client_certificate_required === true
                            ? "mTLS · 强制校验客户端证书" : "HTTPS · 服务端证书与访问令牌"
                      } />}
                  </UiElement>
                  <UiElement as="div" className="agent-details-connection-summary">
                    {connectionSummary(detail)}
                  </UiElement>
                  <UiRegion name="layout.panel" as="section" className="agent-details-section agent-details-connection-action">
                    <UiElement as="div">
                      <UiElement as="h3"><UiText>{"连接与凭据轮换"}</UiText></UiElement>
                      <UiElement as="p">
                        <UiText>{"新令牌仅用于本次校验后保存；不写入浏览器存储。更新连接前会校验目标身份与已有引用。"}</UiText>
                      </UiElement>
                    </UiElement>
                    {canAdminister ? (
                      <Button actionId="action.AgentDetails.008"
                        disabled={!allowed("credentials")}
                        onClick={() => onCredentials(detail)}
                      >
                        <UiText>{"更新连接与凭据"}</UiText>
                      </Button>
                    ) : (
                      <UiElement as="span" className="muted-text"><UiText>{"当前账号只读"}</UiText></UiElement>
                    )}
                  </UiRegion>
                  {canAdminister && !detail.edit_policy.credentials.allowed && (
                    <UiElement as="p" className="agent-details-policy">
                      {detail.edit_policy.credentials.reason}
                    </UiElement>
                  )}
                  {canAdminister && !detail.edit_policy.agent_url.allowed && (
                    <UiElement as="p" className="agent-details-policy">
                      {detail.edit_policy.agent_url.reason}
                    </UiElement>
                  )}
                </>
              )}
              {tab === "runtime" && (
                <>
                  <UiElement as="div" className="agent-details-observation">
                    <Badge
                      status={
                        nodeObservation(detail, now, demo).state === "fresh"
                          ? "online"
                          : "unknown"
                      }
                    >
                      {nodeObservation(detail, now, demo).state === "fresh"
                        ? demo
                          ? "演示采样"
                          : "采样已校验"
                        : resourceNotice(detail, now, demo).title}
                    </Badge>
                    {detail.collected_at &&
                      Number.isFinite(Date.parse(detail.collected_at)) && (
                        <UiElement as="span">
                          <UiText>{"采于"}</UiText> {observationAge(detail.collected_at, now)}
                        </UiElement>
                      )}
                  </UiElement>
                  <UiElement as="p" className="agent-details-runtime-note">
                    <UiText>{"以下信息来自最近一次 Agent 上报。读取登记配置不会触发宿主采样，可在节点列表执行连接探测更新。"}</UiText>
                  </UiElement>
                  {![
                    detail.architecture,
                    detail.os,
                    detail.docker_version,
                    detail.kubernetes_version,
                    detail.host_identity,
                    detail.daemon_id,
                  ].some(knownText) && (
                    <UiElement as="div" className="agent-runtime-setup">
                      <Activity size={24} />
                      <UiElement as="div">
                        <UiElement as="b"><UiText>{"运行信息待采集"}</UiText></UiElement>
                        <UiElement as="p">
                          <UiText>{"先检查 Agent 连接，连接探测成功后将展示已采集的运行环境。"}</UiText>
                        </UiElement>
                      </UiElement>
                      <Button actionId="action.AgentDetails.009" onClick={() => setTab("connection")}>
                        <UiText>{"检查连接配置"}</UiText>
                      </Button>
                    </UiElement>
                  )}
                  <UiElement as="dl" className="agent-details-facts">
                    <Fact label="处理器架构" value={detail.architecture} />
                    <Fact label="操作系统" value={detail.os} />
                    <Fact label="Docker 版本" value={detail.docker_version} />
                    <Fact
                      label="Kubernetes 版本"
                      value={detail.kubernetes_version}
                    />
                    <Fact
                      label="登记通信网卡"
                      value={detail.configured_nic_name}
                    />
                    <Fact
                      label="Agent 上报网卡"
                      value={detail.reported_nic_name}
                    />
                    {knownText(detail.host_identity) && (
                      <Fact
                        label="宿主身份"
                        value={<UiElement as="code">{detail.host_identity}</UiElement>}
                      />
                    )}
                    {knownText(detail.daemon_id) && (
                      <Fact
                        label="Docker daemon"
                        value={<UiElement as="code">{detail.daemon_id}</UiElement>}
                      />
                    )}
                    <Fact
                      label="硬件指标来源"
                      value={detail.npu_telemetry?.source}
                    />
                    {detail.collected_at &&
                      Number.isFinite(Date.parse(detail.collected_at)) && (
                        <Fact
                          label="资源采集时间"
                          value={when(detail.collected_at)}
                        />
                      )}
                    {detail.expires_at &&
                      Number.isFinite(Date.parse(detail.expires_at)) && (
                        <Fact
                          label="资源有效期"
                          value={when(detail.expires_at)}
                        />
                      )}
                    {detail.last_seen &&
                      Number.isFinite(Date.parse(detail.last_seen)) && (
                        <Fact
                          label="最近连接探测"
                          value={when(detail.last_seen)}
                        />
                      )}
                  </UiElement>
                  {detail.agent_metadata &&
                    (knownText(detail.agent_metadata.agent_version) ||
                      isResourceInteger(detail.agent_metadata.agent_protocol) ||
                      isResourceInteger(
                        detail.agent_metadata.operation_protocol,
                      ) ||
                      !!detail.agent_metadata.supported_runtimes?.filter(
                        knownText,
                      ).length ||
                      knownText(detail.agent_metadata.motor_profile?.driver) ||
                      knownText(
                        detail.agent_metadata.motor_profile?.commit,
                      )) && (
                      <UiRegion name="layout.panel" as="section" className="agent-details-section">
                        <UiElement as="h3"><UiText>{"Agent 兼容信息"}</UiText></UiElement>
                        <UiElement as="dl" className="agent-details-facts">
                          <Fact
                            label="Agent 版本"
                            value={detail.agent_metadata.agent_version}
                          />
                          {typeof detail.agent_metadata.read_only === "boolean" && <Fact label="执行权限" value={isReadOnlyAgent(detail) ? "只读接入 · 采集与查看" : "已启用管理 · 仍需最终准入"} />}
                          {isResourceInteger(
                            detail.agent_metadata.agent_protocol,
                          ) && (
                            <Fact
                              label="管理协议"
                              value={detail.agent_metadata.agent_protocol}
                            />
                          )}
                          {isResourceInteger(
                            detail.agent_metadata.operation_protocol,
                          ) && (
                            <Fact
                              label="操作协议"
                              value={detail.agent_metadata.operation_protocol}
                            />
                          )}
                          <Fact
                            label="支持的运行时"
                            value={detail.agent_metadata.supported_runtimes
                              ?.filter(knownText)
                              .join(" / ")}
                          />
                          <Fact
                            label="适配驱动"
                            value={detail.agent_metadata.motor_profile?.driver}
                          />
                          {knownText(
                            detail.agent_metadata.motor_profile?.commit,
                          ) && (
                            <Fact
                              label="原生适配基线"
                              value={
                                <UiElement as="code">
                                  {detail.agent_metadata.motor_profile.commit}
                                </UiElement>
                              }
                            />
                          )}
                        </UiElement>
                      </UiRegion>
                    )}
                  {detail.error && <ErrorBox message={detail.error} />}
                  {!!detail.collection_errors?.length && (
                    <UiElement as="div" className="agent-details-collection-errors">
                      {detail.collection_errors.map((item, index) => (
                        <UiElement as="p" key={index}>
                          <UiElement as="b">{item.source}</UiElement> <UiText>{"·"}</UiText> {item.message}
                        </UiElement>
                      ))}
                    </UiElement>
                  )}
                </>
              )}
            </UiElement>
          )}
        </UiElement>
      </Modal>
      {discard && (
        <Confirm
          title="放弃未保存的登记修改？"
          description="当前输入尚未保存。关闭后将丢弃这份草稿，已保存的节点与服务保持原状。"
          confirmText="放弃修改并关闭"
          danger={false}
          onClose={() => setDiscard(false)}
          onConfirm={onClose}
        />
      )}
    </>
  );
}

function factAvailable(value: ReactNode): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return knownText(value);
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value === "boolean") return true;
  if (Array.isArray(value)) return value.some(factAvailable);
  if (isValidElement<{ children?: ReactNode }>(value))
    return factAvailable(value.props.children);
  return false;
}

function DefaultFact({ label, value }: { label: string; value: ReactNode }) {
  if (!factAvailable(value)) return null;
  return (
    <UiElement as="div">
      <UiElement as="dt">{label}</UiElement>
      <UiElement as="dd">{typeof value === "boolean" ? (value ? "是" : "否") : value}</UiElement>
    </UiElement>
  );
}

const AgentDetails = withUiSlot("component.AgentDetails", DefaultAgentDetails);
export default AgentDetails;
const Fact = withUiSlot("component.Fact", DefaultFact);

// Reference package implementation exports.
export { DefaultAgentDetails, DefaultFact };
