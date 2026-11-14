import { useMemo, useState } from "react";
import type { PluginCatalog } from "./consolePlugins";
import { Badge, Button, Copy, ErrorBox, SearchInput, Select, Spinner } from "./ui";
import { RefreshCw } from "./icons";
import "./plugin-settings.css";

const kinds: Record<string, string> = {
  page: "业务页面", feature: "业务模块", runtime: "运行时", storage: "数据库驱动",
  integration: "外部集成", observability: "观测", core: "宿主组件",
  appearance: "品牌与外观",
  community: "社区扩展",
};

export default function PluginSettings({ catalog, error, refreshing, onRefresh }: {
  catalog: PluginCatalog | null; error: string; refreshing: boolean; onRefresh: () => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const items = useMemo(() => (catalog?.items ?? []).filter((item) =>
    (status === "all" || (status === "enabled") === item.enabled)
    && `${item.name} ${item.id} ${item.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [catalog, query, status]);
  return <section className="panel plugin-settings" aria-labelledby="plugin-settings-title">
    <div className="panel-head"><div><h2 id="plugin-settings-title">插件管理</h2><p className="small-caption">当前实例安装的业务模块、运行时与数据库驱动</p></div>
      <Button loading={refreshing} onClick={onRefresh}><RefreshCw size={14} />读取清单</Button></div>
    <div className="settings-content">
      <p className="settings-context-note">启用状态由启动配置 <code>MOTOR_DISABLED_PLUGINS</code> 管理，修改后重启控制台生效。此页读取实际加载结果；不会停止模型、安装插件或修改部署配置。</p>
      {error && <ErrorBox message={error} />}
      {!catalog && !error && <Spinner label="正在读取插件清单" />}
      {catalog && <>
        <div className="plugin-toolbar"><SearchInput value={query} onChange={setQuery} placeholder="搜索插件名称或标识" ariaLabel="搜索插件" />
          <Select value={status} onChange={setStatus} ariaLabel="插件状态"><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">未启用</option></Select>
          <span className="small-caption">{catalog.items.filter((item) => item.enabled).length} 个已启用 · {catalog.items.length} 个已安装</span></div>
        <div className="table-scroll plugin-table"><table><thead><tr><th scope="col">插件</th><th scope="col">类型 / 版本</th><th scope="col">加载状态</th><th scope="col">依赖</th></tr></thead>
          <tbody>{items.map((item) => <tr key={item.id}><td><strong>{item.name}</strong><code>{item.id}</code><p>{item.description}</p></td>
            <td data-label="类型 / 版本">{kinds[item.kind] ?? item.kind}<span className="small-caption">{item.version} · API {item.api_version}</span></td>
              <td data-label="加载状态"><Badge status={item.enabled ? "pass" : "stopped"}>{!item.enabled ? "未启用" : item.kind === "storage"
                ? catalog.active_storage_plugin === item.id ? "当前驱动" : "可用驱动" : "已启用"}</Badge>{item.required && <span className="small-caption">当前部署必需</span>}</td>
            <td data-label="依赖">{item.dependencies.length ? item.dependencies.map((id) => <code key={id}>{catalog.items.find((entry) => entry.id === id)?.name ?? id}</code>) : <span className="small-caption">无插件依赖</span>}</td></tr>)}</tbody></table>
          {!items.length && <p className="plugin-empty">没有匹配的插件，请调整搜索或筛选条件。</p>}</div>
        <details className="plugin-contract"><summary>兼容合同与清单标识</summary>
          <dl className="description-list"><dt>清单协议</dt><dd>版本 {catalog.schemaVersion}</dd><dt>清单 SHA-256</dt><dd><code>{catalog.digest}</code><Copy value={catalog.digest} /></dd></dl>
          <p className="settings-context-note">权限、审计与资源准入仍由宿主校验。“已启用”表示模块在本实例加载，不代表外部系统连接通过；数据库仅使用标明“当前驱动”的实现。社区包需单独导入、审查并保存下次启动选择，本页不执行热加载。</p>
        </details>
      </>}
    </div>
  </section>;
}
