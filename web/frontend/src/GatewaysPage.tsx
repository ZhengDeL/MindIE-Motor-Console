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

function GatewayAuthorizationHelp({
  baseUrl,
  approved = false,
}: {
  baseUrl: string;
  approved?: boolean;
}) {
  const address = connectionAddress(baseUrl);
  return (
    <div className="gateway-authorization-help">
      <p>
        {approved
          ? "该服务器地址已获检查授权。检查仅读取 /models，不修改网关路由。"
          : "保存连接不会自动开放网络访问；模型列表检查需由部署管理员授权服务器地址，不含 /v1 路径。"}
      </p>
      {address && !approved && (
        <>
          <code className="integration-code">
            MOTOR_GATEWAY_ALLOWED_ORIGINS={address.origin}
          </code>
          <p>
            将此地址追加到现有允许清单，多个地址用英文逗号分隔。部署配置生效后，刷新本页再检查连接。
          </p>
        </>
      )}
    </div>
  );
}

function GatewayEditor({
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
          <Button onClick={close} disabled={busy}>
            取消
          </Button>
          <Button kind="primary" loading={busy} onClick={() => void save()}>
            保存连接
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {!original && (
        <details className="gateway-entry-help">
          <summary>填写示例与接入说明</summary>
          <div className="gateway-entry-help-body">
            <p>
              已有独立网关：填写网关实际提供的 OpenAI 兼容 API 基础地址。
              未部署独立网关：可先用已运行服务的 Coordinator 地址检查模型列表。
            </p>
            {example ? (
              <>
                <Field label="选择服务直连示例">
                  <select
                    aria-label="选择服务直连示例"
                    value={example.serviceId}
                    disabled={busy || !!pendingExample}
                    onChange={(event) => setExampleId(event.target.value)}
                  >
                    {examples.map((item) => (
                      <option key={item.serviceId} value={item.serviceId}>
                        {
                          services.find(
                            (service) => service.id === item.serviceId,
                          )?.name
                        }
                      </option>
                    ))}
                  </select>
                </Field>
                <code className="integration-code">{example.baseUrl}</code>
                <p>
                  直连示例会填入名称、地址并勾选该服务，API Key
                  留空。若服务已开启认证，请自行填写其凭据并使用 HTTPS。
                </p>
                {pendingExample ? (
                  <div
                    className="gateway-example-confirm"
                    role="group"
                    aria-label="替换填写内容确认"
                  >
                    <p>
                      将替换当前名称、地址和服务绑定，并清空已填写的 API
                      Key；只修改表单，不会保存连接。
                    </p>
                    <div className="integration-actions">
                      <Button onClick={() => setPendingExample(null)}>
                        保留当前填写
                      </Button>
                      <Button
                        kind="primary"
                        onClick={() => applyExample(pendingExample)}
                      >
                        替换并填入
                      </Button>
                    </div>
                  </div>
                ) : (
                  <Button
                    disabled={busy}
                    onClick={() => {
                      if (name || baseUrl || apiKey || serviceIds.length)
                        setPendingExample(example);
                      else applyExample(example);
                    }}
                  >
                    填入服务直连示例
                  </Button>
                )}
              </>
            ) : (
              <p>
                运行中的服务尚未提供可用 API 地址。可填写独立网关地址，例如
                https://gateway.example.com/v1。
              </p>
            )}
            <p className="gateway-entry-boundary">
              原生服务直连不是独立
              AIGW。保存与绑定仅建立连接记录，不会安装网关、创建路由或启动模型。
            </p>
          </div>
        </details>
      )}
      <fieldset className="gateway-editor-fields" disabled={busy}>
        <Field label="网关名称" required>
          <input
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
          <input
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
          <input
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
          <label className="integration-checkbox">
            <input
              type="checkbox"
              checked={clearKey}
              onChange={(event) => {
                setClearKey(event.target.checked);
                if (event.target.checked) setApiKey("");
              }}
            />
            明确清除已保存的 API Key
          </label>
        )}
        <div className="gateway-service-picker">
          <span className="field-label">绑定推理服务</span>
          <p className="integration-caption">
            用于核对网关模型列表是否包含绑定服务的模型
            ID；保存连接不会修改网关路由。
          </p>
          {!servicesRead.loaded ? <p className="integration-caption" role="status">{readNotice(servicesRead)}推理服务列表；暂不判断原绑定服务是否存在。</p> : services.length || missingIds.length ? (
            <div className="gateway-service-options">
              {services.map((service) => (
                <label className="gateway-service-option" key={service.id}>
                  <input
                    type="checkbox"
                    checked={serviceIds.includes(service.id)}
                    onChange={(event) =>
                      toggleService(service.id, event.target.checked)
                    }
                  />
                  <span>
                    <strong>{service.name}</strong>
                    <small>
                      {service.model ? `${service.model} · ` : ""}
                      {service.runtime === "docker" ? "Docker" : "Kubernetes"}
                    </small>
                  </span>
                </label>
              ))}
              {missingIds.map((id) => (
                <label className="gateway-service-option" key={id}>
                  <input
                    type="checkbox"
                    checked
                    onChange={() => toggleService(id, false)}
                  />
                  <span>
                    <strong>当前服务列表未包含该绑定</strong>
                    <small>{id}；取消勾选后可移除此关联。</small>
                  </span>
                </label>
              ))}
            </div>
          ) : (
            <p className="integration-caption">
              暂无可绑定的推理服务，可以先保存网关连接。
            </p>
          )}
        </div>
      </fieldset>
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

