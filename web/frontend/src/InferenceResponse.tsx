import { UiElement, withUiSlot, UiText } from "./uiPluginSdk";
import type { InferenceResult } from "./api";
import { Copy, InfoBox } from "./ui";

function DefaultInferenceResponse({ answer }: { answer: InferenceResult }) {
  const text = typeof answer.text === "string" ? answer.text : "";
  const reasoning = typeof answer.reasoning === "string" ? answer.reasoning : "";
  const hasText = !!text.trim();
  const hasReasoning = !!reasoning.trim();
  const truncated = answer.truncated === true || answer.finish_reason === "length";
  const hasFinishReason = typeof answer.finish_reason === "string" && !!answer.finish_reason.trim();
  const incomplete = answer.output_status === "incomplete" || (
    hasFinishReason && !["stop", "length"].includes(answer.finish_reason!)
  );
  const status = !hasText
    ? hasReasoning ? "仅返回思考" : "无可展示内容"
    : truncated ? "回答已截断" : incomplete ? "回答未完成" : "已返回正文";
  const usage = [
    { label: "输入 Token", value: answer.usage?.prompt_tokens },
    { label: "生成 Token", value: answer.usage?.completion_tokens },
    { label: "其中思考 Token", value: answer.usage?.completion_tokens_details?.reasoning_tokens },
    { label: "总 Token", value: answer.usage?.total_tokens },
  ].filter((item): item is { label: string; value: number } => typeof item.value === "number" && Number.isSafeInteger(item.value) && item.value >= 0);
  return (
    <UiElement as="div" className="inference-result" aria-label="模型返回结果">
      <UiElement as="div" className="inference-result-status">
        <UiElement as="span" className={`tag ${hasText && !truncated && !incomplete ? "green" : "amber"}`}>{status}</UiElement>
        {typeof answer.latency_ms === "number" && Number.isFinite(answer.latency_ms) && answer.latency_ms >= 0 && (
          <UiElement as="span" className="small-caption"><UiText>{"耗时"}</UiText> {answer.latency_ms.toFixed(0)} <UiText>{"ms"}</UiText></UiElement>
        )}
        {hasText && <Copy value={text} label="复制回答正文" />}
      </UiElement>
      {!hasText ? (
        <InfoBox>
          <UiElement as="strong">{hasReasoning
            ? truncated ? "达到生成上限，当前仅返回思考内容" : "当前仅返回思考内容，尚无回答正文"
            : "模型未返回正文或思考内容"}</UiElement>
          <UiElement as="p">{hasReasoning && truncated
            ? "本次生成额度已耗尽。可按需提高“最大生成 Token”后手动发起新请求；平台不会自动重试或关闭模型思考。"
            : hasReasoning
              ? "可展开下方的模型思考过程。平台保留原始输出，不会将思考内容替换为回答正文。"
              : "请结合结束原因、Token 用量和运行日志检查原始模型返回；本次结果不代表已经生成有效回答。"}</UiElement>
        </InfoBox>
      ) : (
        <>
          {truncated && <InfoBox><UiElement as="strong"><UiText>{"回答因生成上限而截断"}</UiText></UiElement><UiElement as="p"><UiText>{"以下正文可能不完整。可按需提高“最大生成 Token”后手动发起新请求。"}</UiText></UiElement></InfoBox>}
          {!truncated && incomplete && <InfoBox><UiElement as="strong"><UiText>{"本次生成提前结束，回答可能不完整"}</UiText></UiElement><UiElement as="p"><UiText>{"请结合下方原生结束原因和运行日志检查。本次已收到的正文仍予保留，平台不会自动重发请求。"}</UiText></UiElement></InfoBox>}
          <UiElement as="div" className="inference-answer">{text}</UiElement>
        </>
      )}
      {hasReasoning && (
        <UiElement as="details" className="inference-reasoning">
          <UiElement as="summary"><UiText>{"模型思考过程"}</UiText></UiElement>
          <UiElement as="p" className="small-caption"><UiText>{"模型自行返回的思考内容，与回答正文分别保留。"}</UiText></UiElement>
          <UiElement as="div" className="inference-reasoning-content">{reasoning}</UiElement>
          <Copy value={reasoning} label="复制模型思考内容" />
        </UiElement>
      )}
      <UiElement as="div" className="inference-meta">
        {answer.demo && <UiElement as="span" className="tag amber"><UiText>{"演示响应"}</UiText></UiElement>}
        {hasFinishReason ? (
          <UiElement as="span"><UiText>{"结束原因："}</UiText><UiElement as="code">{answer.finish_reason}</UiElement></UiElement>
        ) : hasText && <UiElement as="span"><UiText>{"本次返回未提供结束原因，当前仅确认收到正文。"}</UiText></UiElement>}
      </UiElement>
      {!!usage.length && <UiElement as="dl" className="inference-token-usage">{usage.map((item) => (
        <UiElement as="div" key={item.label}><UiElement as="dt">{item.label}</UiElement><UiElement as="dd">{item.value.toLocaleString("zh-CN")}</UiElement></UiElement>
      ))}</UiElement>}
    </UiElement>
  );
}

const InferenceResponse = withUiSlot("component.InferenceResponse", DefaultInferenceResponse);
export default InferenceResponse;

// Reference package implementation exports.
export { DefaultInferenceResponse };
