// Self-contained ESM: every React/API/runtime dependency comes from the public SDK.
const sdk = globalThis.MotorConsoleUiSdk;
if (!sdk || sdk.sdkVersion !== "1.0.0") throw Error("UI SDK 1.0.0 is required");
const R = sdk.React, h = R.createElement, Button = sdk.UiButton;
const copy = value => JSON.stringify(value, null, 2);

function Services() {
  const host = sdk.useHost(), t = sdk.useTranslation();
  const canOperate = host.meta?.mode === "demo" || ["admin", "operator"].includes(host.auth?.user?.role);
  return h("section", { className: "panel operations-services" },
    h("div", { className: "panel-head" }, h("h2", null, t("服务工作台")),
      h(Button, { actionId: "services.create", className: "btn btn-primary", disabled: !canOperate,
        onClick: () => host.workflows.createService?.() }, t("创建推理服务"))),
    h("table", null, h("thead", null, h("tr", null, ...["名称", "运行时", "状态", "操作"].map(title => h("th", { key: title }, t(title))))),
      h("tbody", null, ...host.services.map(service => h("tr", { key: service.id },
        h("td", null, service.name), h("td", null, service.runtime), h("td", null, service.status),
        h("td", null, h(Button, { actionId: "services.open", className: "btn", actionContext: { service },
          onClick: () => host.workflows.openService?.(service) }, t("查看")),
          h(Button, { actionId: "services.edit", className: "btn", disabled: !canOperate, actionContext: { service },
            onClick: () => host.workflows.editService?.(service) }, t("编辑"))))))),
    !host.services.length && h("p", { className: "settings-content" }, t("尚无已保存的服务配置。")));
}

