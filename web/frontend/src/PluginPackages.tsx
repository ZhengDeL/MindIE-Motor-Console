import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { useEffect, useRef, useState } from "react";
import { api, post, roleLabel } from "./api";
import { Button, Copy, Empty, ErrorBox, Field, InfoBox, Modal, Spinner } from "./ui";
import { RefreshCw, Upload } from "./icons";
import { MAX_PLUGIN_CONFIGURATION_INPUT_CHARACTERS, parsePluginConfiguration, parsePluginPackages, pluginPackageSize, type PluginPackage, type PluginPackages } from "./communityPlugins";
import { usePollingQuery } from "./usePollingQuery";
import { useUnsavedChanges } from "./unsavedChanges";
import "./plugin-packages.css";

const MAX_PACKAGE_BYTES = 8 * 1024 * 1024;
function mutationFailure(reason: unknown): string {
  const failure = reason as Error & { outcomeUnknown?: boolean; status?: number };
  return failure.message + (failure.outcomeUnknown ? " 操作结果尚未确认，请先关闭窗口并读取目录核对，不要连续重复提交。" : failure.status === 409
    ? " 输入仍保留在当前窗口；请先复制保存，再关闭窗口读取最新目录后对比提交。" : "");
}
function DefaultPluginPackagesPanel({ identity, canManage, onCatalogRefresh }: { identity: string; canManage: boolean; onCatalogRefresh: () => void }) {
  const [directory, setDirectory] = useState<PluginPackages | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [selection, setSelection] = useState<PluginPackage | null>(null);
  const [deselect, setDeselect] = useState<{ item: PluginPackage; revision: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, [identity]);
  const query = usePollingQuery(`plugin-packages:${identity}`, async (signal) => parsePluginPackages(
    await api<unknown>("/plugin-packages", { signal }, 2 * 1024 * 1024)), {
    intervalMs: 30_000, invalidateOn: ["/plugin-packages"],
    onSuccess: (value) => { setDirectory(value); setError(""); },
    onError: (failure) => { setError((failure as Error & { status?: number }).status === 404 ? "当前后端未提供社区插件包接口，请使用配套的插件宿主版本。" : failure.message); },
  });
  async function removeChoice(item: PluginPackage, expectedRevision: number) {
    if (guard.current || !directory || !canManage) return;
    guard.current = true; setBusy(true); setError("");
    try {
      await post("/plugin-packages/deselect", { id: item.id, expected_revision: expectedRevision });
      if (!active.current) return;
      setDeselect(null); setMessage("已保存取消选择。当前进程仍按原清单运行，重启控制台后生效。");
      await query.refresh(); onCatalogRefresh();
    } catch (reason) { if (active.current) setError(mutationFailure(reason)); }
    finally { guard.current = false; if (active.current) setBusy(false); }
  }
  return <UiRegion name="layout.panel" as="section" className="panel plugin-packages" aria-labelledby="plugin-packages-title">
    <UiElement as="div" className="panel-head"><UiElement as="div"><UiElement as="h2" id="plugin-packages-title"><UiText>{"社区插件包"}</UiText></UiElement><UiElement as="p" className="small-caption"><UiText>{"导入离线包，核对来源后选择下次启动加载的版本。"}</UiText></UiElement></UiElement>
      <UiElement as="div" className="plugin-package-toolbar"><Button actionId="plugins.server.refresh" onClick={() => void query.refresh()} loading={query.refreshing}><RefreshCw size={14} /><UiText>{"读取目录"}</UiText></Button>
        {canManage && <Button actionId="plugins.server.import.open" kind="primary" onClick={() => setImportOpen(true)}><Upload size={14} /><UiText>{"导入插件"}</UiText></Button>}</UiElement></UiElement>
    <UiElement as="div" className="settings-content">
      {!canManage && <InfoBox><UiText>{"演示环境仅展示包目录。导入或选择服务端插件代码需要在正式控制面使用已登录的管理员账号。"}</UiText></InfoBox>}
      {error && <ErrorBox message={error} />}{message && <InfoBox>{message}</InfoBox>}
      {directory?.pending_restart && <InfoBox><UiText>{"已选择的插件与当前加载结果不同。请先检查下方的停用或兼容原因；允许加载的选择会在重启对应控制台进程后生效。"}</UiText></InfoBox>}
      {!directory ? error ? null : <Spinner label="正在读取插件包目录" /> : !directory.items.length ? <Empty title="尚未导入社区插件" description="支持 .qhplugin 离线包，单包最多 8 MiB。导入仅检查并保存，不执行插件代码。" /> :
        <UiElement as="div" className="plugin-package-list">{directory.items.map((item) => <UiElement as="article" key={item.digest} className="plugin-package-card">
          <UiElement as="div" className="plugin-package-title"><UiElement as="div"><UiElement as="h3">{item.name}</UiElement><UiElement as="code">{item.id} <UiText>{"·"}</UiText> {item.version}</UiElement></UiElement>
            <UiElement as="div" className="plugin-package-badges">{item.loaded && <UiElement as="span" className="badge badge-pass"><UiElement as="i" /><UiText>{"当前已加载"}</UiText></UiElement>}{item.selected && <UiElement as="span" className={`badge ${item.blocked_reason ? "badge-warn" : "badge-info"}`}><UiElement as="i" />{item.blocked_reason ? "已选择 · 加载受阻" : "下次启动选择"}</UiElement>}{!item.loaded && !item.selected && <UiElement as="span" className="badge badge-stopped"><UiElement as="i" /><UiText>{"已导入"}</UiText></UiElement>}{item.compatible_with_host === false && <UiElement as="span" className="badge badge-warn"><UiElement as="i" /><UiText>{"与当前宿主不兼容"}</UiText></UiElement>}</UiElement></UiElement>
          {item.description && <UiElement as="p">{item.description}</UiElement>}
          {(item.blocked_reason || item.compatible_with_host === false) && <InfoBox>{item.blocked_reason || "此包声明的兼容范围不包含当前宿主版本"}<UiText>{"。"}</UiText>
            {item.compatible_with_host === false ? "不能选择此版本；保留包文件供版本回退核验，不会自动删除。" : "仅重启不能解除宿主停用，请先检查部署侧插件配置。"}</InfoBox>}
          <UiElement as="dl"><UiElement as="dt"><UiText>{"作者 / 许可"}</UiText></UiElement><UiElement as="dd">{item.author} <UiText>{"·"}</UiText> {item.license}</UiElement><UiElement as="dt"><UiText>{"最低访问角色"}</UiText></UiElement><UiElement as="dd">{roleLabel(item.access)}</UiElement><UiElement as="dt"><UiText>{"SHA-256"}</UiText></UiElement><UiElement as="dd"><UiElement as="code">{item.digest}</UiElement><Copy value={item.digest} /></UiElement></UiElement>
          {canManage && <UiElement as="div" className="plugin-package-actions"><Button actionId="plugins.server.select.review" disabled={item.selected || busy || item.compatible_with_host === false} onClick={() => { setSelection(item); setError(""); }}>{directory.items.some((other) => other.id === item.id && other.selected) ? "选择此版本 / 回退" : "选择下次启动"}</Button>
            {item.selected && <Button actionId="plugins.server.deselect.review" disabled={busy} onClick={() => setDeselect({ item, revision: directory.revision })}><UiText>{"取消下次启动选择"}</UiText></Button>}</UiElement>
          }
        </UiElement>)}</UiElement>}
    </UiElement>
    {importOpen && canManage && <ImportPluginPackage identity={identity} onClose={() => setImportOpen(false)} onImported={async () => { await query.refresh(); setMessage("插件包已检查并保存。请选择版本并核对信任声明，导入不会自动启用。"); }} />}
    {selection && directory && canManage && <SelectPluginPackage key={selection.digest} item={selection} revision={directory.revision} identity={identity}
      onClose={() => setSelection(null)} onSelected={async () => { setSelection(null); setMessage("已保存下次启动选择；当前插件清单未热切换。"); await query.refresh(); onCatalogRefresh(); }} />}
    {deselect && canManage && <Modal title="取消下次启动选择" subtitle={`${deselect.item.name} · ${deselect.item.version}`} onClose={() => { if (!busy) setDeselect(null); }} closeDisabled={busy}
      footer={<><Button actionId="plugins.server.deselect.cancel" disabled={busy} onClick={() => setDeselect(null)}><UiText>{"保留选择"}</UiText></Button><Button actionId="plugins.server.deselect" loading={busy} onClick={() => void removeChoice(deselect.item, deselect.revision)}><UiText>{"确认取消选择"}</UiText></Button></>}>
      <UiElement as="p"><UiText>{"取消后不会删除插件包或业务数据。当前已经加载的版本会继续运行，直到控制台进程重启。"}</UiText></UiElement>{error && <ErrorBox message={error} />}
    </Modal>}
  </UiRegion>;
}

