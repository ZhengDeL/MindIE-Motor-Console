import { getUiSdk, definePlugin, defineSlot, provideSlot } from "@mindie-motor/ui-sdk";
import type { Json, Service, UiChangeEvent, UiPageComponent, UiPageProps } from "@mindie-motor/ui-sdk";

const sdk = getUiSdk(), R = sdk.React, h = sdk.createElement;
type Formatter = { format(service: Service): string };
const formatterSlot = defineSlot<Formatter>("example.drafts.formatter", "2.3.0");
const pageSlot = defineSlot<UiPageComponent>("example.drafts.page");
const formatter: Formatter = { format: service => `${service.name} · ${service.status}` };
const seed = { motor_deploy_config: { deploy_mode: "single_container" },
  motor_engine_union_config: { engine_type: "vllm", engine_config: { model: "example/model" } } };
function object(value: unknown): Json {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw Error("配置必须是 JSON 对象");
  return value as Json;
}
function DraftPage({ page }: UiPageProps) {
  const host = sdk.useHost(), format = sdk.useSlot(formatterSlot);
  const [services, setServices] = R.useState<Service[]>([]), [name, setName] = R.useState("");
  const [config, setConfig] = R.useState(JSON.stringify(seed, null, 2));
  const [dirty, setDirty] = R.useState(false), [busy, setBusy] = R.useState(false);
  const [message, setMessage] = R.useState(""), [error, setError] = R.useState("");
  const [unknown, setUnknown] = R.useState(false), inFlight = R.useRef(false);
  sdk.useUnsavedChanges(dirty || busy || unknown);
  const load = async (signal?: AbortSignal) => {
    const result = await sdk.client.api<{ items: Service[] }>("/services", { signal });
    setServices(result.items);
  };
  R.useEffect(() => {
    const abort = new AbortController();
    void load(abort.signal).catch(cause => { if (!abort.signal.aborted) setError(String(cause)); });
    return () => abort.abort();
  }, []);
  const canSave = host.meta?.mode === "control" && ["admin", "operator"].includes(host.auth?.user?.role ?? "");
  async function save() {
    if (inFlight.current || !canSave || unknown) return;
    inFlight.current = true; setBusy(true); setError(""); setMessage("");
    try {
      const body = { name, runtime: "docker", user_config: object(sdk.client.parseStrictJson(config)),
        env: {}, placements: [], extra_manifests: [] };
      const result = await sdk.client.post<Service>("/services", body);
      setDirty(false); setMessage(`已保存草稿：${result.name}`); await load();
    } catch (cause) {
      const failure = cause as Error & { outcomeUnknown?: boolean };
      setUnknown(failure.outcomeUnknown === true);
      setError(failure.message + (failure.outcomeUnknown ? "；结果尚未确认，请先重新读取已有服务核对。" : ""));
    } finally { inFlight.current = false; setBusy(false); }
  }
  return h("section", { className: "panel", "data-independent-ui-page": page.id },
    h("div", { className: "panel-head" }, h("h2", null, page.title)),
    h("div", { className: "settings-content" },
      h("p", null, "通过公共 SDK 读取已保存服务，并将完整原生配置保存为草稿。"),
      h("label", null, "草稿名称", h("input", { "aria-label": "独立草稿名称", value: name, disabled: busy,
        onChange: (event: UiChangeEvent<HTMLInputElement>) => { setName(event.target.value); setDirty(true); } })),
      h("label", null, "原生配置 JSON", h("textarea", { "aria-label": "独立草稿配置", rows: 12, value: config, disabled: busy,
        onChange: (event: UiChangeEvent<HTMLTextAreaElement>) => { setConfig(event.target.value); setDirty(true); } })),
      h(sdk.UiButton, { className: "btn btn-primary", actionId: "example.drafts.save", disabled: busy || !name.trim() || !canSave || unknown,
        onClick: () => void save() }, busy ? "正在保存" : "保存独立草稿"),
      h(sdk.UiButton, { className: "btn", actionId: "example.drafts.refresh", disabled: busy,
        onClick: () => { void load().catch(cause => setError(String(cause))); } }, "读取已有服务"),
      message && h("p", { role: "status" }, message), error && h("p", { role: "alert", className: "error-box" }, error),
      h("ul", { "aria-label": "独立页面服务列表" }, services.map(service => h("li", { key: service.id }, format.format(service))))));
}
export default definePlugin({ id: "example.drafts", version: "1.0.0",
  providers: { ...provideSlot(pageSlot, DraftPage), ...provideSlot(formatterSlot, formatter) } });
