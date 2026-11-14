import type { InferenceResult } from "./api";
import { Copy, InfoBox } from "./ui";

export default function InferenceResponse({ answer }: { answer: InferenceResult }) {
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
  ].filter((item) => typeof item.value === "number" && Number.isSafeInteger(item.value) && item.value >= 0);
  return (
    <div className="inference-result" aria-label="模型返回结果">
      <div className="inference-result-status">
        <span className={`tag ${hasText && !truncated && !incomplete ? "green" : "amber"}`}>{status}</span>
        {typeof answer.latency_ms === "number" && Number.isFinite(answer.latency_ms) && answer.latency_ms >= 0 && (
          <span className="small-caption">耗时 {answer.latency_ms.toFixed(0)} ms</span>
        )}
        {hasText && <Copy value={text} label="复制回答正文" />}
      </div>
      {!hasText ? (
        <InfoBox>
          <strong>{hasReasoning
            ? truncated ? "达到生成上限，当前仅返回思考内容" : "当前仅返回思考内容，尚无回答正文"
            : "模型未返回正文或思考内容"}</strong>
          <p>{hasReasoning && truncated
            ? "本次生成额度已耗尽。可按需提高“最大生成 Token”后手动发起新请求；平台不会自动重试或关闭模型思考。"
            : hasReasoning
              ? "可展开下方的模型思考过程。平台保留原始输出，不会将思考内容替换为回答正文。"
              : "请结合结束原因、Token 用量和运行日志检查原始模型返回；本次结果不代表已经生成有效回答。"}</p>
        </InfoBox>
      ) : (
        <>
          {truncated && <InfoBox><strong>回答因生成上限而截断</strong><p>以下正文可能不完整。可按需提高“最大生成 Token”后手动发起新请求。</p></InfoBox>}
          {!truncated && incomplete && <InfoBox><strong>本次生成提前结束，回答可能不完整</strong><p>请结合下方原生结束原因和运行日志检查。本次已收到的正文仍予保留，平台不会自动重发请求。</p></InfoBox>}
          <div className="inference-answer">{text}</div>
        </>
      )}
      {hasReasoning && (
        <details className="inference-reasoning">
          <summary>模型思考过程</summary>
          <p className="small-caption">模型自行返回的思考内容，与回答正文分别保留。</p>
          <div className="inference-reasoning-content">{reasoning}</div>
          <Copy value={reasoning} label="复制模型思考内容" />
        </details>
      )}
      <div className="inference-meta">
        {answer.demo && <span className="tag amber">演示响应</span>}
        {hasFinishReason ? (
          <span>结束原因：<code>{answer.finish_reason}</code></span>
        ) : hasText && <span>本次返回未提供结束原因，当前仅确认收到正文。</span>}
      </div>
      {!!usage.length && <dl className="inference-token-usage">{usage.map((item) => (
        <div key={item.label}><dt>{item.label}</dt><dd>{item.value.toLocaleString("zh-CN")}</dd></div>
      ))}</dl>}
    </div>
  );
}