function DefaultImportPluginPackage({ identity, onClose, onImported }: { identity: string; onClose: () => void; onImported: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const guard = useRef(false), alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, [identity]);
  useUnsavedChanges(!!file || busy);
  async function submit() {
    if (guard.current || !file) return;
    if (!file.name.toLowerCase().endsWith(".qhplugin") || !file.size || file.size > MAX_PACKAGE_BYTES) { setError("请选择有效的 .qhplugin 文件，大小须为 1 字节至 8 MiB。"); return; }
    guard.current = true; setBusy(true); setError("");
    try {
      const bytes = await file.arrayBuffer();
      if (!alive.current) return;
      await api("/plugin-packages/import", { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: bytes }, 512 * 1024);
      if (!alive.current) return;
      await onImported(); if (alive.current) onClose();
    } catch (reason) {
      if (alive.current) setError(mutationFailure(reason));
    } finally { guard.current = false; if (alive.current) setBusy(false); }
  }
  return <Modal title="导入社区插件" subtitle="保存离线包并检查内容完整性、清单与兼容范围。" onClose={onClose} closeDisabled={busy}
    footer={<><Button actionId="plugins.server.import.cancel" disabled={busy} onClick={onClose}><UiText>{"取消"}</UiText></Button><Button actionId="plugins.server.import" kind="primary" loading={busy} disabled={!file} onClick={() => void submit()}><Upload size={14} /><UiText>{"检查并导入"}</UiText></Button></>}>
    <Field label="插件包" required hint="仅支持 .qhplugin；最大 8 MiB。导入不会安装依赖、联网下载或执行插件。"><UiElement as="input" type="file" accept=".qhplugin" disabled={busy} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setError(""); }} /></Field>
    {file && <UiElement as="p" className="plugin-file-summary">{file.name} <UiText>{"·"}</UiText> {pluginPackageSize(file.size)}</UiElement>}
    <InfoBox><UiText>{"插件包包含服务端代码。导入后仍需核对作者、许可和摘要，再单独选择下次启动加载。"}</UiText></InfoBox>{error && <ErrorBox message={error} />}
  </Modal>;
}

