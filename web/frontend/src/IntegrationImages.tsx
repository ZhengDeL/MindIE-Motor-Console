import { UiElement, withUiSlot, UiText } from "./uiPluginSdk";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { readNotice, type ReadState } from "./readState";
import { api, when } from "./api";
import type { ImageInspection, IntegrationImage, NodeInfo } from "./api";
import { Badge, Button, ErrorBox, Field, InfoBox, Select } from "./ui";
import { runtimeAvailable } from "./ClusterNodes";
import { ChevronDown } from "./icons";
import { documentationUrl } from "./documentation";
import { SESSION_EXPIRED_EVENT } from "./brand";
import "./integration.css";

const evidenceLabel: Record<string, string> = {
  none: "尚无源码身份凭据",
  image_label: "镜像标签声明",
  static_runtime_sources: "受限 CPU 检查中的 Python 源码逐文件比对",
};

function probeErrorLabel(code: string): string {
  const known: Record<string, string> = {
    probe_execution_or_output_invalid:
      "无法启动受限检查，或返回内容不符合检查协议",
    probe_timeout: "受限 CPU 检查超时",
    probe_process_failed: "检查进程退出异常，未获得完整结果",
    probe_cleanup_failed: "临时检查容器清理失败，请检查 Agent 日志与遗留容器",
    image_platform_mismatch: "镜像平台与 Docker 节点不匹配，无法运行检查",
    image_declares_volumes: "镜像声明了隐式卷，受限检查拒绝创建挂载",
  };
  if (Object.hasOwn(known, code)) return `${known[code]}（${code}）`;
  const missing = /^(missing_package|missing_module|missing_cli):(.+)$/.exec(code);
  if (missing) {
    const label: Record<string, string> = {
      missing_package: "未检出 Python 包",
      missing_module: "未检出 Motor 原生模块",
      missing_cli: "未检出命令入口",
    };
    return `${label[missing[1]]}：${missing[2]}（${code}）`;
  }
  return `未识别的检查诊断：${code}，请核对 Agent 日志`;
}

export function immutableImage(image: string, digest: string): string {
  if (!/^sha256:[a-f0-9]{64}$/.test(digest))
    throw Error("镜像目录未提供有效的 manifest SHA256。");
  const repository = image.split("@", 1)[0];
  const separator = repository.lastIndexOf(":");
  return `${separator > repository.lastIndexOf("/") ? repository.slice(0, separator) : repository}@${digest}`;
}

