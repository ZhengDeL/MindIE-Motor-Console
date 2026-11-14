import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { useEffect, useState } from "react";
import { usePollingQuery } from "./usePollingQuery";
import { SESSION_EXPIRED_EVENT } from "./brand";
import { api, when } from "./api";
import type { AigwModel, AigwModels, Service } from "./api";
import { RefreshCw } from "./icons";
import { Badge, Button, Empty, ErrorBox, InfoBox, Spinner } from "./ui";
import "./integration.css";
import { usePluginEnabled, useRuntimeAccess } from "./consolePlugins";

const MODEL_METRICS = [
  { key: "p_max_seqlen", label: "Prefill 长度上限", unit: "Token", minimum: 1 },
  { key: "d_max_seqlen", label: "Decode 长度上限", unit: "Token", minimum: 1 },
  { key: "slo_ttft", label: "首 Token 目标时延", unit: "ms", minimum: Number.MIN_VALUE },
  { key: "slo_tpot", label: "后续 Token 目标时延", unit: "ms", minimum: Number.MIN_VALUE },
  { key: "p_instances_num", label: "可用 Prefill 实例", unit: "个", minimum: 0 },
  { key: "d_instances_num", label: "可用 Decode 实例", unit: "个", minimum: 0 },
] as const;

function DefaultModelMetadata({ model }: { model: AigwModel }) {
  const metrics = MODEL_METRICS.flatMap((definition) => {
    const value = model[definition.key];
    return typeof value === "number" && Number.isFinite(value) && value >= definition.minimum
      ? [{ ...definition, value }]
      : [];
  });
  return (
    <UiElement as="article" className="native-model-row" role="listitem" aria-label={`${model.id} 的模型信息`}>
      <UiElement as="div" className="native-model-heading">
        <UiElement as="strong">{model.id}</UiElement>
        {(model.owned_by || model.object) && (
          <UiElement as="span" className="integration-caption">{[model.owned_by, model.object].filter(Boolean).join(" · ")}</UiElement>
        )}
      </UiElement>
      {metrics.length > 0 && <UiElement as="dl" className="native-model-metrics">
        {metrics.map((metric) => <UiElement as="div" key={metric.key}>
          <UiElement as="dt">{metric.label}</UiElement>
          <UiElement as="dd">{metric.value.toLocaleString("zh-CN")} <UiElement as="span">{metric.unit}</UiElement></UiElement>
        </UiElement>)}
      </UiElement>}
    </UiElement>
  );
}

function DefaultNativeAigwModelsPanel({
  service,
}: {
  service: Service;
}) {
  const runtimeAccess = useRuntimeAccess(service.runtime);
  const gatewaysEnabled = usePluginEnabled("gateways");
  const [result, setResult] = useState<AigwModels | null>(null);
  const [error, setError] = useState("");
  const { refresh: load, refreshing: busy, cancel } = usePollingQuery(
    `native-models:${service.id}:${service.endpoint}:${service.motor_profile}:${service.deployed_digest}`,
    async (signal) => {
      const response = await api<AigwModels>(`/services/${encodeURIComponent(service.id)}/aigw-models`, { signal });
      if (!response || !Array.isArray(response.items) || response.items.some((model) => !model || typeof model.id !== "string"))
        throw Error("模型列表格式不符合接口约定，未展示。请检查服务与平台版本后重试。");
      return response;
    }, {
      enabled: runtimeAccess.enabled && gatewaysEnabled,
      onSuccess: (response) => { setResult(response); setError(""); },
      onError: (failure) => setError(failure.message),
    },
  );
  useEffect(() => { setResult(null); setError(""); }, [service.id, service.endpoint, service.motor_profile, service.deployed_digest]);
  useEffect(() => {
    const invalidate = () => { cancel(); setResult(null); };
    window.addEventListener(SESSION_EXPIRED_EVENT, invalidate);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, invalidate);
  }, [cancel]);
  if (!gatewaysEnabled || !runtimeAccess.enabled) return <InfoBox>{runtimeAccess.reason || "网关接入插件未启用，已暂停模型发现读取。"}</InfoBox>;
  return (
    <UiRegion name="layout.panel" as="section" className="panel integration-models-panel">
      <UiElement as="div" className="panel-head">
        <UiElement as="div">
          <UiElement as="h2"><UiText>{"AIGW 模型发现"}</UiText></UiElement>
          {result?.collected_at && (
            <UiElement as="small" className="integration-caption">
              <UiText>{"最近读取："}</UiText>{when(result.collected_at)}
            </UiElement>
          )}
        </UiElement>
        <Button actionId="action.IntegrationModels.001" onClick={() => void load()} loading={busy}>
          <RefreshCw size={14} />
          <UiText>{"读取模型列表"}</UiText>
        </Button>
      </UiElement>
      <UiElement as="p" className="integration-explanation">
        <UiText>{"读取本服务 Coordinator 的原生 /v1/models 接口，供网关核对模型 ID、上下文与实例数量。可用 P/D 实例数与 NPU 卡数分别统计。"}</UiText>
      </UiElement>
      <InfoBox>
        <UiText>{"此处展示 Motor 提供的对接数据；网关本身的连通与路由状态请在网关接入中检查。"}</UiText>
      </InfoBox>
      {error && <ErrorBox message={error} />}
      {busy ? (
        <Spinner label="正在读取原生模型发现接口" />
      ) : (
        result && (
          <>
            {result.status === "demo" && (
              <InfoBox>
                <Badge status="warn"><UiText>{"演示数据"}</UiText></Badge>{" "}
                <UiText>{"当前为演示环境，不能据此认定模型或网关已部署。"}</UiText>
              </InfoBox>
            )}
            {result.error && result.status === "unavailable" && (
              <ErrorBox message={result.error} />
            )}
            {result.status === "not_deployed" ? (
              <Empty
                title="服务未运行"
                description="服务运行并提供 Coordinator 入口后，可读取原生模型列表。"
              />
            ) : result.status === "unavailable" ? (
              <Empty
                title="原生模型发现接口暂不可用"
                description="请核对 Coordinator 入口、AIGW 原生配置及接口返回；本次读取不会改变服务运行状态。"
              />
            ) : result.items.length ? (
              <UiElement as="div" className="native-model-list" role="list" aria-label="已发现的原生模型">
                {result.items.map((model) => <ModelMetadata key={model.id} model={model} />)}
              </UiElement>
            ) : (
              <Empty
                title="原生模型列表为空"
                description="Coordinator 本次没有返回模型元数据；此处不会根据模板生成模型列表。"
              />
            )}
          </>
        )
      )}
    </UiRegion>
  );
}

const ModelMetadata = withUiSlot("component.ModelMetadata", DefaultModelMetadata);
const NativeAigwModelsPanel = withUiSlot("component.NativeAigwModelsPanel", DefaultNativeAigwModelsPanel);
export default NativeAigwModelsPanel;

// Reference package implementation exports.
export { DefaultModelMetadata, DefaultNativeAigwModelsPanel };