function DefaultSelectPluginPackage({ item, revision, identity, onClose, onSelected }: { item: PluginPackage; revision: number; identity: string; onClose: () => void; onSelected: () => Promise<void> }) {
  const [trusted, setTrusted] = useState(false), [configuration, setConfiguration] = useState("{}"), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const guard = useRef(false), alive = useRef(true);
  const expectedRevision = useRef(revision).current;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, [identity]);
  useUnsavedChanges(trusted || configuration !== "{}" || busy);
  async function submit() {
    if (!trusted || guard.current) return;
    let value: unknown;
    try { value = parsePluginConfiguration(configuration); }
    catch (reason) { setError((reason as Error).message); return; }
    guard.current = true; setBusy(true); setError("");
    try {
      await post("/plugin-packages/select", { id: item.id, digest: item.digest, expected_revision: expectedRevision, trust_code: true, configuration: value });
      if (alive.current) await onSelected();
    } catch (reason) { if (alive.current) setError(mutationFailure(reason)); }
    finally { guard.current = false; if (alive.current) setBusy(false); }
  }
  return <Modal title="选择下次启动版本" subtitle={`${item.name} · ${item.version}`} onClose={onClose} closeDisabled={busy}
    footer={<><Button actionId="plugins.server.select.cancel" disabled={busy} onClick={onClose}><UiText>{"取消"}</UiText></Button><Button actionId="plugins.server.select" kind="primary" loading={busy} disabled={!trusted} onClick={() => void submit()}><UiText>{"保存下次启动选择"}</UiText></Button></>}>
    <UiElement as="dl" className="description-list"><UiElement as="dt"><UiText>{"插件标识"}</UiText></UiElement><UiElement as="dd">{item.id}</UiElement><UiElement as="dt"><UiText>{"作者 / 许可"}</UiText></UiElement><UiElement as="dd">{item.author} <UiText>{"·"}</UiText> {item.license}</UiElement><UiElement as="dt"><UiText>{"代码包摘要"}</UiText></UiElement><UiElement as="dd" className="plugin-digest"><UiElement as="code">{item.digest}</UiElement><Copy value={item.digest} /></UiElement></UiElement>
    <Field label="插件配置 JSON" hint="紧凑 JSON 的 UTF-8 编码最多 32 KiB（32,768 字节）。配置会持久保存；密码、令牌或私钥请按插件文档使用部署侧秘密引用。"><UiElement as="textarea" rows={7} value={configuration} maxLength={MAX_PLUGIN_CONFIGURATION_INPUT_CHARACTERS} disabled={busy} onChange={(event) => setConfiguration(event.target.value)} spellCheck={false} /></Field>
    <UiElement as="label" className="plugin-trust-check"><UiElement as="input" type="checkbox" checked={trusted} disabled={busy} onChange={(event) => setTrusted(event.target.checked)} /><UiElement as="span"><UiText>{"我已审查此版本来源、许可与 SHA-256，并信任它在控制台进程中执行。插件与宿主拥有同等进程权限，不提供代码沙箱。"}</UiText></UiElement></UiElement>
    <UiElement as="p" className="settings-context-note"><UiText>{"保存只更新下次启动配置。请按部署流程重启控制台并检查实际加载结果；页面不会重启平台或模型。"}</UiText></UiElement>{error && <ErrorBox message={error} />}
  </Modal>;
}

const PluginPackagesPanel = withUiSlot("component.PluginPackagesPanel", DefaultPluginPackagesPanel);
export default PluginPackagesPanel;
const ImportPluginPackage = withUiSlot("component.ImportPluginPackage", DefaultImportPluginPackage);
const SelectPluginPackage = withUiSlot("component.SelectPluginPackage", DefaultSelectPluginPackage);

// Reference package implementation exports.
export { DefaultPluginPackagesPanel, DefaultImportPluginPackage, DefaultSelectPluginPackage };
