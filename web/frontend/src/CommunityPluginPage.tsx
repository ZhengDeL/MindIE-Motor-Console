import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { useState } from "react";
import { api } from "./api";
import { Button, Empty, ErrorBox, Spinner } from "./ui";
import { RefreshCw } from "./icons";
import Pagination from "./Pagination";
import { usePollingQuery } from "./usePollingQuery";
import { useRefreshPreference } from "./refreshPreferences";
import { parsePluginTable, type PluginNavigation, type PluginTable } from "./communityPlugins";
import "./plugin-packages.css";

function DefaultCommunityPluginPage({ contribution, identity }: { contribution: PluginNavigation; identity: string }) {
  const [data, setData] = useState<PluginTable | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [interval] = useRefreshPreference();
  const query = usePollingQuery(`community-page:${identity}:${contribution.plugin_id}:${contribution.id}:${contribution.endpoint}`,
    async (signal) => parsePluginTable(await api<unknown>(contribution.endpoint.slice(4), { signal }, 1024 * 1024)), {
      intervalMs: interval,
      onSuccess: (value) => { setData(value); setError(""); },
      onError: (failure) => { setError(failure.message); setData(null); },
    });
  const current = Math.min(page, Math.max(1, Math.ceil((data?.rows.length ?? 0) / 20)));
  return <>
    <UiElement as="div" className="page-heading community-page-heading"><UiElement as="div"><UiElement as="h1">{contribution.title}</UiElement><UiElement as="p" className="small-caption">{contribution.description}</UiElement></UiElement></UiElement>
    <UiRegion name="layout.panel" as="section" className="panel plugin-page-panel" aria-labelledby="plugin-page-data-title">
      <UiElement as="div" className="panel-head"><UiElement as="div"><UiElement as="h2" id="plugin-page-data-title">{data?.title || contribution.title}</UiElement><UiElement as="p" className="small-caption"><UiText>{"由"}</UiText> {contribution.plugin_id} <UiText>{"提供"}</UiText></UiElement></UiElement>
        <Button actionId="action.CommunityPluginPage.001" onClick={() => void query.refresh()} loading={query.refreshing}><RefreshCw size={14} /><UiText>{"刷新"}</UiText></Button></UiElement>
      {error && <UiElement as="div" className="settings-content"><ErrorBox message={error} /><Empty title="暂时无法读取插件数据" description="未取得的数据不会作为空列表或零值展示。可重试读取，或在系统设置中检查插件实际加载结果。" /></UiElement>}
      {!data && !error && <Spinner label="正在读取插件数据" />}
      {data && <>
        {data.summary && <UiElement as="p" className="settings-content">{data.summary}</UiElement>}
        {!data.rows.length ? <Empty title="暂无记录" description="插件接口已返回有效空列表。" /> : <UiElement as="div" className="table-scroll plugin-page-table"><UiElement as="table"><UiElement as="thead"><UiElement as="tr">{data.columns.map((column) => <UiElement as="th" scope="col" key={column.key}>{column.label}</UiElement>)}</UiElement></UiElement>
          <UiElement as="tbody">{data.rows.slice((current - 1) * 20, current * 20).map((row, index) => <UiElement as="tr" key={(current - 1) * 20 + index}>{data.columns.map((column) => <UiElement as="td" key={column.key}>{row[column.key] === null ? "—" : typeof row[column.key] === "boolean" ? row[column.key] ? "是" : "否" : String(row[column.key])}</UiElement>)}</UiElement>)}</UiElement></UiElement></UiElement>}
        <UiElement as="div" className="plugin-page-caption"><UiElement as="span">{data.rows.length} <UiText>{"条记录"}</UiText></UiElement>{data.updated_at && <UiElement as="span"><UiText>{"插件标记时间："}</UiText>{data.updated_at}</UiElement>}</UiElement>
        {data.rows.length > 20 && <Pagination page={current} totalPages={Math.ceil(data.rows.length / 20)} onChange={setPage} ariaLabel="插件表格分页" />}
      </>}
    </UiRegion>
  </>;
}

const CommunityPluginPage = withUiSlot("component.CommunityPluginPage", DefaultCommunityPluginPage);
export default CommunityPluginPage;

// Reference package implementation exports.
export { DefaultCommunityPluginPage };
