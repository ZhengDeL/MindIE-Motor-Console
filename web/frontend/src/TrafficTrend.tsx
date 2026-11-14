import { UiElement, withUiSlot, UiText } from "./uiPluginSdk";
import { useId, useMemo, useState } from "react";
import { Activity } from "./icons";
import { when } from "./api";
import type { Overview } from "./api";
import { Empty } from "./ui";

function DefaultTrafficTrend({ series }: { series: Overview["series"] }) {
  const [active, setActive] = useState<number | null>(null);
  const id = useId().replace(/:/g, "");
  const points = useMemo(() => series.filter(point =>
    Number.isFinite(point.requests) && point.requests >= 0 &&
    Number.isFinite(point.latency) && point.latency >= 0 && typeof point.time === "string"), [series]);
  if (!points.length) return <Empty title="尚无指标历史" description="接入连续采样数据后展示请求速率与时延趋势。" icon={<Activity size={26} />} />;
  const maxRequests = Math.max(...points.map(point => point.requests), 1) * 1.16;
  const maxLatency = Math.max(...points.map(point => point.latency), 1) * 1.16;
  const x = (index: number) => 56 + index / Math.max(1, points.length - 1) * 680;
  const y = (value: number, max: number) => 228 - value / max * 176;
  const path = (field: "requests" | "latency", max: number) => points.map((point, index) =>
    `${index ? "L" : "M"}${x(index)},${y(point[field], max)}`).join(" ");
  const current = active === null ? null : points[active];
  const requestsPath = path("requests", maxRequests);
  return <UiElement as="div" className="trend-chart" tabIndex={0} role="group" aria-label="请求速率与时延趋势，使用左右方向键查看采样"
    onKeyDown={event => {
      if (event.key === "Escape") setActive(null);
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      setActive(previous => event.key === "Home" ? 0 : event.key === "End" ? points.length - 1 :
        Math.max(0, Math.min(points.length - 1, (previous ?? points.length - 1) + (event.key === "ArrowLeft" ? -1 : 1))));
    }} onBlur={() => setActive(null)}>
    <UiElement as="svg" viewBox="0 0 780 270" role="img" aria-label="请求速率与请求时延的独立纵轴趋势">
      <UiElement as="defs"><UiElement as="linearGradient" id={`traffic-${id}`} x1="0" x2="0" y1="0" y2="1"><UiElement as="stop" offset="0%" stopColor="var(--series-requests)" stopOpacity=".15" /><UiElement as="stop" offset="100%" stopColor="var(--series-requests)" stopOpacity="0" /></UiElement></UiElement>
      <UiElement as="text" x="56" y="30" fill="var(--muted)" fontSize="var(--font-size-caption)"><UiText>{"req/s"}</UiText></UiElement><UiElement as="text" x="748" y="30" fill="var(--muted)" fontSize="var(--font-size-caption)"><UiText>{"ms"}</UiText></UiElement>
      {[0, 1, 2, 3, 4].map(index => <UiElement as="g" key={index}>
        <UiElement as="line" x1="56" x2="736" y1={52 + index * 44} y2={52 + index * 44} stroke="var(--border)" strokeDasharray="3 5" />
        <UiElement as="text" x="44" y={56 + index * 44} textAnchor="end" fill="var(--muted)" fontSize="var(--font-size-caption)">{Math.round(maxRequests * (4 - index) / 4)}</UiElement>
        <UiElement as="text" x="748" y={56 + index * 44} fill="var(--muted)" fontSize="var(--font-size-caption)">{Math.round(maxLatency * (4 - index) / 4)}</UiElement>
      </UiElement>)}
      {points.length > 1 && <UiElement as="path" d={`${requestsPath} L736,228 L56,228 Z`} fill={`url(#traffic-${id})`} />}
      <UiElement as="path" d={requestsPath} fill="none" stroke="var(--series-requests)" strokeWidth="2.4" strokeLinejoin="round" />
      <UiElement as="path" d={path("latency", maxLatency)} fill="none" stroke="var(--series-latency)" strokeWidth="1.8" strokeDasharray="5 4" />
      {points.map((point, index) => index % Math.max(1, Math.ceil(points.length / 6)) === 0 || index === points.length - 1 ?
        <UiElement as="text" key={index} x={x(index)} y="253" fill="var(--muted)" fontSize="var(--font-size-caption)" textAnchor="middle">{point.time.length > 8 && Number.isFinite(Date.parse(point.time)) ? new Date(point.time).toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false }) : point.time}</UiElement> : null)}
      {active !== null && current && <UiElement as="g"><UiElement as="line" x1={x(active)} x2={x(active)} y1="45" y2="228" stroke="var(--control-border)" strokeDasharray="4 3" /><UiElement as="circle" cx={x(active)} cy={y(current.requests, maxRequests)} r="4" fill="var(--series-requests)" stroke="var(--surface)" strokeWidth="2" /></UiElement>}
      {points.map((_, index) => <UiElement as="rect" key={index} x={x(index) - 340 / points.length} y="40" width={680 / points.length} height="195" fill="transparent" onMouseEnter={() => setActive(index)} onMouseLeave={() => setActive(null)} />)}
    </UiElement>
    <UiElement as="span" className="workbench-sr-only" aria-live="polite">{current ? `${when(current.time)}，请求速率 ${current.requests} req/s，时延 ${current.latency} ms` : ""}</UiElement>
    {current && active !== null && <UiElement as="div" className="chart-tooltip" style={{ left: `${Math.min(75, 10 + active / Math.max(1, points.length - 1) * 70)}%` }}><UiElement as="b">{when(current.time)}</UiElement><UiElement as="span"><UiElement as="i" className="chart-dot orange" /><UiText>{"完成请求"}</UiText> {current.requests} <UiText>{"req/s"}</UiText></UiElement><UiElement as="span"><UiElement as="i" className="chart-dot blue" /><UiText>{"时延"}</UiText> {current.latency} <UiText>{"ms"}</UiText></UiElement></UiElement>}
  </UiElement>;
}

const TrafficTrend = withUiSlot("component.TrafficTrend", DefaultTrafficTrend);
export default TrafficTrend;

// Reference package implementation exports.
export { DefaultTrafficTrend };