function DefaultImageContractPanel({
  image,
  hardware,
  runtime,
  nodes,
  nodesRead,
  onSelectImage,
}: {
  image: string;
  hardware?: string;
  runtime: "docker" | "kubernetes";
  nodes: NodeInfo[];
  nodesRead: ReadState;
  onSelectImage: (value: string) => void;
}) {
  const [catalog, setCatalog] = useState<IntegrationImage[]>([]);
  const [catalogLoaded, setCatalogLoaded] = useState(false);
  const [docsUrl, setDocsUrl] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [selected, setSelected] = useState("");
  const [reference, setReference] = useState("digest");
  const [nodeId, setNodeId] = useState("");
  const [probe, setProbe] = useState(false);
  const [result, setResult] = useState<ImageInspection | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const active = useRef<AbortController | null>(null);
  const dockerNodes = nodes.filter((node) => runtimeAvailable(node, "docker"));
  const selectedNode = dockerNodes.find((node) => node.id === nodeId);
  const fingerprint = nodes
    .map(
      (node) =>
        `${node.id}:${node.docker_available}:${node.docker_version}:${node.status}`,
    )
    .sort()
    .join("|");
  const preset = catalog.find((item) => item.id === selected);
  const invalidate = useCallback(() => {
    generation.current++;
    active.current?.abort();
    active.current = null;
    setResult(null);
    setError("");
    setBusy(false);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    api<{ items: IntegrationImage[]; documentation_url?: string }>("/integrations/images/catalog", {
      signal: controller.signal,
    })
      .then((data) => {
        if (controller.signal.aborted) return;
        if (!Array.isArray(data.items)) throw Error("镜像目录响应格式不正确。");
        setDocsUrl(documentationUrl(data.documentation_url));
        setCatalog(data.items);
        setCatalogLoaded(true);
        setSelected(data.items[0]?.id ?? "");
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setCatalogError((reason as Error).message);
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    invalidate();
    return () => {
      generation.current++;
      active.current?.abort();
    };
  }, [image, nodeId, probe, runtime, fingerprint, invalidate]);
  useEffect(() => {
    window.addEventListener(SESSION_EXPIRED_EVENT, invalidate);
    return () =>
      window.removeEventListener(SESSION_EXPIRED_EVENT, invalidate);
  }, [invalidate]);
  const applyPreset = () => {
    if (!preset) return;
    try {
      onSelectImage(
        reference === "digest"
          ? immutableImage(preset.image, preset.manifest_digest)
          : preset.image,
      );
      setError("");
    } catch (reason) {
      setError((reason as Error).message);
    }
  };
  const inspect = async () => {
    if (!selectedNode || !image.trim()) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const current = ++generation.current;
    setBusy(true);
    setResult(null);
    setError("");
    try {
      const response = await api<ImageInspection>(
        "/integrations/images/inspect",
        {
          method: "POST",
          signal: controller.signal,
          body: JSON.stringify({
            node_id: nodeId,
            image: image.trim(),
            probe_runtime: probe,
          }),
        },
      );
      if (!controller.signal.aborted && current === generation.current)
        setResult(response);
    } catch (reason) {
      if (!controller.signal.aborted && current === generation.current)
        setError((reason as Error).message);
    } finally {
      if (current === generation.current) setBusy(false);
    }
  };
  const probeLabel = {
    not_requested: "未运行",
    passed: "检查完成",
    failed: "检查失败",
    timeout: "检查超时",
    unavailable: "当前不可运行",
  };
  return (
    <UiElement as="details" className="integration-section">
      <UiElement as="summary">
        <UiElement as="span"><UiText>{"官方镜像与运行环境检查"}</UiText></UiElement>
        <UiElement as="span" className="integration-summary-hint">
          <UiElement as="small"><UiText>{"明确镜像来源，检查节点上的实际内容"}</UiText></UiElement>
          <ChevronDown size={15} />
        </UiElement>
      </UiElement>
      <UiElement as="div" className="integration-section-body">
        {catalogError && (
          <ErrorBox
            message={`镜像目录加载失败：${catalogError}。仍可在“服务镜像”中填写自定义镜像。`}
          />
        )}
        <UiElement as="div" className="integration-control-row">
          <Field label="官方镜像预置">
            <Select
              ariaLabel="官方镜像预置"
              value={selected}
              onChange={setSelected}
              disabled={!catalog.length}
            >
              {!catalog.length && <UiElement as="option" value="">{catalogLoaded ? "暂无可用预置" : catalogError ? "预置目录读取失败" : "正在读取预置目录…"}</UiElement>}
              {catalog.map((item) => (
                <UiElement as="option" key={item.id} value={item.id}>
                  {item.name}
                </UiElement>
              ))}
            </Select>
          </Field>
          <Field label="镜像引用方式">
            <Select
              ariaLabel="镜像引用方式"
              value={reference}
              onChange={setReference}
            >
              <UiElement as="option" value="digest"><UiText>{"固定 manifest SHA256"}</UiText></UiElement>
              <UiElement as="option" value="tag"><UiText>{"版本标签"}</UiText></UiElement>
            </Select>
          </Field>
          <Button actionId="action.IntegrationImages.001" onClick={applyPreset} disabled={!preset}>
            <UiText>{"填入服务镜像"}</UiText>
          </Button>
        </UiElement>
        {preset && (
          <UiElement as="div" className="integration-preset-description">
            <UiElement as="strong">
              <UiText>{"Motor"}</UiText> {preset.motor_release} <UiText>{"· vLLM-Ascend"}</UiText> {preset.vllm_ascend} <UiText>{"·"}</UiText>{" "}
              {preset.hardware}
            </UiElement>
            <UiElement as="div">
              {preset.os} <UiText>{"· Python"}</UiText> {preset.python} <UiText>{"·"}</UiText>{" "}
              {preset.platforms.join(" / ")}
            </UiElement>
            <UiElement as="code" className="integration-code">{preset.image}</UiElement>
            <UiElement as="div">
              <UiText>{"目录验证范围：镜像仓库元数据。填入后仍需检查目标节点、原生版本与实际推理。"}</UiText>{" "}
              {docsUrl && <UiElement as="a" href={docsUrl} target="_blank" rel="noreferrer">
                <UiText>{"使用文档 ↗"}</UiText>
              </UiElement>}
            </UiElement>
            {hardware && hardware !== preset.hardware && (
              <UiElement as="div">
                <UiText>{"当前配置的硬件类型为"}</UiText> {hardware}<UiText>{"，请核对镜像与实际硬件配套。"}</UiText>
              </UiElement>
            )}
          </UiElement>
        )}
        <UiElement as="div" className="integration-control-row">
          <Field
            label="镜像检查节点"
            hint="检查所选节点本地已有镜像，不自动拉取"
          >
            <Select
              ariaLabel="镜像检查节点"
              value={nodeId}
              onChange={setNodeId}
            >
              <UiElement as="option" value="">{nodesRead.loaded ? "请选择 Docker 执行节点" : readNotice(nodesRead)}</UiElement>
              {dockerNodes.map((node) => (
                <UiElement as="option" key={node.id} value={node.id}>
                  {node.name} <UiText>{"·"}</UiText> {node.address}
                </UiElement>
              ))}
            </Select>
          </Field>
          <Button actionId="action.IntegrationImages.002"
            onClick={() => void inspect()}
            loading={busy}
            disabled={!nodesRead.loaded || !selectedNode || !image.trim()}
          >
            <UiText>{"检查服务镜像"}</UiText>
          </Button>
        </UiElement>
        <UiElement as="label" className="integration-checkbox">
          <UiElement as="input"
            type="checkbox"
            checked={probe}
            onChange={(event) => setProbe(event.target.checked)}
          />
          <UiText>{"运行受限 CPU 检查（不加载模型）"}</UiText>
        </UiElement>
        <UiElement as="p" className="integration-caption">
          <UiText>{"勾选后创建临时受限容器，检查 Python 包和原生启动入口；不挂载 NPU、权重或宿主目录，不连接业务网络。"}</UiText>
        </UiElement>
        {!nodesRead.loaded ? <InfoBox>{readNotice(nodesRead)}<UiText>{"节点列表，尚不能判断哪些 Agent 可检查镜像。"}</UiText></InfoBox> : !dockerNodes.length && (
          <InfoBox>
            <UiText>{"当前没有可执行本地镜像检查的 Docker Agent。Kubernetes 镜像由集群容器运行时与仓库准备，本地 Docker 检查不能代替集群节点检查。"}</UiText>
          </InfoBox>
        )}
        {runtime === "kubernetes" && dockerNodes.length > 0 && (
          <UiElement as="p" className="integration-caption">
            <UiText>{"本次为独立 Docker 节点的镜像内容检查，不表示 Kubernetes 节点已拉取该镜像。"}</UiText>
          </UiElement>
        )}
        {error && <ErrorBox message={error} />}
        {result && (
          <UiElement as="div" className="integration-report">
            <UiElement as="div" className="integration-report-head">
              <UiElement as="h4"><UiText>{"镜像检查结果"}</UiText></UiElement>
              <UiElement as="span" className="integration-caption">
                {when(result.collected_at)}
              </UiElement>
            </UiElement>
            <UiElement as="dl" className="integration-facts">
              <UiElement as="dt"><UiText>{"检查镜像"}</UiText></UiElement>
              <UiElement as="dd">
                <UiElement as="code" className="integration-code">{result.image}</UiElement>
              </UiElement>
              <UiElement as="dt"><UiText>{"镜像 ID"}</UiText></UiElement>
              <UiElement as="dd">
                <UiElement as="code" className="integration-code">{result.image_id}</UiElement>
              </UiElement>
              <UiElement as="dt"><UiText>{"系统 / 架构"}</UiText></UiElement>
              <UiElement as="dd">
                {result.os} <UiText>{"/"}</UiText> {result.architecture}
              </UiElement>
              <UiElement as="dt"><UiText>{"Motor Python 源码身份"}</UiText></UiElement>
              <UiElement as="dd">
                {result.identity.status === "verified"
                  ? "Python 源码比对一致"
                  : result.identity.status === "declared"
                    ? "标签声明一致，源码尚未证实"
                    : result.identity.status === "mismatch"
                      ? "与已固定 Python 源码不一致"
                      : "尚未证实"}
                <UiText>{"："}</UiText>{evidenceLabel[result.identity.evidence] ?? result.identity.evidence}
              </UiElement>
              <UiElement as="dt"><UiText>{"CPU 内容检查"}</UiText></UiElement>
              <UiElement as="dd">{probeLabel[result.probe.status]}</UiElement>
              {Object.entries(result.probe.packages).map(([name, version]) => (
                <Fragment key={name}>
                  <UiElement as="dt">{name}</UiElement>
                  <UiElement as="dd">{version || "未检出"}</UiElement>
                </Fragment>
              ))}
            </UiElement>
            {result.probe.errors.length > 0 && (
              <ErrorBox
                message={`CPU 检查诊断：${result.probe.errors.map(probeErrorLabel).join("；")}`}
              />
            )}
            <UiElement as="ul" className="integration-checks">
              {result.checks.map((check, index) => (
                <UiElement as="li" key={`${check.name}-${index}`}>
                  <Badge status={check.status} />
                  <UiElement as="div">
                    <UiElement as="strong">{check.name}</UiElement>
                    <UiElement as="p">{check.detail}</UiElement>
                  </UiElement>
                </UiElement>
              ))}
            </UiElement>
            <InfoBox>
              <UiText>{"检查结果仅覆盖已列出的镜像内容与启动入口，不验证模型加载、NPU 推理或多节点通信。"}</UiText>
            </InfoBox>
          </UiElement>
        )}
      </UiElement>
    </UiElement>
  );
}

const ImageContractPanel = withUiSlot("component.ImageContractPanel", DefaultImageContractPanel);
export default ImageContractPanel;

// Reference package implementation exports.
export { DefaultImageContractPanel };
