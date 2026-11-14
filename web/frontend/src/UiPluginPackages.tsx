import { useRef, useState } from "react";
import { api, post } from "./api";
import { Button, Copy, Empty, ErrorBox, Field, InfoBox, Modal, Spinner } from "./ui";
import { RefreshCw, Upload } from "./icons";
import { usePollingQuery } from "./usePollingQuery";
import { useUnsavedChanges } from "./unsavedChanges";
import { UiElement, UiRegion, UiText, withUiSlot, type UiPackageManifest } from "./uiPluginSdk";

type Selection = { packages: Record<string, string>; bindings: Record<string, string> };
type Package = { digest: string; manifest: UiPackageManifest & { name?: string; description?: string }; selected: boolean };
type Directory = { revision: number; items: Package[]; selection: Selection; active_generation: string;
  desired_generation: string; pending_restart: boolean; activation: "next-start" };
type Review = { item: Package | null; revision: number; selection: Selection };
const SHA = /^[a-f0-9]{64}$/;
function parseDirectory(input: unknown): Directory {
  const value = input as Directory;
  if (!value || !Number.isSafeInteger(value.revision) || !Array.isArray(value.items) || value.items.length > 128
    || !value.selection || typeof value.selection.packages !== "object" || !value.selection.bindings
    || typeof value.active_generation !== "string" || typeof value.desired_generation !== "string"
    || typeof value.pending_restart !== "boolean" || value.activation !== "next-start") throw Error("UI 插件目录格式无效");
  const digests = new Set<string>();
  for (const item of value.items) {
    if (!item || !SHA.test(item.digest) || digests.has(item.digest) || !item.manifest || typeof item.manifest.id !== "string"
      || typeof item.manifest.version !== "string" || !Array.isArray(item.manifest.provides)
      || item.manifest.provides.length > 512 || !item.manifest.provides.every(p => p && typeof p.slot === "string" && typeof p.version === "string"))
      throw Error("UI 插件目录存在无效或重复制品");
    digests.add(item.digest);
  }
  return value;
}
function failureText(cause: unknown): string {
  const failure = cause as Error & { outcomeUnknown?: boolean; status?: number };
  return failure.message + (failure.outcomeUnknown ? " 操作结果尚未确认，请先重新读取目录核对，平台不会自动重试。"
    : failure.status === 409 ? " 选择修订已变化。当前输入保留，请关闭窗口并读取新目录后重新核对。" : "");
}
function DefaultUiPluginPackages({ canManage, identity }: { canManage: boolean; identity: string }) {
  const [directory, setDirectory] = useState<Directory | null>(null);
  const [error, setError] = useState(""), [message, setMessage] = useState("");
  const [file, setFile] = useState<File | null>(null), [busy, setBusy] = useState(false), [review, setReview] = useState<Review | null>(null);
  const guard = useRef(false), upload = useRef<HTMLInputElement>(null);
  const query = usePollingQuery(`ui-packages:${identity}`, async signal => parseDirectory(await api("/ui-plugins/packages", { signal }, 2 * 1024 * 1024)), {
    intervalMs: 30000, invalidateOn: ["/ui-plugins/"],
    onSuccess: value => { setDirectory(value); setError(""); }, onError: cause => setError(cause.message),
  });
  async function importPackage() {
    if (!canManage || !file || guard.current) return;
    if (!file.name.endsWith(".mcui") || file.size < 1 || file.size > 8 * 1024 * 1024) { setError("请选择不超过 8 MiB 的 .mcui 离线 UI 插件包。"); return; }
    guard.current = true; setBusy(true); setError("");
    try {
      await api("/ui-plugins/packages/import", { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: await file.arrayBuffer() });
      setFile(null); if (upload.current) upload.current.value = "";
      setMessage("UI 包已校验并保存；未选择的代码不会执行。请选择需要替换的槽位并核对信任声明。"); await query.refresh();
    } catch (cause) { setError(failureText(cause)); }
    finally { guard.current = false; setBusy(false); }
  }
  function openReview(item: Package | null) {
    if (!directory) return;
    setReview({ item, revision: directory.revision, selection: structuredClone(directory.selection) });
  }
  return <UiRegion name="layout.panel" as="section" className="panel plugin-packages" aria-labelledby="ui-packages-title">
    <UiElement as="div" className="panel-head"><UiElement as="div"><UiElement as="h2" id="ui-packages-title"><UiText>界面插件</UiText></UiElement>
      <UiElement as="p" className="small-caption"><UiText>独立 UI 制品可替换外壳、登录、页面、组件、按钮、动作、图标与外观，无需重建控制台。</UiText></UiElement></UiElement>
      <Button actionId="ui.packages.refresh" onClick={() => void query.refresh()} loading={query.refreshing}><RefreshCw size={14} /><UiText>读取目录</UiText></Button></UiElement>
    <UiElement as="div" className="settings-content">
      {error && <ErrorBox message={error} />}{message && <InfoBox>{message}</InfoBox>}
      {directory?.pending_restart && <InfoBox><UiText>已保存的 UI 选择等待下次启动；当前浏览器保留原代次和草稿。控制台重新启动后，保存草稿并刷新页面以加载新的完整装配。</UiText></InfoBox>}
      {canManage && <UiElement as="div" className="plugin-package-toolbar">
        <Field label="离线 UI 包" hint=".mcui，最大 8 MiB；导入仅校验并保存。"><UiElement as="input" ref={upload} type="file" accept=".mcui" disabled={busy} onChange={event => setFile(event.target.files?.[0] ?? null)} /></Field>
        <Button actionId="plugins.ui.import" loading={busy} disabled={!file} onClick={() => void importPackage()}><Upload size={14} /><UiText>检查并导入 UI 包</UiText></Button>
        <Button actionId="ui.packages.restore-defaults" disabled={!directory || busy} onClick={() => openReview(null)}><UiText>恢复默认 UI 装配</UiText></Button>
      </UiElement>}
      {!directory ? !error && <Spinner label="正在读取 UI 插件目录" /> : <>
        <UiElement as="p" className="small-caption"><UiText>当前代次</UiText> <UiElement as="code">{directory.active_generation}</UiElement></UiElement>
        {!directory.items.length ? <Empty title="尚未导入独立 UI 包" description="默认界面同样通过公开 SDK 和显式槽位绑定装配。" /> :
          <UiElement as="div" className="plugin-package-list">{directory.items.map(item => <UiElement as="article" key={item.digest} className="plugin-package-card">
            <UiElement as="div" className="plugin-package-title"><UiElement as="div"><UiElement as="h3">{item.manifest.name ?? item.manifest.id}</UiElement><UiElement as="code">{item.manifest.id} · {item.manifest.version}</UiElement></UiElement>
              {item.selected && <UiElement as="span" className="badge badge-info"><UiElement as="i" /><UiText>下次启动选择</UiText></UiElement>}</UiElement>
            {item.manifest.description && <UiElement as="p">{item.manifest.description}</UiElement>}
            <UiElement as="p"><UiText>提供槽位</UiText>：{item.manifest.provides.map(value => value.slot).join("、")}</UiElement>
            <UiElement as="p" className="plugin-digest"><UiElement as="code">{item.digest}</UiElement><Copy value={item.digest} /></UiElement>
            {canManage && <Button actionId="ui.packages.review" disabled={busy} onClick={() => openReview(item)}><UiText>核对版本与槽位选择</UiText></Button>}
          </UiElement>)}</UiElement>}
      </>}
      <UiElement as="p" className="small-caption"><UiText>UI 包在浏览器中与控制台共享权限，属于受信代码；导入和选择由后端管理员权限及 CSRF 检查保护。未选择制品不会加载，选择失败不会静默退回默认实现。</UiText></UiElement>
    </UiElement>
    {review && canManage && <UiPluginReview key={review.item?.digest ?? "defaults"} review={review} onClose={() => setReview(null)} onSaved={async () => {
      setReview(null); setMessage("UI 装配选择已保存，等待下次控制台启动。当前页面未热切换，也未重启模型。"); await query.refresh();
    }} />}
  </UiRegion>;
}
function DefaultUiPluginReview({ review, onClose, onSaved }: { review: Review; onClose: () => void; onSaved: () => Promise<void> }) {
  const { item } = review;
  const [slots, setSlots] = useState(() => new Set(item?.manifest.provides.map(p => p.slot) ?? []));
  const [trusted, setTrusted] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const guard = useRef(false);
  useUnsavedChanges(trusted || busy);
  async function save() {
    if (guard.current || !trusted) return;
    guard.current = true; setBusy(true); setError("");
    try {
      const selection: Selection = item ? structuredClone(review.selection) : { packages: {}, bindings: {} };
      if (item) {
        selection.packages[item.manifest.id] = item.digest;
        for (const [slot, id] of Object.entries(selection.bindings)) if (id === item.manifest.id) delete selection.bindings[slot];
        for (const slot of slots) selection.bindings[slot] = item.manifest.id;
        for (const id of Object.keys(selection.packages)) if (!Object.values(selection.bindings).includes(id)) delete selection.packages[id];
      }
      await post("/ui-plugins/selection", { expected_revision: review.revision, trust_code: true, ...selection });
      await onSaved();
    } catch (cause) { setError(failureText(cause)); }
    finally { guard.current = false; setBusy(false); }
  }
  return <Modal title={item ? "选择 UI 提供者" : "恢复默认 UI 装配"} subtitle={item ? `${item.manifest.id} · ${item.manifest.version}` : "清除第三方 UI 槽位选择，下次启动采用默认参考包。"}
    onClose={onClose} closeDisabled={busy} wide footer={<><Button actionId="ui.selection.cancel" disabled={busy} onClick={onClose}><UiText>取消</UiText></Button>
      <Button actionId="plugins.ui.select" kind="primary" loading={busy} disabled={!trusted} onClick={() => void save()}><UiText>保存下次启动选择</UiText></Button></>}>
    {item && <><UiElement as="p" className="plugin-digest"><UiElement as="code">{item.digest}</UiElement></UiElement><Field label="替换槽位" hint="可选取同一包中的多个槽位；未选槽位保留其他已选择提供者。">
      <UiElement as="div" className="ui-slot-choice-list">{item.manifest.provides.map(contribution => <UiElement as="label" key={contribution.slot} className="plugin-trust-check">
        <UiElement as="input" type="checkbox" checked={slots.has(contribution.slot)} disabled={busy} onChange={event => setSlots(previous => {
          const next = new Set(previous); if (event.target.checked) next.add(contribution.slot); else next.delete(contribution.slot); return next;
        })} /><UiElement as="code">{contribution.slot}</UiElement><UiElement as="span">{contribution.version}</UiElement></UiElement>)}</UiElement></Field></>}
    <UiElement as="label" className="plugin-trust-check"><UiElement as="input" type="checkbox" checked={trusted} disabled={busy} onChange={event => setTrusted(event.target.checked)} />
      <UiElement as="span"><UiText>我已审查来源、许可、版本和摘要，并信任所选 UI 代码。它可访问当前浏览器会话及同源 API，不提供代码沙箱。</UiText></UiElement></UiElement>
    <InfoBox><UiText>保存前会整体校验依赖、版本、重复提供者和必需槽位。选择仅在下次控制台启动生效，导入、保存或刷新页面均不会重启模型。</UiText></InfoBox>
    {error && <ErrorBox message={error} />}
  </Modal>;
}
export const UiPluginPackages = withUiSlot("component.UiPluginPackages", DefaultUiPluginPackages);
const UiPluginReview = withUiSlot("component.UiPluginReview", DefaultUiPluginReview);
export default UiPluginPackages;

// Reference package implementation exports.
export { DefaultUiPluginPackages, DefaultUiPluginReview };
