import { UiElement, withUiSlot, UiText } from "./uiPluginSdk";
import type { Json } from "./api";
import { Field, InfoBox } from "./ui";
import { ChevronDown } from "./icons";
import "./integration.css";

const textFields = [
  ["id", "模型 ID", "留空时由 Motor 从引擎配置推导"],
  ["object", "对象类型", "原生默认值为 model"],
  ["owned_by", "模型归属", "原生默认值为 motor"],
] as const;
const numberFields = [
  ["p_max_seqlen", "Prefill 上下文上限", "Token，留空时由引擎配置推导"],
  ["d_max_seqlen", "Decode 上下文上限", "Token，留空时由引擎配置推导"],
  ["slo_ttft", "首 Token 时延 SLO", "毫秒，原生默认值为 1000"],
  ["slo_tpot", "Token 间时延 SLO", "毫秒，原生默认值为 50"],
] as const;
export type NativeAigwField =
  | (typeof textFields)[number][0]
  | (typeof numberFields)[number][0];
const isRecord = (value: unknown): value is Json =>
  Boolean(value && typeof value === "object" && !Array.isArray(value));

export function nativeAigwValue(config: Json): Json {
  const coordinator = config.motor_coordinator_config;
  if (coordinator !== undefined && !isRecord(coordinator))
    throw Error(
      "motor_coordinator_config 必须是 JSON 对象，请先修正原生配置。",
    );
  const aigw = coordinator?.aigw;
  if (aigw !== undefined && !isRecord(aigw))
    throw Error(
      "motor_coordinator_config.aigw 必须是 JSON 对象，请先修正原生配置。",
    );
  return aigw ?? {};
}

export function applyNativeAigwField(
  config: Json,
  field: NativeAigwField,
  text: string,
): Json {
  if (![...textFields, ...numberFields].some(([key]) => key === field))
    throw Error("不支持的 AIGW 可视配置字段。");
  const current = nativeAigwValue(config);
  const aigw = { ...current };
  if (text === "") delete aigw[field];
  else if (numberFields.some(([key]) => key === field)) {
    if (!/^[1-9][0-9]*$/.test(text) || !Number.isSafeInteger(Number(text)))
      throw Error("AIGW 数值必须为正整数，且不能超出浏览器安全整数范围。");
    aigw[field] = Number(text);
  } else {
    if (/[\u0000-\u001f\u007f]/.test(text))
      throw Error("AIGW 文本字段不能包含控制字符。");
    aigw[field] = text;
  }
  return {
    ...config,
    motor_coordinator_config: {
      ...config.motor_coordinator_config,
      aigw,
    },
  };
}

function DefaultNativeAigwEditor({
  config,
  onChange,
  onError,
}: {
  config: Json;
  onChange: (field: NativeAigwField, value: string) => void;
  onError: (message: string) => void;
}) {
  let aigw: Json;
  try {
    aigw = nativeAigwValue(config);
  } catch (error) {
    return <InfoBox>{(error as Error).message}</InfoBox>;
  }
  const display = (value: unknown) =>
    typeof value === "string" || typeof value === "number"
      ? String(value)
      : value === undefined
        ? ""
        : JSON.stringify(value);
  const update = (field: NativeAigwField, value: string) => {
    try {
      applyNativeAigwField(config, field, value);
      onChange(field, value);
      onError("");
    } catch (error) {
      onError((error as Error).message);
    }
  };
  return (
    <UiElement as="details" className="integration-section">
      <UiElement as="summary">
        <UiElement as="span"><UiText>{"原生 AIGW 模型配置"}</UiText></UiElement>
        <UiElement as="span" className="integration-summary-hint">
          <UiElement as="small"><UiText>{"模型发现 · 上下文与 SLO"}</UiText></UiElement>
          <ChevronDown size={15} />
        </UiElement>
      </UiElement>
      <UiElement as="div" className="integration-section-body">
        <UiElement as="p" className="integration-explanation">
          <UiText>{"配置 Coordinator 对外提供的模型元数据。空白字段保持原生推导；清空输入只移除该字段，其他配置与自定义字段继续保留。"}</UiText>
        </UiElement>
        <UiElement as="div" className="form-two">
          {textFields.map(([field, label, hint]) => (
            <Field key={field} label={label} hint={hint}>
              <UiElement as="input"
                aria-label={label}
                value={display(aigw[field])}
                onChange={(event) => update(field, event.target.value)}
                placeholder="由 Motor 生成"
              />
            </Field>
          ))}
          {numberFields.map(([field, label, hint]) => (
            <Field key={field} label={label} hint={hint}>
              <UiElement as="input"
                aria-label={label}
                inputMode="numeric"
                value={display(aigw[field])}
                onChange={(event) => update(field, event.target.value)}
                placeholder="使用原生默认值"
              />
            </Field>
          ))}
        </UiElement>
        <InfoBox>
          <UiText>{"业务链路：应用 → AIGW → Motor Coordinator → 推理引擎。此处配置由原生 /v1/models 等接口使用；独立网关实例的连接在“网关接入”中管理。"}</UiText>
        </InfoBox>
      </UiElement>
    </UiElement>
  );
}

const NativeAigwEditor = withUiSlot("component.NativeAigwEditor", DefaultNativeAigwEditor);
export default NativeAigwEditor;

// Reference package implementation exports.
export { DefaultNativeAigwEditor };
