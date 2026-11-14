import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
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

function DefaultImagesPage({
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
    <UiRegion name="layout.panel" as="section" className="panel inventory-panel">
      <ImageOperations nodes={nodes} canOperate={canOperate} principal={principal} removal={removal} onRemovalClosed={() => setRemoval(null)} onCompleted={() => void load()} />
      {error && <ErrorBox message={error} />}
      {readErrors.length > 0 && (
        <UiElement as="div" className="error-box" role="alert">
          <AlertCircle size={17} />
          <UiElement as="div" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
            <UiElement as="b">
              {images.length
                ? "部分 Docker 节点镜像读取失败"
                : "Docker 镜像库存读取失败"}
            </UiElement>
            <UiElement as="ul">
              {readErrors.map((message, index) => (
                <UiElement as="li" key={index}>{message}</UiElement>
              ))}
            </UiElement>
            {images.length
              ? "当前仅显示成功读取的库存；请排查失败节点后刷新重试。"
              : "未取得可展示的 Docker 镜像库存，请排查失败节点后刷新重试。"}
          </UiElement>
        </UiElement>
      )}
      {unsupportedNodes.length > 0 && (
        <InfoBox>
          <UiElement as="b"><UiText>{"以下节点不适用 Docker 本地库存"}</UiText></UiElement>
          <UiElement as="ul">
            {unsupportedNodes.map((entry) => (
              <UiElement as="li" key={entry.node_id} style={{ overflowWrap: "anywhere" }}>
                {entry.node_name}<UiText>{"："}</UiText>{entry.reason}
              </UiElement>
            ))}
          </UiElement>
        </InfoBox>
      )}
      <UiElement as="div" className="table-toolbar">
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="搜索镜像名称、ID 或节点"
        />
        <UiElement as="div" style={{ minWidth: 145 }}>
          <Select
            value={node}
            onChange={setNode}
            ariaLabel="筛选 Docker 库存节点"
          >
            <UiElement as="option" value="all"><UiText>{"全部Docker节点"}</UiText></UiElement>
            {Array.from(
              new Map([...nodes.filter((item) => item.docker_available !== false && !!item.docker_version).map((item) => [item.id, item.name] as const), ...images.map((i) => [i.node_id, i.node_name] as const)]).entries(),
            ).map(([id, name]) => (
              <UiElement as="option" key={id} value={id}>
                {name}
              </UiElement>
            ))}
          </Select>
        </UiElement>
        <Button actionId="action.ImagesPage.001" onClick={load} loading={loading}>
          <RefreshCw size={14} />
          <UiText>{"刷新镜像"}</UiText>
        </Button>
        <UiElement as="span" className="toolbar-right-note">
          {!hasInventory
            ? error ? "尚未取得库存" : "正在读取库存"
            : loading ? "正在刷新库存"
            : `${items.length} 个${readErrors.length ? "已读取" : ""}镜像记录`}
        </UiElement>
      </UiElement>
      {loading && !images.length ? (
        <Spinner label="正在读取节点镜像" />
      ) : items.length ? (
        <UiElement as="div" className="table-wrap">
          <UiElement as="table">
            <UiElement as="thead">
              <UiElement as="tr">
                <UiElement as="th"><UiText>{"镜像名称 / 标签"}</UiText></UiElement>
                <UiElement as="th"><UiText>{"镜像 ID"}</UiText></UiElement>
                <UiElement as="th"><UiText>{"所在节点"}</UiText></UiElement>
                <UiElement as="th"><UiText>{"架构"}</UiText></UiElement>
                <UiElement as="th"><UiText>{"大小"}</UiText></UiElement>
                <UiElement as="th"><UiText>{"操作"}</UiText></UiElement>
              </UiElement>
            </UiElement>
            <UiElement as="tbody">
              {items.slice((imagePage - 1) * 20, imagePage * 20).map((i, j) => (
                <UiElement as="tr" key={`${i.node_id}-${i.id}-${j}`}>
                  <UiElement as="td">
                    <UiElement as="div" className="name-cell">
                      <UiElement as="div" className="service-icon blue">
                        <Box size={18} />
                      </UiElement>
                      <UiElement as="div">
                        <UiElement as="b">{i.tags[0] ?? "未标记镜像"}</UiElement>
                        {i.tags.length > 1 && (
                          <UiElement as="span" className="cell-secondary">
                            {i.tags.slice(1).join(" · ")}
                          </UiElement>
                        )}
                      </UiElement>
                    </UiElement>
                  </UiElement>
                  <UiElement as="td" className="mono">
                    {i.id.replace("sha256:", "").slice(0, 12)}
                  </UiElement>
                  <UiElement as="td">{i.node_name}</UiElement>
                  <UiElement as="td">
                    {knownText(i.architecture) && <UiElement as="span" className="tag">{i.architecture}</UiElement>}
                  </UiElement>
                  <UiElement as="td">{size(i.size)}</UiElement>
                  <UiElement as="td">
                    <Copy value={i.tags[0] ?? i.id} label="复制镜像名称" />
                    {canOperate && <Button actionId="action.ImagesPage.002" kind="text" disabled={!/^sha256:[0-9a-f]{64}$/.test(i.id) || isReadOnlyAgent(nodes.find((item) => item.id === i.node_id) ?? { agent_metadata: { read_only: true } })} title="仅删除所选节点的完整镜像ID；使用中或只读Agent会拒绝" onClick={() => setRemoval(i)}><Trash2 size={13} /><UiText>{"删除"}</UiText></Button>}
                  </UiElement>
                </UiElement>
              ))}
            </UiElement>
          </UiElement>
        </UiElement>
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
      <UiElement as="div" className="table-footer">
        <UiElement as="span">{!hasInventory
          ? error ? "尚未取得镜像库存" : "正在读取镜像库存"
          : error ? `上次读取 ${items.length} 个镜像 · 本次刷新失败`
            : readErrors.length ? `已读取 ${items.length} 个镜像 · 部分节点读取失败`
              : loading ? `上次读取 ${items.length} 个镜像 · 正在刷新`
                : `共 ${items.length} 个镜像 · 可用性以目标节点本地库存为准`}</UiElement>
        {!loading && items.length > 0 && <Pagination page={imagePage} totalPages={Math.ceil(items.length / 20)} onChange={setImagePage} ariaLabel="镜像库存分页" />}
      </UiElement>
    </UiRegion>
  );
}

const ImagesPage = withUiSlot("component.ImagesPage", DefaultImagesPage);
export default ImagesPage;

// Reference package implementation exports.
export { DefaultImagesPage };
