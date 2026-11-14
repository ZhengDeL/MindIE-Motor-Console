import { UiElement, UiRegion, withUiSlot, UiText } from "./uiPluginSdk";
import { useEffect, useId, useRef, useState } from "react";
import { api, when } from "./api";
import type { Service, ServiceMetrics } from "./api";
import { Activity, RefreshCw } from "./icons";
import { Button, Empty, ErrorBox, InfoBox, Spinner } from "./ui";
import { serverNow } from "./serverClock";
import { metricNumber, metricPoint, parseMetricSnapshot } from "./serviceMetrics";
import type { MetricPoint, MetricSnapshot } from "./serviceMetrics";
import "./service-metrics.css";
import { usePollingQuery } from "./usePollingQuery";
import { useRuntimeAccess } from "./consolePlugins";

type View = { payload: ServiceMetrics | null; snapshot: MetricSnapshot | null; points: MetricPoint[] };
const initial = (): View => ({ payload: null, snapshot: null, points: [] });
const timeLabel = (at: number) => new Date(at).toLocaleTimeString("zh-CN", { hour12: false });

function DefaultRateChart({ points, field, title, unit }: {
  points: MetricPoint[]; field: "completionRate" | "generationRate"; title: string; unit: string;
}) {
  const id = useId();
  const lastAt = points.at(-1)?.at ?? 0;
  const recent = points.filter((point) => point.at >= lastAt - 600_000);
  const valid = recent.filter((point) => point[field] !== null);
  const max = Math.max(1, ...valid.map((point) => point[field]!));
  const firstAt = recent[0]?.at ?? lastAt;
  const range = Math.max(1, lastAt - firstAt);
  const x = (point: MetricPoint) => 48 + (point.at - firstAt) / range * 532;
  const y = (value: number) => 174 - value / max * 142;
  const paths: string[] = [];
  let current = "";
  for (const point of recent) {
    if (point[field] === null) { if (current) paths.push(current); current = ""; continue; }
    current += `${current ? " L" : "M"}${x(point).toFixed(2)},${y(point[field]!).toFixed(2)}`;
  }
  if (current) paths.push(current);
  return <UiRegion name="layout.panel" as="section" className="panel service-metric-chart">
    <UiElement as="div" className="panel-head"><UiElement as="h2">{title}</UiElement><UiElement as="span" className="small-caption">{unit}</UiElement></UiElement>
    {valid.length ? <>
      <UiElement as="svg" viewBox="0 0 600 210" role="img" aria-labelledby={`${id}-title ${id}-description`}>
        <UiElement as="title" id={`${id}-title`}>{title}</UiElement>
        <UiElement as="desc" id={`${id}-description`}><UiText>{"本次查看期间最近十分钟，"}</UiText>{valid.length} <UiText>{"个有效窗口值；缺失或重置的窗口断开显示。数值单位"}</UiText> {unit}<UiText>{"。"}</UiText></UiElement>
        {[0, max / 2, max].map((value) => <UiElement as="g" key={value} className="service-metric-grid">
          <UiElement as="line" x1="48" x2="580" y1={y(value)} y2={y(value)} />
          <UiElement as="text" x="39" y={y(value) + 4} textAnchor="end">{metricNumber(value)}</UiElement>
        </UiElement>)}
        {paths.map((path, index) => <UiElement as="path" key={index} d={path} className="service-metric-line" />)}
        {valid.map((point) => <UiElement as="circle" key={point.at} cx={x(point)} cy={y(point[field]!)} r="3" className="service-metric-point">
          <UiElement as="title">{timeLabel(point.at)} <UiText>{"·"}</UiText> {metricNumber(point[field]!)} {unit}</UiElement>
        </UiElement>)}
        <UiElement as="text" className="service-metric-axis" x="48" y="200">{timeLabel(firstAt)}</UiElement>
        <UiElement as="text" className="service-metric-axis" x="580" y="200" textAnchor="end">{timeLabel(lastAt)}</UiElement>
      </UiElement>
      <UiElement as="div" className="service-metric-chart-caption"><UiText>{"最近 10 分钟 · 本次查看期间的实际采样"}</UiText></UiElement>
    </> : <Empty title="暂无有效速率样本" description="服务运行并取得两次有效采样后，才能计算速率；未取得的数据不会绘成 0。" icon={<Activity size={24} />} />}
  </UiRegion>;
}

