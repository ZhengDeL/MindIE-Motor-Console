import type { Observations, Service } from "./api";
import { isResourceInteger } from "./resourceObservation";

const numericSample = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;

/** A summary of known ASCII metrics, not a replacement for a Prometheus scraper. */
export function serviceMetric(
  text: string,
  name: string,
  integer = false,
): number | null {
  let total = 0,
    matched = false;
  const series = new Set<string>();
  for (const source of text.split("\n")) {
    const line = source.trim();
    if (!line.startsWith(name) || !/[{\s]/.test(line[name.length] ?? ""))
      continue;
    matched = true;
    let position = name.length;
    if (line[position] === "{") {
      let quoted = false,
        escaped = false;
      for (position++; position < line.length; position++) {
        const character = line[position];
        if (escaped) escaped = false;
        else if (character === "\\" && quoted) escaped = true;
        else if (character === '"') quoted = !quoted;
        else if (character === "}" && !quoted) {
          position++;
          break;
        }
      }
      if (quoted || escaped) return null;
    }
    if (!/\s/.test(line[position] ?? "")) return null;
    const identity = line.slice(0, position);
    if (series.has(identity)) return null;
    series.add(identity);
    const samples = line.slice(position).trim().split(/\s+/);
    if (
      samples.length < 1 ||
      samples.length > 2 ||
      !numericSample.test(samples[0]) ||
      (samples.length === 2 && !/^-?\d+$/.test(samples[1]))
    )
      return null;
    // A Prometheus sample's optional timestamp follows the value. It must not
    // be mistaken for the metric by selecting the last whitespace token.
    const value = Number(samples[0]);
    if (
      !Number.isFinite(value) ||
      value < 0 ||
      (integer && !Number.isSafeInteger(value))
    )
      return null;
    total += value;
    if (!Number.isFinite(total) || (integer && !Number.isSafeInteger(total)))
      return null;
  }
  return matched ? total : null;
}

export function serviceReplicaPlan(service: Service) {
  return (
    [
      {
        key: "prefill",
        label: "Prefill",
        short: "P",
        value: service.replicas?.prefill,
      },
      {
        key: "decode",
        label: "Decode",
        short: "D",
        value: service.replicas?.decode,
      },
      {
        key: "union",
        label: "PD 混部",
        short: "混部",
        value: service.replicas?.union,
      },
    ] as const
  ).filter((item) => isResourceInteger(item.value));
}

export function serviceInstanceSummary(observations: Observations | null) {
  const items: unknown = observations?.instances?.instances;
  if (
    !Array.isArray(items) ||
    !items.every(
      (item) => item && typeof item === "object" && !Array.isArray(item),
    )
  )
    return null;
  const completeHealth = items.every(
    (item) =>
      item && typeof item === "object" && typeof item.healthy === "boolean",
  );
  return {
    registered: items.length,
    healthy: completeHealth
      ? items.filter((item) => item.healthy === true).length
      : null,
  };
}
