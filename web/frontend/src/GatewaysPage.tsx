import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import { useCallback, useEffect, useRef, useState } from "react";
import { readCount, readNotice, type ReadState } from "./readState";
import { api, download, when } from "./api";
import type { Gateway, GatewayCheck, Json, Service, UserRole } from "./api";
import {
  ArrowUpRight,
  Download,
  Plus,
  RefreshCw,
  Settings2,
  Trash2,
} from "./icons";
import {
  Badge,
  Button,
  Confirm,
  Empty,
  ErrorBox,
  Field,
  InfoBox,
  Modal,
  SearchInput,
  Spinner,
} from "./ui";
import "./integration.css";
import { useUnsavedChanges } from "./unsavedChanges";

const checkLabel = {
  matched: "模型已匹配",
  discovered: "已读取模型",
  missing: "存在缺失模型",
  unreachable: "读取未成功",
};
const checkStatus = {
  matched: "pass",
  discovered: "pass",
  missing: "warn",
  unreachable: "fail",
};
const labelForCheck = (check: GatewayCheck) =>
  check.stale ? "配置已变更，需重新检查" : checkLabel[check.status];

function connectionAddress(value: string): URL | null {
  const candidate = value.trim();
  if (
    !/^https?:\/\//i.test(candidate) ||
    /[\u0000-\u0020\u007f\\%?#]/.test(candidate) ||
    /(?:^|\/)\.{1,2}(?:\/|$)/.test(candidate)
  )
    return null;
  try {
    const address = new URL(candidate);
    return address.hostname &&
      !address.username &&
      !address.password &&
      !address.search &&
      !address.hash &&
      /^(?:\/[A-Za-z0-9._~@+\-]+)*\/?$/.test(address.pathname)
      ? address
      : null;
  } catch {
    return null;
  }
}

type DirectExample = { serviceId: string; name: string; baseUrl: string };

function directServiceExamples(services: Service[]): DirectExample[] {
  return services.flatMap((service) => {
    // Kubernetes publication is verified by the independent endpoints adapter.
    if (service.status !== "running" || service.runtime !== "docker") return [];
    const address = connectionAddress(service.endpoint);
    if (!address) return [];
    const path = address.pathname.replace(/\/+$/, "");
    address.pathname = path.endsWith("/v1") ? path : `${path}/v1`;
    return [
      {
        serviceId: service.id,
        name: `${(service.model || service.name).slice(0, 88)} 原生服务入口`,
        baseUrl: address.toString().replace(/\/$/, ""),
      },
    ];
  });
}

function DefaultGatewayAuthorizationHelp({
  baseUrl,
  approved = false,
}: {
  baseUrl: string;
  approved?: boolean;
}) {
  const address = connectionAddress(baseUrl);
  return (
    <UiElement as="div" className="gateway-authorization-help">
      <UiElement as="p">
        {approved
          ? "该服务器地址已获检查授权。检查仅读取 /models，不修改网关路由。"
          : "保存连接不会自动开放网络访问；模型列表检查需由部署管理员授权服务器地址，不含 /v1 路径。"}
      </UiElement>
      {address && !approved && (
        <>
          <UiElement as="code" className="integration-code">
            <UiText>{"MOTOR_GATEWAY_ALLOWED_ORIGINS="}</UiText>{address.origin}
          </UiElement>
          <UiElement as="p">
            <UiText>{"将此地址追加到现有允许清单，多个地址用英文逗号分隔。部署配置生效后，刷新本页再检查连接。"}</UiText>
          </UiElement>
        </>
      )}
    </UiElement>
  );
}

function DefaultGatewayEditor({
  original,
  services,
  servicesRead,
  onClose,
  onSaved,
}: {
  original: Gateway | null;
  services: Service[];
  servicesRead: ReadState;
  onClose: () => void;
  onSaved: (gateway: Gateway) => void;
}) {
  const [name, setName] = useState(original?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(original?.base_url ?? "");
  const [serviceIds, setServiceIds] = useState(original?.service_ids ?? []);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const signature = JSON.stringify([name, baseUrl, serviceIds, apiKey, clearKey]);
  const baseline = useRef(signature);
  const dirty = signature !== baseline.current;
  useUnsavedChanges(dirty);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const examples = original ? [] : directServiceExamples(services);
  const [exampleId, setExampleId] = useState("");
  const [pendingExample, setPendingExample] = useState<DirectExample | null>(
    null,
  );
  const example =
    examples.find((item) => item.serviceId === exampleId) ?? examples[0];
  const active = useRef<AbortController | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      active.current?.abort();
    };
  }, []);
  const close = () => {
    if (busy) return;
    if (dirty) { setDiscardOpen(true); return; }
    setApiKey("");
    onClose();
  };
  const save = async () => {
    setError("");
    const gatewayName = name.trim();
    if (!gatewayName) {
      setError("请填写网关名称。");
      return;
    }
    const address = connectionAddress(baseUrl);
    if (!address) {
      setError(
        "请填写完整的 HTTP(S) API 基础地址，不含凭据、查询参数、片段或编码路径。",
      );
      return;
    }
    if (apiKey && address.protocol !== "https:") {
      setError("配置 API Key 的网关连接必须使用 HTTPS。");
      return;
    }
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    try {
      const body: Json = {
        name: gatewayName,
        base_url: baseUrl.trim(),
        service_ids: serviceIds,
      };
      if (apiKey) body.api_key = apiKey;
      if (original) {
        body.revision = original.revision;
        body.clear_api_key = clearKey;
      }
      const result = await api<Gateway>(
        original ? `/gateways/${encodeURIComponent(original.id)}` : "/gateways",
        {
          method: original ? "PUT" : "POST",
          body: JSON.stringify(body),
          signal: controller.signal,
        },
      );
      if (!alive.current || controller.signal.aborted) return;
      setApiKey("");
      onSaved(result);
    } catch (reason) {
      if (alive.current && !controller.signal.aborted)
        setError((reason as Error).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  const missingIds = serviceIds.filter(
    (id) => !services.some((service) => service.id === id),
  );
  const toggleService = (id: string, checked: boolean) =>
    setServiceIds((current) =>
      checked
        ? [...current.filter((value) => value !== id), id]
        : current.filter((value) => value !== id),
    );
  const applyExample = (candidate: DirectExample) => {
    const current = examples.find(
      (item) =>
        item.serviceId === candidate.serviceId &&
        item.baseUrl === candidate.baseUrl,
    );
    if (!current) {
      setPendingExample(null);
      setError("服务地址已变化，请重新选择填写示例。");
      return;
    }
    setName(current.name);
    setBaseUrl(current.baseUrl);
    setServiceIds([current.serviceId]);
    setApiKey("");
    setError("");
    setPendingExample(null);
  };
  return (
    <Modal
      title={original ? "编辑网关连接" : "接入已有网关"}
      subtitle="保存连接信息与模型绑定，随后检查网关对外模型列表。"
      onClose={close}
      footer={
        <>
          <Button actionId="action.GatewaysPage.001" onClick={close} disabled={busy}>
            <UiText>{"取消"}</UiText>
          </Button>
          <Button actionId="action.GatewaysPage.002" kind="primary" loading={busy} onClick={() => void save()}>
            <UiText>{"保存连接"}</UiText>
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {!original && (
        <UiElement as="details" className="gateway-entry-help">
          <UiElement as="summary"><UiText>{"填写示例与接入说明"}</UiText></UiElement>
          <UiElement as="div" className="gateway-entry-help-body">
            <UiElement as="p">
              <UiText>{"已有独立网关：填写网关实际提供的 OpenAI 兼容 API 基础地址。 未部署独立网关：可先用已运行服务的 Coordinator 地址检查模型列表。"}</UiText>
            </UiElement>
            {example ? (
              <>
                <Field label="选择服务直连示例">
                  <UiElement as="select"
                    aria-label="选择服务直连示例"
                    value={example.serviceId}
                    disabled={busy || !!pendingExample}
                    onChange={(event) => setExampleId(event.target.value)}
                  >
                    {examples.map((item) => (
                      <UiElement as="option" key={item.serviceId} value={item.serviceId}>
                        {
                          services.find(
                            (service) => service.id === item.serviceId,
                          )?.name
                        }
                      </UiElement>
                    ))}
                  </UiElement>
                </Field>
                <UiElement as="code" className="integration-code">{example.baseUrl}</UiElement>
                <UiElement as="p">
                  <UiText>{"直连示例会填入名称、地址并勾选该服务，API Key 留空。若服务已开启认证，请自行填写其凭据并使用 HTTPS。"}</UiText>
                </UiElement>
                {pendingExample ? (
                  <UiElement as="div"
                    className="gateway-example-confirm"
                    role="group"
                    aria-label="替换填写内容确认"
                  >
                    <UiElement as="p">
                      <UiText>{"将替换当前名称、地址和服务绑定，并清空已填写的 API Key；只修改表单，不会保存连接。"}</UiText>
                    </UiElement>
                    <UiElement as="div" className="integration-actions">
                      <Button actionId="action.GatewaysPage.003" onClick={() => setPendingExample(null)}>
                        <UiText>{"保留当前填写"}</UiText>
                      </Button>
                      <Button actionId="action.GatewaysPage.004"
                        kind="primary"
                        onClick={() => applyExample(pendingExample)}
                      >
                        <UiText>{"替换并填入"}</UiText>
                      </Button>
                    </UiElement>
                  </UiElement>
                ) : (
                  <Button actionId="action.GatewaysPage.005"
                    disabled={busy}
                    onClick={() => {
                      if (name || baseUrl || apiKey || serviceIds.length)
                        setPendingExample(example);
                      else applyExample(example);
                    }}
                  >
                    <UiText>{"填入服务直连示例"}</UiText>
                  </Button>
                )}
              </>
            ) : (
              <UiElement as="p">
                <UiText>{"运行中的服务尚未提供可用 API 地址。可填写独立网关地址，例如 https://gateway.example.com/v1。"}</UiText>
              </UiElement>
            )}
            <UiElement as="p" className="gateway-entry-boundary">
              <UiText>{"原生服务直连不是独立 AIGW。保存与绑定仅建立连接记录，不会安装网关、创建路由或启动模型。"}</UiText>
            </UiElement>
          </UiElement>
        </UiElement>
      )}
      <UiElement as="fieldset" className="gateway-editor-fields" disabled={busy}>
        <Field label="网关名称" required>
          <UiElement as="input"
            aria-label="网关名称"
            value={name}
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如 production-aigw"
          />
        </Field>
        <Field
          label="API 基础地址"
          required
          hint="填写网关的 OpenAI 兼容基础地址，例如 https://gateway.example.com/v1；检查时追加 /models。"
        >
          <UiElement as="input"
            aria-label="API 基础地址"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://gateway.example.com/v1"
          />
        </Field>
        <Field
          label="网关 API Key"
          hint={
            original?.has_api_key
              ? "已配置；留空保留原值。凭据保存后不回显。"
              : "未开启认证时留空；填写 API Key 时必须使用 HTTPS。"
          }
        >
          <UiElement as="input"
            aria-label="网关 API Key"
            type="password"
            autoComplete="new-password"
            value={apiKey}
            disabled={clearKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={
              original?.has_api_key ? "留空保留现有凭据" : "填写网关访问凭据"
            }
          />
        </Field>
        {original?.has_api_key && (
          <UiElement as="label" className="integration-checkbox">
            <UiElement as="input"
              type="checkbox"
              checked={clearKey}
              onChange={(event) => {
                setClearKey(event.target.checked);
                if (event.target.checked) setApiKey("");
              }}
            />
            <UiText>{"明确清除已保存的 API Key"}</UiText>
          </UiElement>
        )}
        <UiElement as="div" className="gateway-service-picker">
          <UiElement as="span" className="field-label"><UiText>{"绑定推理服务"}</UiText></UiElement>
          <UiElement as="p" className="integration-caption">
            <UiText>{"用于核对网关模型列表是否包含绑定服务的模型 ID；保存连接不会修改网关路由。"}</UiText>
          </UiElement>
          {!servicesRead.loaded ? <UiElement as="p" className="integration-caption" role="status">{readNotice(servicesRead)}<UiText>{"推理服务列表；暂不判断原绑定服务是否存在。"}</UiText></UiElement> : services.length || missingIds.length ? (
            <UiElement as="div" className="gateway-service-options">
              {services.map((service) => (
                <UiElement as="label" className="gateway-service-option" key={service.id}>
                  <UiElement as="input"
                    type="checkbox"
                    checked={serviceIds.includes(service.id)}
                    onChange={(event) =>
                      toggleService(service.id, event.target.checked)
                    }
                  />
                  <UiElement as="span">
                    <UiElement as="strong">{service.name}</UiElement>
                    <UiElement as="small">
                      {service.model ? `${service.model} · ` : ""}
                      {service.runtime === "docker" ? "Docker" : "Kubernetes"}
                    </UiElement>
                  </UiElement>
                </UiElement>
              ))}
              {missingIds.map((id) => (
                <UiElement as="label" className="gateway-service-option" key={id}>
                  <UiElement as="input"
                    type="checkbox"
                    checked
                    onChange={() => toggleService(id, false)}
                  />
                  <UiElement as="span">
                    <UiElement as="strong"><UiText>{"当前服务列表未包含该绑定"}</UiText></UiElement>
                    <UiElement as="small">{id}<UiText>{"；取消勾选后可移除此关联。"}</UiText></UiElement>
                  </UiElement>
                </UiElement>
              ))}
            </UiElement>
          ) : (
            <UiElement as="p" className="integration-caption">
              <UiText>{"暂无可绑定的推理服务，可以先保存网关连接。"}</UiText>
            </UiElement>
          )}
        </UiElement>
      </UiElement>
      <InfoBox>
        <GatewayAuthorizationHelp
          baseUrl={baseUrl}
          approved={
            !!original?.probe_allowed &&
            connectionAddress(original.base_url)?.origin ===
              connectionAddress(baseUrl)?.origin
          }
        />
      </InfoBox>
      {discardOpen && <Confirm title="放弃未保存的连接？" description="当前连接修改与尚未保存的凭据将被清除。" confirmText="放弃更改" onClose={() => setDiscardOpen(false)} onConfirm={() => { setApiKey(""); onClose(); }} />}
    </Modal>
  );
}

function DefaultGatewaysPage({
  services,
  servicesRead,
  role,
  notify = () => {},
}: {
  services: Service[];
  servicesRead: ReadState;
  role: UserRole;
  notify?: (message: string) => void;
}) {
  const [items, setItems] = useState<Gateway[]>([]);
  const [hasSuccess, setHasSuccess] = useState(false);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [editor, setEditor] = useState<Gateway | "new" | null>(null);
  const [remove, setRemove] = useState<Gateway | null>(null);
  const [details, setDetails] = useState<Gateway | null>(null);
  const generation = useRef(0);
  const active = useRef<AbortController | null>(null);
  const actionGeneration = useRef(0);
  const actionAbort = useRef<AbortController | null>(null);
  const canManage = role === "admin";
  const canProbe = role === "admin" || role === "operator";
  const cancelLoad = useCallback(() => {
    generation.current++;
    active.current?.abort();
  }, []);
  const load = useCallback(async () => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const current = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const result = await api<{ items: Gateway[] }>("/gateways", {
        signal: controller.signal,
      });
      if (!controller.signal.aborted && current === generation.current) {
        setItems(result.items);
        setHasSuccess(true);
      }
    } catch (reason) {
      if (!controller.signal.aborted && current === generation.current) {
        setError((reason as Error).message);
      }
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    setItems([]);
    setHasSuccess(false);
    setEditor(null);
    setRemove(null);
    setDetails(null);
    setActionError("");
    setBusyId("");
    actionGeneration.current++;
    actionAbort.current?.abort();
    void load();
    return () => {
      cancelLoad();
      actionGeneration.current++;
      actionAbort.current?.abort();
    };
  }, [role, load, cancelLoad]);
  const upsert = (gateway: Gateway) => {
    cancelLoad();
    setLoading(false);
    setItems((current) =>
      current.some((item) => item.id === gateway.id)
        ? current.map((item) => (item.id === gateway.id ? gateway : item))
        : [...current, gateway],
    );
    setDetails((current) => (current?.id === gateway.id ? gateway : current));
  };
  const perform = async (
    gateway: Gateway,
    operation: "probe" | "delete" | "export",
  ) => {
    if (busyId) return;
    const controller = new AbortController();
    actionAbort.current = controller;
    const current = ++actionGeneration.current;
    cancelLoad();
    setLoading(false);
    setBusyId(gateway.id);
    setActionError("");
    try {
      const path = `/gateways/${encodeURIComponent(gateway.id)}`;
      if (operation === "probe") {
        const result = await api<Gateway>(`${path}/probe`, {
          method: "POST",
          body: JSON.stringify({ revision: gateway.revision }),
          signal: controller.signal,
        });
        if (current !== actionGeneration.current || controller.signal.aborted)
          return;
        upsert(result);
        notify("网关模型列表检查已完成");
      } else if (operation === "delete") {
        await api(`${path}?revision=${gateway.revision}`, {
          method: "DELETE",
          signal: controller.signal,
        });
        if (current !== actionGeneration.current || controller.signal.aborted)
          return;
        setItems((values) => values.filter((item) => item.id !== gateway.id));
        setRemove(null);
        if (details?.id === gateway.id) setDetails(null);
        notify("网关连接记录已移除");
      } else {
        const result = await api(`${path}/export`, {
          signal: controller.signal,
        });
        if (current !== actionGeneration.current || controller.signal.aborted)
          return;
        const fileName =
          gateway.name
            .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
            .slice(0, 80) || "gateway";
        download(result, `${fileName}-connection.json`);
        notify("连接描述已导出，不含凭据");
      }
    } catch (reason) {
      if (current === actionGeneration.current && !controller.signal.aborted)
        setActionError((reason as Error).message);
    } finally {
      if (current === actionGeneration.current) setBusyId("");
    }
  };
  const serviceName = (id: string) =>
    services.find((service) => service.id === id)?.name || id;
  const visible = items.filter((gateway) =>
    [gateway.name, gateway.base_url, ...gateway.service_ids.map(serviceName)]
      .join(" ")
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  return (
    <>
      <UiElement as="div" className="page-heading">
        <UiElement as="div">
          <UiElement as="h1"><UiText>{"网关接入"}</UiText></UiElement>
          <UiElement as="p"><UiText>{"维护已有 AIGW 实例的连接，核对网关对外模型与 Motor 服务的关联。"}</UiText></UiElement>
        </UiElement>
        <UiElement as="div" className="heading-actions">
          <Button actionId="action.GatewaysPage.006"
            onClick={() => void load()}
            disabled={Boolean(busyId)}
            loading={loading}
          >
            <RefreshCw size={14} />
            <UiText>{"刷新数据"}</UiText>
          </Button>
          {canManage && (
            <Button actionId="action.GatewaysPage.007"
              kind="primary"
              onClick={() => {
                setEditor("new");
                setActionError("");
              }}
            >
              <Plus size={14} />
              <UiText>{"接入网关"}</UiText>
            </Button>
          )}
        </UiElement>
      </UiElement>
      <InfoBox>
        <UiText>{"接入已部署的推理网关，校验可访问的模型列表并绑定推理服务。 保存连接后可按需检查，网关路由由其自身管理。"}</UiText>
      </InfoBox>
      {error && <ErrorBox message={error} />}
      {actionError && <ErrorBox message={actionError} />}
      <UiRegion name="layout.panel" as="section" className="panel gateway-list-panel">
        <UiElement as="div" className="gateway-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="搜索网关名称、地址或绑定服务"
          />
          <UiElement as="span" className="integration-caption">
            {readCount({ loaded: hasSuccess, refreshing: loading, error }, visible.length, "个连接")}
          </UiElement>
        </UiElement>
        {loading && !hasSuccess ? (
          <Spinner label="正在读取网关连接" />
        ) : visible.length ? (
          <UiElement as="div" className="table-wrap">
            <UiElement as="table">
              <UiElement as="thead">
                <UiElement as="tr">
                  <UiElement as="th" scope="col"><UiText>{"网关 / API 基础地址"}</UiText></UiElement>
                  <UiElement as="th" scope="col"><UiText>{"绑定服务"}</UiText></UiElement>
                  <UiElement as="th" scope="col"><UiText>{"上次模型核对"}</UiText></UiElement>
                  <UiElement as="th" scope="col"><UiText>{"访问凭据"}</UiText></UiElement>
                  <UiElement as="th" scope="col"><UiText>{"操作"}</UiText></UiElement>
                </UiElement>
              </UiElement>
              <UiElement as="tbody">
                {visible.map((gateway) => (
                  <UiElement as="tr" key={gateway.id}>
                    <UiElement as="td">
                      <UiButton actionId="action.GatewaysPage.008"
                        className="gateway-name-link"
                        onClick={() => {
                          setDetails(gateway);
                          setActionError("");
                        }}
                      >
                        {gateway.name}
                        <ArrowUpRight size={13} />
                      </UiButton>
                      <UiElement as="code" className="integration-code">
                        {gateway.base_url}
                      </UiElement>
                      <UiElement as="span" className="integration-caption">
                        <UiText>{"修订"}</UiText> {gateway.revision}
                      </UiElement>
                    </UiElement>
                    <UiElement as="td">
                      {gateway.service_ids.length ? (
                        gateway.service_ids.map((id) => (
                          <UiElement as="div" className="gateway-service-name" key={id}>
                            {serviceName(id)}
                          </UiElement>
                        ))
                      ) : (
                        <UiElement as="span" className="integration-caption">
                          <UiText>{"尚未绑定服务"}</UiText>
                        </UiElement>
                      )}
                    </UiElement>
                    <UiElement as="td">
                      {gateway.last_check ? (
                        <>
                          <Badge
                            status={checkStatus[gateway.last_check.status]}
                          >
                            {labelForCheck(gateway.last_check)}
                          </Badge>
                          <UiElement as="div" className="integration-caption">
                            {when(gateway.last_check.checked_at)}
                          </UiElement>
                        </>
                      ) : (
                        <UiElement as="span" className="integration-caption"><UiText>{"待检查"}</UiText></UiElement>
                      )}
                      {!gateway.probe_allowed && (
                        <UiElement as="details" className="gateway-authorization-inline">
                          <UiElement as="summary"><UiText>{"检查地址尚未授权 · 查看配置方法"}</UiText></UiElement>
                          <GatewayAuthorizationHelp
                            baseUrl={gateway.base_url}
                          />
                        </UiElement>
                      )}
                    </UiElement>
                    <UiElement as="td">{gateway.has_api_key ? "已配置" : "未配置"}</UiElement>
                    <UiElement as="td">
                      <UiElement as="div" className="gateway-row-actions">
                        {canProbe && (
                          <Button actionId="action.GatewaysPage.009"
                            kind="text"
                            disabled={Boolean(busyId) || !gateway.probe_allowed}
                            loading={busyId === gateway.id}
                            title={
                              gateway.probe_allowed
                                ? "读取网关 /models 并核对绑定服务模型"
                                : `需由部署管理员授权 ${connectionAddress(gateway.base_url)?.origin ?? "该服务器地址"}`
                            }
                            onClick={() => void perform(gateway, "probe")}
                          >
                            <UiText>{"检查连接"}</UiText>
                          </Button>
                        )}
                        <Button actionId="action.GatewaysPage.010"
                          kind="text"
                          disabled={Boolean(busyId)}
                          onClick={() => void perform(gateway, "export")}
                        >
                          <Download size={13} />
                          <UiText>{"导出连接"}</UiText>
                        </Button>
                        {canManage && (
                          <>
                            <Button actionId="action.GatewaysPage.011"
                              kind="text"
                              disabled={Boolean(busyId)}
                              onClick={() => {
                                setEditor(gateway);
                                setActionError("");
                              }}
                            >
                              <Settings2 size={13} />
                              <UiText>{"编辑"}</UiText>
                            </Button>
                            <Button actionId="action.GatewaysPage.012"
                              kind="text"
                              className="danger-text"
                              disabled={Boolean(busyId)}
                              onClick={() => {
                                setRemove(gateway);
                                setActionError("");
                              }}
                            >
                              <Trash2 size={13} />
                              <UiText>{"移除"}</UiText>
                            </Button>
                          </>
                        )}
                      </UiElement>
                    </UiElement>
                  </UiElement>
                ))}
              </UiElement>
            </UiElement>
          </UiElement>
        ) : (
          <Empty
            title={!hasSuccess ? readNotice({ loaded: false, refreshing: loading, error }) : query ? "没有匹配的网关连接" : "尚未接入网关"}
            description={
              !hasSuccess ? "尚未取得网关连接列表，请检查错误提示后重试。" : query
                ? "请调整搜索条件。"
                : canManage
                  ? "接入已有 AIGW API 地址，并绑定需要核对的 Motor 服务。"
                  : "管理员接入已有网关后，可在此查看连接和模型核对结果。"
            }
            action={
              !query && canManage ? (
                <Button actionId="action.GatewaysPage.013" kind="primary" onClick={() => setEditor("new")}>
                  <UiText>{"接入已有网关"}</UiText>
                </Button>
              ) : undefined
            }
          />
        )}
      </UiRegion>
      {editor && canManage && (
        <GatewayEditor
          key={editor === "new" ? "new" : `${editor.id}:${editor.revision}`}
          original={editor === "new" ? null : editor}
          services={services}
          servicesRead={servicesRead}
          onClose={() => setEditor(null)}
          onSaved={(gateway) => {
            upsert(gateway);
            setEditor(null);
            notify("网关连接已保存");
          }}
        />
      )}
      {remove && canManage && (
        <Confirm
          title="移除网关连接"
          confirmText="移除连接记录"
          busy={busyId === remove.id}
          error={actionError}
          onClose={() => {
            if (!busyId) setRemove(null);
          }}
          onConfirm={() => void perform(remove, "delete")}
          description={
            <>
              <UiText>{"仅移除控制台中的“"}</UiText>{remove.name}
              <UiText>{"”连接记录。网关进程、路由配置和推理服务继续运行。"}</UiText>
            </>
          }
        />
      )}
      {details && (
        <Modal
          title={details.name}
          subtitle="连接记录与最近一次模型核对；不代表网关进程部署状态。"
          onClose={() => setDetails(null)}
          wide
        >
          <UiElement as="dl" className="integration-facts">
            <UiElement as="dt"><UiText>{"API 基础地址"}</UiText></UiElement>
            <UiElement as="dd">
              <UiElement as="code" className="integration-code">{details.base_url}</UiElement>
            </UiElement>
            <UiElement as="dt"><UiText>{"绑定服务"}</UiText></UiElement>
            <UiElement as="dd">
              {details.service_ids.map(serviceName).join("、") || "尚未绑定"}
            </UiElement>
            <UiElement as="dt"><UiText>{"访问凭据"}</UiText></UiElement>
            <UiElement as="dd">{details.has_api_key ? "已保存，凭据不回显" : "未配置"}</UiElement>
            <UiElement as="dt"><UiText>{"探测授权"}</UiText></UiElement>
            <UiElement as="dd">
              {details.probe_allowed
                ? "服务器地址已获部署授权"
                : "服务器地址尚未获部署授权"}
            </UiElement>
            <UiElement as="dt"><UiText>{"最近更新"}</UiText></UiElement>
            <UiElement as="dd">
              {when(details.updated_at)} <UiText>{"· 修订"}</UiText> {details.revision}
            </UiElement>
          </UiElement>
          <GatewayAuthorizationHelp
            baseUrl={details.base_url}
            approved={details.probe_allowed}
          />
          <InfoBox>{details.deployment.message}</InfoBox>
          {details.last_check ? (
            <UiElement as="div" className="gateway-check-detail">
              <UiElement as="h3">
                <UiText>{"上次模型核对"}</UiText>{" "}
                <Badge status={checkStatus[details.last_check.status]}>
                  {labelForCheck(details.last_check)}
                </Badge>
              </UiElement>
              <UiElement as="p" className="integration-caption">
                {when(details.last_check.checked_at)} <UiText>{"·"}</UiText>{" "}
                {details.last_check.message}
              </UiElement>
              <UiElement as="p"><UiText>{"网关返回"}</UiText> {details.last_check.model_count} <UiText>{"个模型"}</UiText></UiElement>
              <UiElement as="div" className="gateway-model-list">
                {details.last_check.model_ids.map((id) => (
                  <UiElement as="code" key={id}>{id}</UiElement>
                ))}
              </UiElement>
              {details.last_check.bindings.map((binding) => (
                <UiElement as="div" className="gateway-binding-check" key={binding.service_id}>
                  <UiElement as="strong">{serviceName(binding.service_id)}</UiElement>
                  <UiElement as="div" className="integration-caption">
                    {details.last_check?.stale
                      ? "上次检查时的预期模型："
                      : "预期模型："}
                    {binding.expected_model_ids.join("、") || "尚未配置模型 ID"}
                  </UiElement>
                  <UiElement as="div" className="integration-caption">
                    {details.last_check?.stale
                      ? "绑定服务配置已变更，请重新检查后确认模型关联。"
                      : binding.status === "matched"
                        ? "网关列表包含预期模型"
                        : binding.status === "unconfigured"
                          ? "服务尚未配置可核对的模型 ID"
                          : `缺失模型：${binding.missing_model_ids.join("、")}`}
                  </UiElement>
                </UiElement>
              ))}
            </UiElement>
          ) : (
            <UiElement as="p" className="integration-caption">
              <UiText>{"尚未检查模型列表。连接保存不会自动修改网关路由。"}</UiText>
            </UiElement>
          )}
        </Modal>
      )}
    </>
  );
}

const GatewayAuthorizationHelp = withUiSlot("component.GatewayAuthorizationHelp", DefaultGatewayAuthorizationHelp);
const GatewayEditor = withUiSlot("component.GatewayEditor", DefaultGatewayEditor);
const GatewaysPage = withUiSlot("component.GatewaysPage", DefaultGatewaysPage);
export default GatewaysPage;

// Reference package implementation exports.
export { DefaultGatewayAuthorizationHelp, DefaultGatewayEditor, DefaultGatewaysPage };
