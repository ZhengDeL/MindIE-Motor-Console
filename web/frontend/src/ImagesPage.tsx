import { useEffect, useState } from "react";
import { api, isReadOnlyAgent } from "./api";
import type { NodeInfo } from "./api";
import { AlertCircle, Box, RefreshCw, Trash2 } from "./icons";
import { Button, Copy, Empty, ErrorBox, InfoBox, SearchInput, Select, Spinner } from "./ui";
import Pagination from "./Pagination";
import { knownText } from "./resourcePresentation";
import { usePollingQuery } from "./usePollingQuery";
import ImageOperations from "./ImageOperations";
import type { ImageRemoval } from "./ImageOperations";
import "./image-operations.css";

type ImageInfo = {
  id: string;
  node_id: string;
  node_name: string;
  tags: string[];
  size: number | string;
  architecture: string;
};
const size = (value: number | string) => {
  if (typeof value === "string") return knownText(value) ? value : null;
  if (!Number.isFinite(value) || value < 0) return null;
  return value >= 1073741824 ? `${(value / 1073741824).toFixed(2)} GB` : `${(value / 1048576).toFixed(0)} MB`;
};

export default function ImagesPage({
  search,
  setSearch,
  nodes, canOperate, principal,
}: {
  search: string;
  setSearch: (s: string) => void;
  nodes: NodeInfo[]; canOperate: boolean; principal: string;
}) {
  const [imagePage, setImagePage] = useState(1);
  const [images, setImages] = useState<ImageInfo[]>([]);
  const [removal, setRemoval] = useState<ImageRemoval | null>(null);
  const [error, setError] = useState("");
  const [hasInventory, setHasInventory] = useState(false);
  const [readErrors, setReadErrors] = useState<string[]>([]);
  const [unsupportedNodes, setUnsupportedNodes] = useState<
    { node_id: string; node_name: string; reason: string }[]
  >([]);
  const [node, setNode] = useState("all");
  const { refresh: load, refreshing: loading } = usePollingQuery("image-inventory", (signal) => api<{
    items: ImageInfo[]; errors: string[]; unsupported_nodes: { node_id: string; node_name: string; reason: string }[];
  }>("/images", { signal }), {
    invalidateOn: ["/images/pull", "/images/remove"],
    onSuccess: (result) => {
      if (!result || !Array.isArray(result.items) || result.items.some((item) => !item ||
        typeof item.id !== "string" || typeof item.node_id !== "string" || typeof item.node_name !== "string" ||
        !Array.isArray(item.tags) || item.tags.some((tag) => typeof tag !== "string")) ||
        (result.errors !== undefined && (!Array.isArray(result.errors) || result.errors.some((message) => typeof message !== "string"))) ||
        (result.unsupported_nodes !== undefined && (!Array.isArray(result.unsupported_nodes) || result.unsupported_nodes.some((entry) => !entry ||
          typeof entry.node_id !== "string" || typeof entry.node_name !== "string" || typeof entry.reason !== "string"))))
        throw Error("镜像库存响应格式不完整，已保留上次成功读取的库存，请刷新核对。");
      setImages(result.items); setReadErrors(result.errors ?? []); setUnsupportedNodes(result.unsupported_nodes ?? []); setHasInventory(true); setError("");
    },
    onError: (failure) => setError(failure.message),
  });
  const items = images.filter(
    (i) =>
      (node === "all" || i.node_id === node) &&
      `${i.tags.join(" ")} ${i.id} ${i.node_name}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  useEffect(() => setImagePage(1), [search, node]);
  useEffect(() => setImagePage(current => Math.min(current, Math.max(1, Math.ceil(items.length / 20)))), [items.length]);
  return (
    <section className="panel inventory-panel">
      <ImageOperations nodes={nodes} canOperate={canOperate} principal={principal} removal={removal} onRemovalClosed={() => setRemoval(null)} onCompleted={() => void load()} />
      {error && <ErrorBox message={error} />}
      {readErrors.length > 0 && (
        <div className="error-box" role="alert">
          <AlertCircle size={17} />
          <div style={{ minWidth: 0, overflowWrap: "anywhere" }}>
            <b>
              {images.length
                ? "部分 Docker 节点镜像读取失败"
                : "Docker 镜像库存读取失败"}
            </b>
            <ul>
              {readErrors.map((message, index) => (
                <li key={index}>{message}</li>
              ))}
            </ul>
            {images.length
              ? "当前仅显示成功读取的库存；请排查失败节点后刷新重试。"
              : "未取得可展示的 Docker 镜像库存，请排查失败节点后刷新重试。"}
          </div>
        </div>
      )}
      {unsupportedNodes.length > 0 && (
        <InfoBox>
          <b>以下节点不适用 Docker 本地库存</b>
          <ul>
            {unsupportedNodes.map((entry) => (
              <li key={entry.node_id} style={{ overflowWrap: "anywhere" }}>
                {entry.node_name}：{entry.reason}
              </li>
            ))}
          </ul>
        </InfoBox>
      )}
      <div className="table-toolbar">
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="搜索镜像名称、ID 或节点"
        />
        <div style={{ minWidth: 145 }}>
          <Select
            value={node}
            onChange={setNode}
            ariaLabel="筛选 Docker 库存节点"
          >
            <option value="all">全部Docker节点</option>
            {Array.from(
              new Map([...nodes.filter((item) => item.docker_available !== false && !!item.docker_version).map((item) => [item.id, item.name] as const), ...images.map((i) => [i.node_id, i.node_name] as const)]).entries(),
            ).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </Select>
        </div>
        <Button onClick={load} loading={loading}>
          <RefreshCw size={14} />
          刷新镜像
        </Button>
        <span className="toolbar-right-note">
          {!hasInventory
            ? error ? "尚未取得库存" : "正在读取库存"
            : loading ? "正在刷新库存"
            : `${items.length} 个${readErrors.length ? "已读取" : ""}镜像记录`}
        </span>
      </div>
      {loading && !images.length ? (
        <Spinner label="正在读取节点镜像" />
      ) : items.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>镜像名称 / 标签</th>
                <th>镜像 ID</th>
                <th>所在节点</th>
                <th>架构</th>
                <th>大小</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.slice((imagePage - 1) * 20, imagePage * 20).map((i, j) => (
                <tr key={`${i.node_id}-${i.id}-${j}`}>
                  <td>
                    <div className="name-cell">
                      <div className="service-icon blue">
                        <Box size={18} />
                      </div>
                      <div>
                        <b>{i.tags[0] ?? "未标记镜像"}</b>
                        {i.tags.length > 1 && (
                          <span className="cell-secondary">
                            {i.tags.slice(1).join(" · ")}
                          </span>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="mono">
                    {i.id.replace("sha256:", "").slice(0, 12)}
                  </td>
                  <td>{i.node_name}</td>
                  <td>
                    {knownText(i.architecture) && <span className="tag">{i.architecture}</span>}
                  </td>
                  <td>{size(i.size)}</td>
                  <td>
                    <Copy value={i.tags[0] ?? i.id} label="复制镜像名称" />
                    {canOperate && <Button kind="text" disabled={!/^sha256:[0-9a-f]{64}$/.test(i.id) || isReadOnlyAgent(nodes.find((item) => item.id === i.node_id) ?? { agent_metadata: { read_only: true } })} title="仅删除所选节点的完整镜像ID；使用中或只读Agent会拒绝" onClick={() => setRemoval(i)}><Trash2 size={13} />删除</Button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : hasInventory && !error && !readErrors.length ? (
        <Empty
          title={
            images.length
              ? "没有匹配的镜像"
              : unsupportedNodes.length
                ? "暂无适用的 Docker 镜像库存"
                : "尚无 Docker 镜像记录"
          }
          description={
            images.length
              ? "调整搜索或节点筛选条件后查看。"
              : "本页仅列出成功读取的 Docker 节点本地镜像，不展示 Kubernetes 镜像缓存。"
          }
          icon={<Box size={29} />}
        />
      ) : null}
      <div className="table-footer">
        <span>{!hasInventory
          ? error ? "尚未取得镜像库存" : "正在读取镜像库存"
          : error ? `上次读取 ${items.length} 个镜像 · 本次刷新失败`
            : readErrors.length ? `已读取 ${items.length} 个镜像 · 部分节点读取失败`
              : loading ? `上次读取 ${items.length} 个镜像 · 正在刷新`
                : `共 ${items.length} 个镜像 · 可用性以目标节点本地库存为准`}</span>
        {!loading && items.length > 0 && <Pagination page={imagePage} totalPages={Math.ceil(items.length / 20)} onChange={setImagePage} ariaLabel="镜像库存分页" />}
      </div>
    </section>
  );
}
