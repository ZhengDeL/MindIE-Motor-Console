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

function RateChart({ points, field, title, unit }: {
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
  return <section className="panel service-metric-chart">
    <div className="panel-head"><h2>{title}</h2><span className="small-caption">{unit}</span></div>
    {valid.length ? <>
      <svg viewBox="0 0 600 210" role="img" aria-labelledby={`${id}-title ${id}-description`}>
        <title id={`${id}-title`}>{title}</title>
        <desc id={`${id}-description`}>本次查看期间最近十分钟，{valid.length} 个有效窗口值；缺失或重置的窗口断开显示。数值单位 {unit}。</desc>
        {[0, max / 2, max].map((value) => <g key={value} className="service-metric-grid">
          <line x1="48" x2="580" y1={y(value)} y2={y(value)} />
          <text x="39" y={y(value) + 4} textAnchor="end">{metricNumber(value)}</text>
        </g>)}
        {paths.map((path, index) => <path key={index} d={path} className="service-metric-line" />)}
        {valid.map((point) => <circle key={point.at} cx={x(point)} cy={y(point[field]!)} r="3" className="service-metric-point">
          <title>{timeLabel(point.at)} · {metricNumber(point[field]!)} {unit}</title>
        </circle>)}
        <text className="service-metric-axis" x="48" y="200">{timeLabel(firstAt)}</text>
        <text className="service-metric-axis" x="580" y="200" textAnchor="end">{timeLabel(lastAt)}</text>
      </svg>
      <div className="service-metric-chart-caption">最近 10 分钟 · 本次查看期间的实际采样</div>
    </> : <Empty title="暂无有效速率样本" description="服务运行并取得两次有效采样后，才能计算速率；未取得的数据不会绘成 0。" icon={<Activity size={24} />} />}
  </section>;
}

export default function ServiceMetricsPanel({ service }: { service: Service }) {
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
  return <div className="service-metrics-panel">
    <div className="section-heading service-metrics-heading">
      <div><h2>服务指标</h2><p>只读采集 Coordinator 指标，不进入推理请求链路。</p></div>
      <div className="service-metrics-controls">
        <label><input type="checkbox" checked={auto} onChange={(event) => setAuto(event.target.checked)} />读取完成后 10 秒刷新</label>
        <Button onClick={() => void load()} loading={reading}><RefreshCw size={14} />刷新指标</Button>
      </div>
    </div>
    <div className="service-metrics-freshness" role="status" aria-live="polite">
      <span className={`status-dot ${fresh ? "green" : "muted"}`} />
      <span>{fresh ? "采样有效" : error ? "本次采样未成功" : view.snapshot ? "采样已过期" : "等待首个采样"}</span>
      {payload?.sampled_at && <span>采集于 {when(payload.sampled_at)}</span>}
      {!auto && <span>自动刷新已暂停</span>}
      {payload?.state === "demo" && <strong>演示数据</strong>}
    </div>
    {error && (payload?.state === "warming" ? <InfoBox>{error}</InfoBox> : <ErrorBox message={error} />)}
    {!payload && reading && <Spinner label="正在读取首个服务采样" />}
    {!!cards.length && <div className="service-metrics-cards">{cards.map((card) => <div className="metric-card" key={card.label}>
      <span>{card.label}</span><strong>{metricNumber(card.value!)}<small>{card.unit}</small></strong>
    </div>)}</div>}
    {fresh && !cards.length && <Empty title="本次未返回可展示的引擎指标" description="原始采样已保留；缺失或格式无效的指标不会显示为 0。" icon={<Activity size={24} />} />}
    {point?.notice && <p className="service-metrics-notice">{point.notice}</p>}
    {idle && <p className="service-metrics-notice">当前采样没有运行或等待请求。有效窗口中的速率 0 表示该窗口内计数没有增长。</p>}
    {!fresh && view.points.length > 0 && <p className="service-metrics-notice">下方保留本次查看期间的历史采样；过期数据不作为当前数值展示。</p>}
    <div className="service-metrics-charts">
      <RateChart points={view.points} field="completionRate" title="引擎完成速率" unit="次/s" />
      <RateChart points={view.points} field="generationRate" title="引擎生成吞吐" unit="tokens/s" />
    </div>
    {!!reasons.length && <section className="panel service-metrics-reasons">
      <div className="panel-head"><h2>引擎结束原因累计计数</h2><span className="small-caption">原生 finished_reason</span></div>
      <dl>{reasons.map(([reason, count]) => <div key={reason}><dt>{reasonLabels[reason as keyof typeof reasonLabels]}</dt><dd>{metricNumber(count!)}<small>次</small></dd></div>)}</dl>
    </section>}
    <p className="service-metrics-scope">统计口径：Coordinator 返回的 P/D 引擎计数，阶段可能分别计数，不等同于用户请求 QPS。完成计数仅统计 stop、length、repetition，{payload?.metrics_view === "dp" ? "按各引擎端点的计数增量计算速率；端点变化或计数重置时重新建立基线。" : "按两次有效采样间的计数增量计算速率。"}{payload?.metrics_view === "full" && "当前为汇总视图，单个端点重启可能无法独立识别。"}趋势仅保留本次查看期间最多 120 次采样，离开页面后清空。</p>
    {fresh && payload?.metrics_text && <section className="panel service-metrics-raw">
      <details><summary>查看原始采样</summary><pre className="metrics-view">{payload.metrics_text}</pre></details>
    </section>}
  </div>;
}