/** Independent controlled workflow: does not import or render the default editor. */
function Editor({ original, initialTemplate, onSaved, onDeploy, onClose }) {
  const t = sdk.useTranslation();
  const seed = original ?? initialTemplate ?? {};
  const [raw, setRaw] = R.useState(() => copy({ name: original?.name ?? "", runtime: seed.runtime ?? "docker",
    motor_profile: seed.motor_profile ?? "", user_config: seed.user_config ?? {}, env: seed.env ?? {},
    placements: original?.placements ?? [], extra_manifests: seed.extra_manifests ?? [] }));
  const [saved, setSaved] = R.useState(original), [dirty, setDirty] = R.useState(false);
  const [plan, setPlan] = R.useState(null), [approved, setApproved] = R.useState(false);
  const [busy, setBusy] = R.useState(false), [error, setError] = R.useState(""), [unknown, setUnknown] = R.useState(false);
  const pending = R.useRef(false);
  const [portal] = R.useState(() => document.createElement("div"));
  const dialog = R.useRef(null), closeRef = R.useRef(() => {});
  sdk.useUnsavedChanges(dirty || unknown);
  const close = () => { if (!busy && (!dirty && !unknown || window.confirm(t("放弃尚未保存或尚未确认的编辑结果？")))) onClose(); };
  closeRef.current = close;
  R.useEffect(() => {
    const previousFocus = document.activeElement, previousOverflow = document.body.style.overflow;
    document.body.appendChild(portal);
    const siblings = Array.from(document.body.children).filter(element => element !== portal);
    const previousInert = siblings.map(element => element.inert);
    siblings.forEach(element => { element.inert = true; }); document.body.style.overflow = "hidden";
    const frame = requestAnimationFrame(() => dialog.current?.querySelector("textarea")?.focus());
    const keydown = event => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); }
      if (event.key === "Tab") {
        const controls = Array.from(dialog.current?.querySelectorAll("button:not(:disabled),input:not(:disabled),textarea:not(:disabled),[tabindex='0']") ?? []);
        if (!controls.length) { event.preventDefault(); dialog.current?.focus(); return; }
        const index = controls.indexOf(document.activeElement);
        if (event.shiftKey && index <= 0) { event.preventDefault(); controls.at(-1)?.focus(); }
        if (!event.shiftKey && index === controls.length - 1) { event.preventDefault(); controls[0].focus(); }
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      cancelAnimationFrame(frame); document.removeEventListener("keydown", keydown);
      siblings.forEach((element, index) => { element.inert = previousInert[index]; });
      document.body.style.overflow = previousOverflow; portal.remove();
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [portal]);
  async function run(operation) {
    if (pending.current || unknown) return;
    pending.current = true; setBusy(true); setError("");
    try { await operation(); }
    catch (cause) {
      setError(`${cause.message}${cause.operationKey ? ` [${cause.operationKey}]` : ""}`);
      if (cause.outcomeUnknown) setUnknown(true);
    } finally { pending.current = false; setBusy(false); }
  }
  async function save() {
    const body = sdk.client.parseStrictJson(raw);
    if (!body || typeof body !== "object" || Array.isArray(body)) throw Error(t("请输入完整服务配置对象"));
    const result = await sdk.client.api(saved ? `/services/${encodeURIComponent(saved.id)}` : "/services", {
      method: saved ? "PUT" : "POST",
      body: JSON.stringify({ ...body, ...(saved ? { expected_revision: saved.config_revision } : {}) }),
    });
    setSaved(result); setDirty(false); setPlan(null); setApproved(false); onSaved(); return result;
  }
  async function inspect() {
    const service = dirty || !saved ? await save() : saved;
    const result = await sdk.client.post(`/services/${encodeURIComponent(service.id)}/plan`);
    setPlan(result); setApproved(false);
  }
  const extra = Boolean(saved?.extra_manifests?.length);
  const canDeploy = !busy && !unknown && !dirty && saved && plan?.valid && (!extra || approved && plan.extra_manifest_sha256 && plan.configuration_digest && plan.manifests?.length);
  return sdk.createPortal(h("div", { className: "operations-editor-overlay" }, h("section", { ref: dialog, className: "modal modal-wide operations-raw-editor", role: "dialog",
    "aria-modal": "true", "aria-labelledby": "operations-editor-title", tabIndex: -1 },
    h("div", { className: "modal-head" }, h("h2", { id: "operations-editor-title" }, t("原生配置部署工作台"))),
    h("div", { className: "modal-body" },
      h("p", null, t("完整编辑服务名称、运行时、原生 user_config/env、执行 placements 与附加资源；原生对象中的未知字段保留。")),
      h("textarea", { rows: 18, value: raw, spellCheck: false, disabled: busy || unknown, "aria-label": t("完整服务配置 JSON"),
        onChange: event => { setRaw(event.target.value); setDirty(true); setPlan(null); setApproved(false); } }),
      error && h("p", { role: "alert", className: "error-box" }, error),
      unknown && h("p", { role: "alert" }, t("操作结果尚未确认。请保留操作标识并查询已有服务/任务；此编辑器不会自动重试。")),
      plan && h("pre", { "aria-label": t("实际预检结果") }, copy(plan)),
      extra && h("label", null, h("input", { type: "checkbox", checked: approved, disabled: !plan?.valid || busy,
        onChange: event => setApproved(event.target.checked) }), t("我已审阅本次预检返回的附加资源及配置摘要"))),
    h("div", { className: "modal-footer" },
      h(Button, { actionId: "services.editor.close", className: "btn", disabled: busy, onClick: close }, t("关闭")),
      h(Button, { actionId: "services.draft.save", className: "btn", disabled: busy || unknown, onClick: () => run(save) }, t("保存草稿")),
      h(Button, { actionId: "services.plan", className: "btn", disabled: busy || unknown, onClick: () => run(inspect) }, t("保存并预检")),
      h(Button, { actionId: "services.deploy.confirm", className: "btn btn-primary", disabled: !canDeploy,
        actionContext: { service: saved, plan }, onClick: () => run(async () => {
          await onDeploy(saved, extra ? { extra_manifests_approved_sha256: plan.extra_manifest_sha256,
            reviewed_configuration_digest: plan.configuration_digest } : undefined); onClose();
        }) }, t("确认部署"))))), portal);
}

const handlers = new Map();
const actions = {
  invoke(action) { return action.disabled ? false : (handlers.get(action.id) ?? (value => value.execute()))(action); },
  register(id, handler) { if (handlers.has(id)) throw Error(`Duplicate action ${id}`); handlers.set(id, handler); return () => handlers.delete(id); },
};
export default sdk.definePlugin({
  id: "example.operations-ui", version: "1.0.0",
  providers: {
    "page.services": Services,
    "component.ServiceEditor": Editor,
    "ui.button": ({ actionId, ...props }) => h("button", { ...props, "data-ui-action": actionId, "data-design-system": "operations" }),
    "ui.actions": actions,
    "ui.icons": { render(id, { size, ...props }) { return h("svg", { ...props, viewBox: "0 0 24 24", "data-icon-theme": "operations", "data-semantic-icon": id },
      h("rect", { x: 4, y: 4, width: 16, height: 16, rx: 3, fill: "none", stroke: "currentColor", strokeWidth: 1.5 }),
      h("path", { d: "M8 12h8M12 8v8", fill: "none", stroke: "currentColor", strokeWidth: 1.5 })); } },
    "ui.i18n": { locale: "en", translate(_key, source) { return ({ "创建推理服务": "Create service", "关闭": "Close" })[source] ?? source; } },
  },
  async activate(context) {
    const style = document.createElement("style");
    style.textContent = new TextDecoder().decode(await context.loadAsset("operations.css"));
    document.head.appendChild(style); context.onStop(() => style.remove());
  },
});