export default function GatewaysPage({
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
      <div className="page-heading">
        <div>
          <h1>网关接入</h1>
          <p>维护已有 AIGW 实例的连接，核对网关对外模型与 Motor 服务的关联。</p>
        </div>
        <div className="heading-actions">
          <Button
            onClick={() => void load()}
            disabled={Boolean(busyId)}
            loading={loading}
          >
            <RefreshCw size={14} />
            刷新数据
          </Button>
          {canManage && (
            <Button
              kind="primary"
              onClick={() => {
                setEditor("new");
                setActionError("");
              }}
            >
              <Plus size={14} />
              接入网关
            </Button>
          )}
        </div>
      </div>
      <InfoBox>
        接入已部署的推理网关，校验可访问的模型列表并绑定推理服务。
        保存连接后可按需检查，网关路由由其自身管理。
      </InfoBox>
      {error && <ErrorBox message={error} />}
      {actionError && <ErrorBox message={actionError} />}
      <section className="panel gateway-list-panel">
        <div className="gateway-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="搜索网关名称、地址或绑定服务"
          />
          <span className="integration-caption">
            {readCount({ loaded: hasSuccess, refreshing: loading, error }, visible.length, "个连接")}
          </span>
        </div>
        {loading && !hasSuccess ? (
          <Spinner label="正在读取网关连接" />
        ) : visible.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">网关 / API 基础地址</th>
                  <th scope="col">绑定服务</th>
                  <th scope="col">上次模型核对</th>
                  <th scope="col">访问凭据</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((gateway) => (
                  <tr key={gateway.id}>
                    <td>
                      <button
                        className="gateway-name-link"
                        onClick={() => {
                          setDetails(gateway);
                          setActionError("");
                        }}
                      >
                        {gateway.name}
                        <ArrowUpRight size={13} />
                      </button>
                      <code className="integration-code">
                        {gateway.base_url}
                      </code>
                      <span className="integration-caption">
                        修订 {gateway.revision}
                      </span>
                    </td>
                    <td>
                      {gateway.service_ids.length ? (
                        gateway.service_ids.map((id) => (
                          <div className="gateway-service-name" key={id}>
                            {serviceName(id)}
                          </div>
                        ))
                      ) : (
                        <span className="integration-caption">
                          尚未绑定服务
                        </span>
                      )}
                    </td>
                    <td>
                      {gateway.last_check ? (
                        <>
                          <Badge
                            status={checkStatus[gateway.last_check.status]}
                          >
                            {labelForCheck(gateway.last_check)}
                          </Badge>
                          <div className="integration-caption">
                            {when(gateway.last_check.checked_at)}
                          </div>
                        </>
                      ) : (
                        <span className="integration-caption">待检查</span>
                      )}
                      {!gateway.probe_allowed && (
                        <details className="gateway-authorization-inline">
                          <summary>检查地址尚未授权 · 查看配置方法</summary>
                          <GatewayAuthorizationHelp
                            baseUrl={gateway.base_url}
                          />
                        </details>
                      )}
                    </td>
                    <td>{gateway.has_api_key ? "已配置" : "未配置"}</td>
                    <td>
                      <div className="gateway-row-actions">
                        {canProbe && (
                          <Button
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
                            检查连接
                          </Button>
                        )}
                        <Button
                          kind="text"
                          disabled={Boolean(busyId)}
                          onClick={() => void perform(gateway, "export")}
                        >
                          <Download size={13} />
                          导出连接
                        </Button>
                        {canManage && (
                          <>
                            <Button
                              kind="text"
                              disabled={Boolean(busyId)}
                              onClick={() => {
                                setEditor(gateway);
                                setActionError("");
                              }}
                            >
                              <Settings2 size={13} />
                              编辑
                            </Button>
                            <Button
                              kind="text"
                              className="danger-text"
                              disabled={Boolean(busyId)}
                              onClick={() => {
                                setRemove(gateway);
                                setActionError("");
                              }}
                            >
                              <Trash2 size={13} />
                              移除
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
                <Button kind="primary" onClick={() => setEditor("new")}>
                  接入已有网关
                </Button>
              ) : undefined
            }
          />
        )}
      </section>
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
              仅移除控制台中的“{remove.name}
              ”连接记录。网关进程、路由配置和推理服务继续运行。
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
          <dl className="integration-facts">
            <dt>API 基础地址</dt>
            <dd>
              <code className="integration-code">{details.base_url}</code>
            </dd>
            <dt>绑定服务</dt>
            <dd>
              {details.service_ids.map(serviceName).join("、") || "尚未绑定"}
            </dd>
            <dt>访问凭据</dt>
            <dd>{details.has_api_key ? "已保存，凭据不回显" : "未配置"}</dd>
            <dt>探测授权</dt>
            <dd>
              {details.probe_allowed
                ? "服务器地址已获部署授权"
                : "服务器地址尚未获部署授权"}
            </dd>
            <dt>最近更新</dt>
            <dd>
              {when(details.updated_at)} · 修订 {details.revision}
            </dd>
          </dl>
          <GatewayAuthorizationHelp
            baseUrl={details.base_url}
            approved={details.probe_allowed}
          />
          <InfoBox>{details.deployment.message}</InfoBox>
          {details.last_check ? (
            <div className="gateway-check-detail">
              <h3>
                上次模型核对{" "}
                <Badge status={checkStatus[details.last_check.status]}>
                  {labelForCheck(details.last_check)}
                </Badge>
              </h3>
              <p className="integration-caption">
                {when(details.last_check.checked_at)} ·{" "}
                {details.last_check.message}
              </p>
              <p>网关返回 {details.last_check.model_count} 个模型</p>
              <div className="gateway-model-list">
                {details.last_check.model_ids.map((id) => (
                  <code key={id}>{id}</code>
                ))}
              </div>
              {details.last_check.bindings.map((binding) => (
                <div className="gateway-binding-check" key={binding.service_id}>
                  <strong>{serviceName(binding.service_id)}</strong>
                  <div className="integration-caption">
                    {details.last_check?.stale
                      ? "上次检查时的预期模型："
                      : "预期模型："}
                    {binding.expected_model_ids.join("、") || "尚未配置模型 ID"}
                  </div>
                  <div className="integration-caption">
                    {details.last_check?.stale
                      ? "绑定服务配置已变更，请重新检查后确认模型关联。"
                      : binding.status === "matched"
                        ? "网关列表包含预期模型"
                        : binding.status === "unconfigured"
                          ? "服务尚未配置可核对的模型 ID"
                          : `缺失模型：${binding.missing_model_ids.join("、")}`}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="integration-caption">
              尚未检查模型列表。连接保存不会自动修改网关路由。
            </p>
          )}
        </Modal>
      )}
    </>
  );
}
