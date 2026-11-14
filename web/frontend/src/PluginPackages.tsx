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
export default function PluginPackagesPanel({ identity, canManage, onCatalogRefresh }: { identity: string; canManage: boolean; onCatalogRefresh: () => void }) {
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
  return <section className="panel plugin-packages" aria-labelledby="plugin-packages-title">
    <div className="panel-head"><div><h2 id="plugin-packages-title">社区插件包</h2><p className="small-caption">导入离线包，核对来源后选择下次启动加载的版本。</p></div>
      <div className="plugin-package-toolbar"><Button onClick={() => void query.refresh()} loading={query.refreshing}><RefreshCw size={14} />读取目录</Button>
        {canManage && <Button kind="primary" onClick={() => setImportOpen(true)}><Upload size={14} />导入插件</Button>}</div></div>
    <div className="settings-content">
      {!canManage && <InfoBox>演示环境仅展示包目录。导入或选择服务端插件代码需要在正式控制面使用已登录的管理员账号。</InfoBox>}
      {error && <ErrorBox message={error} />}{message && <InfoBox>{message}</InfoBox>}
      {directory?.pending_restart && <InfoBox>已选择的插件与当前加载结果不同。请先检查下方的停用或兼容原因；允许加载的选择会在重启对应控制台进程后生效。</InfoBox>}
      {!directory ? error ? null : <Spinner label="正在读取插件包目录" /> : !directory.items.length ? <Empty title="尚未导入社区插件" description="支持 .qhplugin 离线包，单包最多 8 MiB。导入仅检查并保存，不执行插件代码。" /> :
        <div className="plugin-package-list">{directory.items.map((item) => <article key={item.digest} className="plugin-package-card">
          <div className="plugin-package-title"><div><h3>{item.name}</h3><code>{item.id} · {item.version}</code></div>
            <div className="plugin-package-badges">{item.loaded && <span className="badge badge-pass"><i />当前已加载</span>}{item.selected && <span className={`badge ${item.blocked_reason ? "badge-warn" : "badge-info"}`}><i />{item.blocked_reason ? "已选择 · 加载受阻" : "下次启动选择"}</span>}{!item.loaded && !item.selected && <span className="badge badge-stopped"><i />已导入</span>}{item.compatible_with_host === false && <span className="badge badge-warn"><i />与当前宿主不兼容</span>}</div></div>
          {item.description && <p>{item.description}</p>}
          {(item.blocked_reason || item.compatible_with_host === false) && <InfoBox>{item.blocked_reason || "此包声明的兼容范围不包含当前宿主版本"}。
            {item.compatible_with_host === false ? "不能选择此版本；保留包文件供版本回退核验，不会自动删除。" : "仅重启不能解除宿主停用，请先检查部署侧插件配置。"}</InfoBox>}
          <dl><dt>作者 / 许可</dt><dd>{item.author} · {item.license}</dd><dt>最低访问角色</dt><dd>{roleLabel(item.access)}</dd><dt>SHA-256</dt><dd><code>{item.digest}</code><Copy value={item.digest} /></dd></dl>
          {canManage && <div className="plugin-package-actions"><Button disabled={item.selected || busy || item.compatible_with_host === false} onClick={() => { setSelection(item); setError(""); }}>{directory.items.some((other) => other.id === item.id && other.selected) ? "选择此版本 / 回退" : "选择下次启动"}</Button>
            {item.selected && <Button disabled={busy} onClick={() => setDeselect({ item, revision: directory.revision })}>取消下次启动选择</Button>}</div>
          }
        </article>)}</div>}
    </div>
    {importOpen && canManage && <ImportPluginPackage identity={identity} onClose={() => setImportOpen(false)} onImported={async () => { await query.refresh(); setMessage("插件包已检查并保存。请选择版本并核对信任声明，导入不会自动启用。"); }} />}
    {selection && directory && canManage && <SelectPluginPackage key={selection.digest} item={selection} revision={directory.revision} identity={identity}
      onClose={() => setSelection(null)} onSelected={async () => { setSelection(null); setMessage("已保存下次启动选择；当前插件清单未热切换。"); await query.refresh(); onCatalogRefresh(); }} />}
    {deselect && canManage && <Modal title="取消下次启动选择" subtitle={`${deselect.item.name} · ${deselect.item.version}`} onClose={() => { if (!busy) setDeselect(null); }} closeDisabled={busy}
      footer={<><Button disabled={busy} onClick={() => setDeselect(null)}>保留选择</Button><Button loading={busy} onClick={() => void removeChoice(deselect.item, deselect.revision)}>确认取消选择</Button></>}>
      <p>取消后不会删除插件包或业务数据。当前已经加载的版本会继续运行，直到控制台进程重启。</p>{error && <ErrorBox message={error} />}
    </Modal>}
  </section>;
}

function ImportPluginPackage({ identity, onClose, onImported }: { identity: string; onClose: () => void; onImported: () => Promise<void> }) {
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
    footer={<><Button disabled={busy} onClick={onClose}>取消</Button><Button kind="primary" loading={busy} disabled={!file} onClick={() => void submit()}><Upload size={14} />检查并导入</Button></>}>
    <Field label="插件包" required hint="仅支持 .qhplugin；最大 8 MiB。导入不会安装依赖、联网下载或执行插件。"><input type="file" accept=".qhplugin" disabled={busy} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setError(""); }} /></Field>
    {file && <p className="plugin-file-summary">{file.name} · {pluginPackageSize(file.size)}</p>}
    <InfoBox>插件包包含服务端代码。导入后仍需核对作者、许可和摘要，再单独选择下次启动加载。</InfoBox>{error && <ErrorBox message={error} />}
  </Modal>;
}

function SelectPluginPackage({ item, revision, identity, onClose, onSelected }: { item: PluginPackage; revision: number; identity: string; onClose: () => void; onSelected: () => Promise<void> }) {
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
    footer={<><Button disabled={busy} onClick={onClose}>取消</Button><Button kind="primary" loading={busy} disabled={!trusted} onClick={() => void submit()}>保存下次启动选择</Button></>}>
    <dl className="description-list"><dt>插件标识</dt><dd>{item.id}</dd><dt>作者 / 许可</dt><dd>{item.author} · {item.license}</dd><dt>代码包摘要</dt><dd className="plugin-digest"><code>{item.digest}</code><Copy value={item.digest} /></dd></dl>
    <Field label="插件配置 JSON" hint="紧凑 JSON 的 UTF-8 编码最多 32 KiB（32,768 字节）。配置会持久保存；密码、令牌或私钥请按插件文档使用部署侧秘密引用。"><textarea rows={7} value={configuration} maxLength={MAX_PLUGIN_CONFIGURATION_INPUT_CHARACTERS} disabled={busy} onChange={(event) => setConfiguration(event.target.value)} spellCheck={false} /></Field>
    <label className="plugin-trust-check"><input type="checkbox" checked={trusted} disabled={busy} onChange={(event) => setTrusted(event.target.checked)} /><span>我已审查此版本来源、许可与 SHA-256，并信任它在控制台进程中执行。插件与宿主拥有同等进程权限，不提供代码沙箱。</span></label>
    <p className="settings-context-note">保存只更新下次启动配置。请按部署流程重启控制台并检查实际加载结果；页面不会重启平台或模型。</p>{error && <ErrorBox message={error} />}
  </Modal>;
}
