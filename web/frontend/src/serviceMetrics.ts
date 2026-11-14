/** Parse only the engine series displayed here. Missing or invalid data stays absent. */
export type MetricSeries = Record<string, number>;
type Sample = { identity: string; labels: Record<string, string>; value: number };
export type MetricSnapshot = {
  at: number;
  revision: string;
  completions: MetricSeries | null;
  generation: MetricSeries | null;
  running: number | null;
  waiting: number | null;
  finishes: Partial<Record<"stop" | "length" | "repetition" | "abort" | "error", number>>;
};
export type MetricPoint = {
  at: number;
  completionRate: number | null;
  generationRate: number | null;
  completions: number | null;
  generation: number | null;
  running: number | null;
  waiting: number | null;
  notice: string;
};

const names = {
  completions: "vllm:request_success_total",
  generation: "vllm:generation_tokens_total",
  running: "vllm:num_requests_running",
  waiting: "vllm:num_requests_waiting",
} as const;
const numberSyntax = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
const finishes = ["stop", "length", "repetition", "abort", "error"] as const;
const completed = new Set<string>(["stop", "length", "repetition"]);

function sample(line: string, name: string): Sample | null {
  let rest = line.slice(name.length);
  const labels: Record<string, string> = Object.create(null);
  if (rest.startsWith("{")) {
    rest = rest.slice(1).trimStart();
    while (!rest.startsWith("}")) {
      const match = /^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*("(?:[^"\\\r\n]|\\[\\"n])*")\s*/.exec(rest);
      if (!match || Object.hasOwn(labels, match[1])) return null;
      try {
        labels[match[1]] = JSON.parse(match[2]);
      } catch {
        return null;
      }
      rest = rest.slice(match[0].length);
      if (rest.startsWith(",")) {
        rest = rest.slice(1).trimStart();
        if (rest.startsWith("}")) break;
      } else if (!rest.startsWith("}")) return null;
    }
    rest = rest.slice(1);
  }
  if (!/^\s/.test(rest)) return null;
  const parts = rest.trim().split(/\s+/);
  if (parts.length > 2 || !numberSyntax.test(parts[0]) ||
      (parts.length === 2 && !/^-?\d+$/.test(parts[1]))) return null;
  const value = Number(parts[0]);
  if (!Number.isSafeInteger(value) || value < 0) return null;
  return { identity: JSON.stringify(Object.entries(labels).sort(([a], [b]) => a.localeCompare(b))), labels, value };
}

function series(text: string, name: string): Sample[] | null {
  const result: Sample[] = [];
  const identities = new Set<string>();
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line.startsWith(name) || !/[{\s]/.test(line[name.length] ?? "")) continue;
    const parsed = sample(line, name);
    if (!parsed || identities.has(parsed.identity)) return null;
    identities.add(parsed.identity);
    result.push(parsed);
  }
  return result.length ? result : null;
}

function mapSeries(values: Sample[] | null): MetricSeries | null {
  return values?.length ? Object.fromEntries(values.map((value) => [value.identity, value.value])) : null;
}

export function metricTotal(values: MetricSeries | null): number | null {
  if (!values) return null;
  const total = Object.values(values).reduce((sum, value) => sum + value, 0);
  return Number.isSafeInteger(total) ? total : null;
}

export function parseMetricSnapshot(text: string, at: number, revision: string): MetricSnapshot {
  const requests = series(text, names.completions);
  const qualified = requests?.every((item) => typeof item.labels.finished_reason === "string") ? requests : null;
  const counts: MetricSnapshot["finishes"] = {};
  for (const reason of finishes) {
    const selected = qualified?.filter((item) => item.labels.finished_reason === reason) ?? [];
    const count = metricTotal(mapSeries(selected));
    if (count !== null) counts[reason] = count;
  }
  return {
    at, revision,
    completions: mapSeries(qualified?.filter((item) => completed.has(item.labels.finished_reason)) ?? null),
    generation: mapSeries(series(text, names.generation)),
    running: metricTotal(mapSeries(series(text, names.running))),
    waiting: metricTotal(mapSeries(series(text, names.waiting))),
    finishes: counts,
  };
}

function counterRate(current: MetricSeries | null, previous: MetricSeries | null, elapsed: number): number | null {
  if (!current || !previous || !Number.isFinite(elapsed) || elapsed < 5 || elapsed > 45) return null;
  const keys = Object.keys(current);
  if (keys.length !== Object.keys(previous).length || keys.some((key) => !Object.hasOwn(previous, key))) return null;
  let delta = 0;
  for (const key of keys) {
    if (current[key] < previous[key]) return null;
    delta += current[key] - previous[key];
    if (!Number.isSafeInteger(delta)) return null;
  }
  return delta / elapsed;
}

export function metricPoint(current: MetricSnapshot, previous: MetricSnapshot | null): MetricPoint {
  const elapsed = previous ? (current.at - previous.at) / 1000 : 0;
  const baseline = previous?.revision === current.revision ? previous : null;
  const completionRate = counterRate(current.completions, baseline?.completions ?? null, elapsed);
  const generationRate = counterRate(current.generation, baseline?.generation ?? null, elapsed);
  const measurable = current.completions !== null || current.generation !== null;
  return {
    at: current.at, completionRate, generationRate,
    completions: metricTotal(current.completions), generation: metricTotal(current.generation),
    running: current.running, waiting: current.waiting,
    notice: !measurable ? "本次未取得可计算速率的引擎累计计数。"
      : !baseline ? "首个有效采样已取得，下一次采样后显示窗口速率。"
      : completionRate === null && generationRate === null
        ? "采样间隔、序列或计数发生变化，已重新建立速率基线。"
        : "",
  };
}

export function metricNumber(value: number): string {
  return value.toLocaleString("zh-CN", value > 0 && value < 0.01
    ? { maximumSignificantDigits: 3 }
    : { maximumFractionDigits: 2 });
}
