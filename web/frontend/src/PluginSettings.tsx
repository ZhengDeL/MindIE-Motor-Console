import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
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

function DefaultPluginSettings({ catalog, error, refreshing, onRefresh }: {
  catalog: PluginCatalog | null; error: string; refreshing: boolean; onRefresh: () => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const items = useMemo(() => (catalog?.items ?? []).filter((item) =>
    (status === "all" || (status === "enabled") === item.enabled)
    && `${item.name} ${item.id} ${item.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())), [catalog, query, status]);
  return <UiRegion name="layout.panel" as="section" className="panel plugin-settings" aria-labelledby="plugin-settings-title">
    <UiElement as="div" className="panel-head"><UiElement as="div"><UiElement as="h2" id="plugin-settings-title"><UiText>{"插件管理"}</UiText></UiElement><UiElement as="p" className="small-caption"><UiText>{"当前实例安装的业务模块、运行时与数据库驱动"}</UiText></UiElement></UiElement>
      <Button actionId="action.PluginSettings.001" loading={refreshing} onClick={onRefresh}><RefreshCw size={14} /><UiText>{"读取清单"}</UiText></Button></UiElement>
    <UiElement as="div" className="settings-content">
      <UiElement as="p" className="settings-context-note"><UiText>{"启用状态由启动配置"}</UiText> <UiElement as="code"><UiText>{"MOTOR_DISABLED_PLUGINS"}</UiText></UiElement> <UiText>{"管理，修改后重启控制台生效。此页读取实际加载结果；不会停止模型、安装插件或修改部署配置。"}</UiText></UiElement>
      {error && <ErrorBox message={error} />}
      {!catalog && !error && <Spinner label="正在读取插件清单" />}
      {catalog && <>
        <UiElement as="div" className="plugin-toolbar"><SearchInput value={query} onChange={setQuery} placeholder="搜索插件名称或标识" ariaLabel="搜索插件" />
          <Select value={status} onChange={setStatus} ariaLabel="插件状态"><UiElement as="option" value="all"><UiText>{"全部状态"}</UiText></UiElement><UiElement as="option" value="enabled"><UiText>{"已启用"}</UiText></UiElement><UiElement as="option" value="disabled"><UiText>{"未启用"}</UiText></UiElement></Select>
          <UiElement as="span" className="small-caption">{catalog.items.filter((item) => item.enabled).length} <UiText>{"个已启用 ·"}</UiText> {catalog.items.length} <UiText>{"个已安装"}</UiText></UiElement></UiElement>
        <UiElement as="div" className="table-scroll plugin-table"><UiElement as="table"><UiElement as="thead"><UiElement as="tr"><UiElement as="th" scope="col"><UiText>{"插件"}</UiText></UiElement><UiElement as="th" scope="col"><UiText>{"类型 / 版本"}</UiText></UiElement><UiElement as="th" scope="col"><UiText>{"加载状态"}</UiText></UiElement><UiElement as="th" scope="col"><UiText>{"依赖"}</UiText></UiElement></UiElement></UiElement>
          <UiElement as="tbody">{items.map((item) => <UiElement as="tr" key={item.id}><UiElement as="td"><UiElement as="strong">{item.name}</UiElement><UiElement as="code">{item.id}</UiElement><UiElement as="p">{item.description}</UiElement></UiElement>
            <UiElement as="td" data-label="类型 / 版本">{kinds[item.kind] ?? item.kind}<UiElement as="span" className="small-caption">{item.version} <UiText>{"· API"}</UiText> {item.api_version}</UiElement></UiElement>
              <UiElement as="td" data-label="加载状态"><Badge status={item.enabled ? "pass" : "stopped"}>{!item.enabled ? "未启用" : item.kind === "storage"
                ? catalog.active_storage_plugin === item.id ? "当前驱动" : "可用驱动" : "已启用"}</Badge>{item.required && <UiElement as="span" className="small-caption"><UiText>{"当前部署必需"}</UiText></UiElement>}</UiElement>
            <UiElement as="td" data-label="依赖">{item.dependencies.length ? item.dependencies.map((id) => <UiElement as="code" key={id}>{catalog.items.find((entry) => entry.id === id)?.name ?? id}</UiElement>) : <UiElement as="span" className="small-caption"><UiText>{"无插件依赖"}</UiText></UiElement>}</UiElement></UiElement>)}</UiElement></UiElement>
          {!items.length && <UiElement as="p" className="plugin-empty"><UiText>{"没有匹配的插件，请调整搜索或筛选条件。"}</UiText></UiElement>}</UiElement>
        <UiElement as="details" className="plugin-contract"><UiElement as="summary"><UiText>{"兼容合同与清单标识"}</UiText></UiElement>
          <UiElement as="dl" className="description-list"><UiElement as="dt"><UiText>{"清单协议"}</UiText></UiElement><UiElement as="dd"><UiText>{"版本"}</UiText> {catalog.schemaVersion}</UiElement><UiElement as="dt"><UiText>{"清单 SHA-256"}</UiText></UiElement><UiElement as="dd"><UiElement as="code">{catalog.digest}</UiElement><Copy value={catalog.digest} /></UiElement></UiElement>
          <UiElement as="p" className="settings-context-note"><UiText>{"权限、审计与资源准入仍由宿主校验。“已启用”表示模块在本实例加载，不代表外部系统连接通过；数据库仅使用标明“当前驱动”的实现。社区包需单独导入、审查并保存下次启动选择，本页不执行热加载。"}</UiText></UiElement>
        </UiElement>
      </>}
    </UiElement>
  </UiRegion>;
}

const PluginSettings = withUiSlot("component.PluginSettings", DefaultPluginSettings);
export default PluginSettings;

// Reference package implementation exports.
export { DefaultPluginSettings };
