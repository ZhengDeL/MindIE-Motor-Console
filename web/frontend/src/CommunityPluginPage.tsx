import { useState } from "react";
import { api } from "./api";
import { Button, Empty, ErrorBox, Spinner } from "./ui";
import { RefreshCw } from "./icons";
import Pagination from "./Pagination";
import { usePollingQuery } from "./usePollingQuery";
import { useRefreshPreference } from "./refreshPreferences";
import { parsePluginTable, type PluginNavigation, type PluginTable } from "./communityPlugins";
import "./plugin-packages.css";

export default function CommunityPluginPage({ contribution, identity }: { contribution: PluginNavigation; identity: string }) {
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
    <div className="page-heading community-page-heading"><div><h1>{contribution.title}</h1><p className="small-caption">{contribution.description}</p></div></div>
    <section className="panel plugin-page-panel" aria-labelledby="plugin-page-data-title">
      <div className="panel-head"><div><h2 id="plugin-page-data-title">{data?.title || contribution.title}</h2><p className="small-caption">由 {contribution.plugin_id} 提供</p></div>
        <Button onClick={() => void query.refresh()} loading={query.refreshing}><RefreshCw size={14} />刷新</Button></div>
      {error && <div className="settings-content"><ErrorBox message={error} /><Empty title="暂时无法读取插件数据" description="未取得的数据不会作为空列表或零值展示。可重试读取，或在系统设置中检查插件实际加载结果。" /></div>}
      {!data && !error && <Spinner label="正在读取插件数据" />}
      {data && <>
        {data.summary && <p className="settings-content">{data.summary}</p>}
        {!data.rows.length ? <Empty title="暂无记录" description="插件接口已返回有效空列表。" /> : <div className="table-scroll plugin-page-table"><table><thead><tr>{data.columns.map((column) => <th scope="col" key={column.key}>{column.label}</th>)}</tr></thead>
          <tbody>{data.rows.slice((current - 1) * 20, current * 20).map((row, index) => <tr key={(current - 1) * 20 + index}>{data.columns.map((column) => <td key={column.key}>{row[column.key] === null ? "—" : typeof row[column.key] === "boolean" ? row[column.key] ? "是" : "否" : String(row[column.key])}</td>)}</tr>)}</tbody></table></div>}
        <div className="plugin-page-caption"><span>{data.rows.length} 条记录</span>{data.updated_at && <span>插件标记时间：{data.updated_at}</span>}</div>
        {data.rows.length > 20 && <Pagination page={current} totalPages={Math.ceil(data.rows.length / 20)} onChange={setPage} ariaLabel="插件表格分页" />}
      </>}
    </section>
  </>;
}
