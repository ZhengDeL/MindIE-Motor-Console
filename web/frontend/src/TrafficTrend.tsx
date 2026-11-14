import { useId, useMemo, useState } from "react";
import { Activity } from "./icons";
import { when } from "./api";
import type { Overview } from "./api";
import { Empty } from "./ui";

export default function TrafficTrend({ series }: { series: Overview["series"] }) {
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
  return <div className="trend-chart" tabIndex={0} role="group" aria-label="请求速率与时延趋势，使用左右方向键查看采样"
    onKeyDown={event => {
      if (event.key === "Escape") setActive(null);
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      setActive(previous => event.key === "Home" ? 0 : event.key === "End" ? points.length - 1 :
        Math.max(0, Math.min(points.length - 1, (previous ?? points.length - 1) + (event.key === "ArrowLeft" ? -1 : 1))));
    }} onBlur={() => setActive(null)}>
    <svg viewBox="0 0 780 270" role="img" aria-label="请求速率与请求时延的独立纵轴趋势">
      <defs><linearGradient id={`traffic-${id}`} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="var(--series-requests)" stopOpacity=".15" /><stop offset="100%" stopColor="var(--series-requests)" stopOpacity="0" /></linearGradient></defs>
      <text x="56" y="30" fill="var(--muted)" fontSize="var(--font-size-caption)">req/s</text><text x="748" y="30" fill="var(--muted)" fontSize="var(--font-size-caption)">ms</text>
      {[0, 1, 2, 3, 4].map(index => <g key={index}>
        <line x1="56" x2="736" y1={52 + index * 44} y2={52 + index * 44} stroke="var(--border)" strokeDasharray="3 5" />
        <text x="44" y={56 + index * 44} textAnchor="end" fill="var(--muted)" fontSize="var(--font-size-caption)">{Math.round(maxRequests * (4 - index) / 4)}</text>
        <text x="748" y={56 + index * 44} fill="var(--muted)" fontSize="var(--font-size-caption)">{Math.round(maxLatency * (4 - index) / 4)}</text>
      </g>)}
      {points.length > 1 && <path d={`${requestsPath} L736,228 L56,228 Z`} fill={`url(#traffic-${id})`} />}
      <path d={requestsPath} fill="none" stroke="var(--series-requests)" strokeWidth="2.4" strokeLinejoin="round" />
      <path d={path("latency", maxLatency)} fill="none" stroke="var(--series-latency)" strokeWidth="1.8" strokeDasharray="5 4" />
      {points.map((point, index) => index % Math.max(1, Math.ceil(points.length / 6)) === 0 || index === points.length - 1 ?
        <text key={index} x={x(index)} y="253" fill="var(--muted)" fontSize="var(--font-size-caption)" textAnchor="middle">{point.time.length > 8 && Number.isFinite(Date.parse(point.time)) ? new Date(point.time).toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false }) : point.time}</text> : null)}
      {active !== null && current && <g><line x1={x(active)} x2={x(active)} y1="45" y2="228" stroke="var(--control-border)" strokeDasharray="4 3" /><circle cx={x(active)} cy={y(current.requests, maxRequests)} r="4" fill="var(--series-requests)" stroke="var(--surface)" strokeWidth="2" /></g>}
      {points.map((_, index) => <rect key={index} x={x(index) - 340 / points.length} y="40" width={680 / points.length} height="195" fill="transparent" onMouseEnter={() => setActive(index)} onMouseLeave={() => setActive(null)} />)}
    </svg>
    <span className="workbench-sr-only" aria-live="polite">{current ? `${when(current.time)}，请求速率 ${current.requests} req/s，时延 ${current.latency} ms` : ""}</span>
    {current && active !== null && <div className="chart-tooltip" style={{ left: `${Math.min(75, 10 + active / Math.max(1, points.length - 1) * 70)}%` }}><b>{when(current.time)}</b><span><i className="chart-dot orange" />完成请求 {current.requests} req/s</span><span><i className="chart-dot blue" />时延 {current.latency} ms</span></div>}
  </div>;
}
