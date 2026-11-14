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

export default function ImageContractPanel({
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
    <details className="integration-section">
      <summary>
        <span>官方镜像与运行环境检查</span>
        <span className="integration-summary-hint">
          <small>明确镜像来源，检查节点上的实际内容</small>
          <ChevronDown size={15} />
        </span>
      </summary>
      <div className="integration-section-body">
        {catalogError && (
          <ErrorBox
            message={`镜像目录加载失败：${catalogError}。仍可在“服务镜像”中填写自定义镜像。`}
          />
        )}
        <div className="integration-control-row">
          <Field label="官方镜像预置">
            <Select
              ariaLabel="官方镜像预置"
              value={selected}
              onChange={setSelected}
              disabled={!catalog.length}
            >
              {!catalog.length && <option value="">{catalogLoaded ? "暂无可用预置" : catalogError ? "预置目录读取失败" : "正在读取预置目录…"}</option>}
              {catalog.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="镜像引用方式">
            <Select
              ariaLabel="镜像引用方式"
              value={reference}
              onChange={setReference}
            >
              <option value="digest">固定 manifest SHA256</option>
              <option value="tag">版本标签</option>
            </Select>
          </Field>
          <Button onClick={applyPreset} disabled={!preset}>
            填入服务镜像
          </Button>
        </div>
        {preset && (
          <div className="integration-preset-description">
            <strong>
              Motor {preset.motor_release} · vLLM-Ascend {preset.vllm_ascend} ·{" "}
              {preset.hardware}
            </strong>
            <div>
              {preset.os} · Python {preset.python} ·{" "}
              {preset.platforms.join(" / ")}
            </div>
            <code className="integration-code">{preset.image}</code>
            <div>
              目录验证范围：镜像仓库元数据。填入后仍需检查目标节点、原生版本与实际推理。{" "}
              {docsUrl && <a href={docsUrl} target="_blank" rel="noreferrer">
                使用文档 ↗
              </a>}
            </div>
            {hardware && hardware !== preset.hardware && (
              <div>
                当前配置的硬件类型为 {hardware}，请核对镜像与实际硬件配套。
              </div>
            )}
          </div>
        )}
        <div className="integration-control-row">
          <Field
            label="镜像检查节点"
            hint="检查所选节点本地已有镜像，不自动拉取"
          >
            <Select
              ariaLabel="镜像检查节点"
              value={nodeId}
              onChange={setNodeId}
            >
              <option value="">{nodesRead.loaded ? "请选择 Docker 执行节点" : readNotice(nodesRead)}</option>
              {dockerNodes.map((node) => (
                <option key={node.id} value={node.id}>
                  {node.name} · {node.address}
                </option>
              ))}
            </Select>
          </Field>
          <Button
            onClick={() => void inspect()}
            loading={busy}
            disabled={!nodesRead.loaded || !selectedNode || !image.trim()}
          >
            检查服务镜像
          </Button>
        </div>
        <label className="integration-checkbox">
          <input
            type="checkbox"
            checked={probe}
            onChange={(event) => setProbe(event.target.checked)}
          />
          运行受限 CPU 检查（不加载模型）
        </label>
        <p className="integration-caption">
          勾选后创建临时受限容器，检查 Python 包和原生启动入口；不挂载
          NPU、权重或宿主目录，不连接业务网络。
        </p>
        {!nodesRead.loaded ? <InfoBox>{readNotice(nodesRead)}节点列表，尚不能判断哪些 Agent 可检查镜像。</InfoBox> : !dockerNodes.length && (
          <InfoBox>
            当前没有可执行本地镜像检查的 Docker Agent。Kubernetes
            镜像由集群容器运行时与仓库准备，本地 Docker
            检查不能代替集群节点检查。
          </InfoBox>
        )}
        {runtime === "kubernetes" && dockerNodes.length > 0 && (
          <p className="integration-caption">
            本次为独立 Docker 节点的镜像内容检查，不表示 Kubernetes
            节点已拉取该镜像。
          </p>
        )}
        {error && <ErrorBox message={error} />}
        {result && (
          <div className="integration-report">
            <div className="integration-report-head">
              <h4>镜像检查结果</h4>
              <span className="integration-caption">
                {when(result.collected_at)}
              </span>
            </div>
            <dl className="integration-facts">
              <dt>检查镜像</dt>
              <dd>
                <code className="integration-code">{result.image}</code>
              </dd>
              <dt>镜像 ID</dt>
              <dd>
                <code className="integration-code">{result.image_id}</code>
              </dd>
              <dt>系统 / 架构</dt>
              <dd>
                {result.os} / {result.architecture}
              </dd>
              <dt>Motor Python 源码身份</dt>
              <dd>
                {result.identity.status === "verified"
                  ? "Python 源码比对一致"
                  : result.identity.status === "declared"
                    ? "标签声明一致，源码尚未证实"
                    : result.identity.status === "mismatch"
                      ? "与已固定 Python 源码不一致"
                      : "尚未证实"}
                ：{evidenceLabel[result.identity.evidence] ?? result.identity.evidence}
              </dd>
              <dt>CPU 内容检查</dt>
              <dd>{probeLabel[result.probe.status]}</dd>
              {Object.entries(result.probe.packages).map(([name, version]) => (
                <Fragment key={name}>
                  <dt>{name}</dt>
                  <dd>{version || "未检出"}</dd>
                </Fragment>
              ))}
            </dl>
            {result.probe.errors.length > 0 && (
              <ErrorBox
                message={`CPU 检查诊断：${result.probe.errors.map(probeErrorLabel).join("；")}`}
              />
            )}
            <ul className="integration-checks">
              {result.checks.map((check, index) => (
                <li key={`${check.name}-${index}`}>
                  <Badge status={check.status} />
                  <div>
                    <strong>{check.name}</strong>
                    <p>{check.detail}</p>
                  </div>
                </li>
              ))}
            </ul>
            <InfoBox>
              检查结果仅覆盖已列出的镜像内容与启动入口，不验证模型加载、NPU
              推理或多节点通信。
            </InfoBox>
          </div>
        )}
      </div>
    </details>
  );
}