function DefaultServiceMetricsPanel({ service }: { service: Service }) {
  const runtimeAccess = useRuntimeAccess(service.runtime);
  const [view, setView] = useState<View>(initial);
  const [auto, setAuto] = useState(true);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(serverNow);
  const state = useRef<View>(initial());
  const baseline = useRef<MetricSnapshot | null>(null);
  const { refresh: load, refreshing: reading } = usePollingQuery(
    `service-metrics:${service.id}`,
    (signal) => api<ServiceMetrics>(`/services/${encodeURIComponent(service.id)}/metrics`, { signal }),
    { enabled: runtimeAccess.enabled, intervalMs: auto ? 10_000 : 0, onSuccess: (payload) => {
      if (payload.service_id !== service.id) throw Error("收到的指标不属于当前服务，未展示。");
      const now = serverNow();
      const at = Date.parse(payload.sampled_at ?? "");
      const expires = Date.parse(payload.expires_at ?? "");
      const fresh = ["ready", "demo"].includes(payload.state) && Number.isFinite(at) && Number.isFinite(expires) &&
        at <= now + 5000 && expires > at && expires > now && typeof payload.revision === "string" && !!payload.revision;
      if (state.current.payload?.revision && payload.revision !== state.current.payload.revision) {
        state.current = initial(); baseline.current = null;
      }
      if (!fresh) {
        baseline.current = null;
        state.current = { ...state.current, payload, snapshot: null };
        setView(state.current);
        setError(payload.error ?? (payload.state === "warming" ? "采样正在进行，下一次刷新将读取结果。" : "本次未取得有效采样，请稍后刷新。"));
        return;
      }
      if (state.current.snapshot && at < state.current.snapshot.at) throw Error("收到较旧的采样，未覆盖当前指标。");
      if (state.current.snapshot && at === state.current.snapshot.at) {
        baseline.current = state.current.snapshot;
        state.current = { ...state.current, payload };
      } else {
        const snapshot = parseMetricSnapshot(payload.metrics_text, at, payload.revision);
        const point = metricPoint(snapshot, baseline.current);
        state.current = { payload, snapshot, points: [...state.current.points, point].slice(-120) };
        baseline.current = snapshot;
      }
      setView(state.current); setError(""); setClock(now);
    }, onError: (reason) => { baseline.current = null; setError(reason.message); } },
  );
  useEffect(() => {
    if (!runtimeAccess.enabled) return;
    const timer = window.setInterval(() => setClock(serverNow()), 2000);
    return () => clearInterval(timer);
  }, [runtimeAccess.enabled]);
  const payload = view.payload;
  const fresh = !error && !!view.snapshot && clock < Date.parse(payload?.expires_at ?? "");
  const point = fresh ? view.points.at(-1) : null;
  const cards = point ? [
    { label: "引擎完成速率", value: point.completionRate, unit: "次/s" },
    { label: "引擎生成吞吐", value: point.generationRate, unit: "tokens/s" },
    { label: "运行中请求", value: point.running, unit: "个" },
    { label: "等待中请求", value: point.waiting, unit: "个" },
    { label: "累计引擎完成计数", value: point.completions, unit: "次" },
    { label: "累计引擎生成 Token", value: point.generation, unit: "Token" },
  ].filter((card) => card.value !== null) : [];
  const reasonLabels = { stop: "正常结束", length: "达到长度上限", repetition: "重复检测终止", abort: "取消", error: "错误" };
  const reasons = fresh && view.snapshot ? Object.entries(view.snapshot.finishes) : [];
  const idle = point?.running === 0 && point.waiting === 0;
  if (!runtimeAccess.enabled) return <InfoBox>{runtimeAccess.reason}</InfoBox>;
  return <UiElement as="div" className="service-metrics-panel">
    <UiElement as="div" className="section-heading service-metrics-heading">
      <UiElement as="div"><UiElement as="h2"><UiText>{"服务指标"}</UiText></UiElement><UiElement as="p"><UiText>{"只读采集 Coordinator 指标，不进入推理请求链路。"}</UiText></UiElement></UiElement>
      <UiElement as="div" className="service-metrics-controls">
        <UiElement as="label"><UiElement as="input" type="checkbox" checked={auto} onChange={(event) => setAuto(event.target.checked)} /><UiText>{"读取完成后 10 秒刷新"}</UiText></UiElement>
        <Button actionId="action.ServiceMetricsPanel.001" onClick={() => void load()} loading={reading}><RefreshCw size={14} /><UiText>{"刷新指标"}</UiText></Button>
      </UiElement>
    </UiElement>
    <UiElement as="div" className="service-metrics-freshness" role="status" aria-live="polite">
      <UiElement as="span" className={`status-dot ${fresh ? "green" : "muted"}`} />
      <UiElement as="span">{fresh ? "采样有效" : error ? "本次采样未成功" : view.snapshot ? "采样已过期" : "等待首个采样"}</UiElement>
      {payload?.sampled_at && <UiElement as="span"><UiText>{"采集于"}</UiText> {when(payload.sampled_at)}</UiElement>}
      {!auto && <UiElement as="span"><UiText>{"自动刷新已暂停"}</UiText></UiElement>}
      {payload?.state === "demo" && <UiElement as="strong"><UiText>{"演示数据"}</UiText></UiElement>}
    </UiElement>
    {error && (payload?.state === "warming" ? <InfoBox>{error}</InfoBox> : <ErrorBox message={error} />)}
    {!payload && reading && <Spinner label="正在读取首个服务采样" />}
    {!!cards.length && <UiElement as="div" className="service-metrics-cards">{cards.map((card) => <UiElement as="div" className="metric-card" key={card.label}>
      <UiElement as="span">{card.label}</UiElement><UiElement as="strong">{metricNumber(card.value!)}<UiElement as="small">{card.unit}</UiElement></UiElement>
    </UiElement>)}</UiElement>}
    {fresh && !cards.length && <Empty title="本次未返回可展示的引擎指标" description="原始采样已保留；缺失或格式无效的指标不会显示为 0。" icon={<Activity size={24} />} />}
    {point?.notice && <UiElement as="p" className="service-metrics-notice">{point.notice}</UiElement>}
    {idle && <UiElement as="p" className="service-metrics-notice"><UiText>{"当前采样没有运行或等待请求。有效窗口中的速率 0 表示该窗口内计数没有增长。"}</UiText></UiElement>}
    {!fresh && view.points.length > 0 && <UiElement as="p" className="service-metrics-notice"><UiText>{"下方保留本次查看期间的历史采样；过期数据不作为当前数值展示。"}</UiText></UiElement>}
    <UiElement as="div" className="service-metrics-charts">
      <RateChart points={view.points} field="completionRate" title="引擎完成速率" unit="次/s" />
      <RateChart points={view.points} field="generationRate" title="引擎生成吞吐" unit="tokens/s" />
    </UiElement>
    {!!reasons.length && <UiRegion name="layout.panel" as="section" className="panel service-metrics-reasons">
      <UiElement as="div" className="panel-head"><UiElement as="h2"><UiText>{"引擎结束原因累计计数"}</UiText></UiElement><UiElement as="span" className="small-caption"><UiText>{"原生 finished_reason"}</UiText></UiElement></UiElement>
      <UiElement as="dl">{reasons.map(([reason, count]) => <UiElement as="div" key={reason}><UiElement as="dt">{reasonLabels[reason as keyof typeof reasonLabels]}</UiElement><UiElement as="dd">{metricNumber(count!)}<UiElement as="small"><UiText>{"次"}</UiText></UiElement></UiElement></UiElement>)}</UiElement>
    </UiRegion>}
    <UiElement as="p" className="service-metrics-scope"><UiText>{"统计口径：Coordinator 返回的 P/D 引擎计数，阶段可能分别计数，不等同于用户请求 QPS。完成计数仅统计 stop、length、repetition，"}</UiText>{payload?.metrics_view === "dp" ? "按各引擎端点的计数增量计算速率；端点变化或计数重置时重新建立基线。" : "按两次有效采样间的计数增量计算速率。"}{payload?.metrics_view === "full" && "当前为汇总视图，单个端点重启可能无法独立识别。"}<UiText>{"趋势仅保留本次查看期间最多 120 次采样，离开页面后清空。"}</UiText></UiElement>
    {fresh && payload?.metrics_text && <UiRegion name="layout.panel" as="section" className="panel service-metrics-raw">
      <UiElement as="details"><UiElement as="summary"><UiText>{"查看原始采样"}</UiText></UiElement><UiElement as="pre" className="metrics-view">{payload.metrics_text}</UiElement></UiElement>
    </UiRegion>}
  </UiElement>;
}

const RateChart = withUiSlot("component.RateChart", DefaultRateChart);
const ServiceMetricsPanel = withUiSlot("component.ServiceMetricsPanel", DefaultServiceMetricsPanel);
export default ServiceMetricsPanel;

// Reference package implementation exports.
export { DefaultRateChart, DefaultServiceMetricsPanel };
