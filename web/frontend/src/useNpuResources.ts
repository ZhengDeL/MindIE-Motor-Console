import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type { NodeInfo } from "./api";
import {
  aggregateKubernetesNpu,
  isKubernetesAgent,
  validateKubernetesInventory,
} from "./npuResources";
import type { AgentNpuInventory } from "./npuResources";
import { useObservationClock } from "./resourceObservation";

export type NpuResourcesState = ReturnType<typeof useNpuResources>;
type Snapshot = { key: string; sources: AgentNpuInventory[] };
const REFRESH_MS = 30_000;
const MAX_BACKOFF_MS = 120_000;

/** One session-scoped reader, three concurrent requests, and no background-tab polling. */
export function useNpuResources(
  nodes: readonly NodeInfo[],
  {
    active,
    sessionKey,
    contextKey,
  }: { active: boolean; sessionKey: string; contextKey: string },
) {
  const [revision, setRevision] = useState(0);
  const [visible, setVisible] = useState(() => !document.hidden);
  const [snapshot, setSnapshot] = useState<Snapshot>({ key: "", sources: [] });
  const latest = useRef(snapshot);
  const generation = useRef(0);
  const now = useObservationClock(active);
  // Host observations have their own sampler; their normal refresh must not restart this reader.
  const fingerprint = JSON.stringify(
    nodes
      .filter(isKubernetesAgent)
      .map((node) => ({
        id: node.id,
        name: node.name,
        address: node.address,
        url: node.agent_url,
        status: node.status,
        kubernetes: node.kubernetes_available,
        version: node.kubernetes_version,
        nic: node.nic_name,
        labels: [...node.labels].sort(),
      }))
      .sort((a, b) => a.id.localeCompare(b.id)),
  );
  const agents = useMemo(
    () =>
      JSON.parse(fingerprint) as { id: string; name: string; status: string }[],
    [fingerprint],
  );
  // The browser session identity only invalidates requests. It is never rendered or persisted.
  const key = active
    ? JSON.stringify([sessionKey, contextKey, fingerprint])
    : "";
  const pending = useMemo<AgentNpuInventory[]>(
    () =>
      active
        ? agents.map((agent) => ({
            agentId: agent.id,
            agentName: agent.name,
            status: agent.status === "offline" ? "offline" : "loading",
          }))
        : [],
    [active, agents],
  );

  useEffect(() => {
    const sync = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const retries = new Map<string, { failures: number; nextAt: number }>();
    let sources =
      latest.current.key === key
        ? latest.current.sources.map((source) => ({ ...source }))
        : pending;
    const publish = () => {
      if (current !== generation.current || controller.signal.aborted) return;
      const value = { key, sources: [...sources] };
      latest.current = value;
      setSnapshot(value);
    };
    if (!key) {
      sources = [];
      publish();
      return () => {
        generation.current++;
        controller.abort();
      };
    }
    if (!visible)
      return () => {
        generation.current++;
        controller.abort();
      };
    const poll = async () => {
      if (controller.signal.aborted) return;
      let next = 0;
      const capturedAt = performance.now();
      const queue = sources.flatMap((source, index) =>
        source.status !== "offline" &&
        (retries.get(source.agentId)?.nextAt ?? 0) <= capturedAt
          ? [index]
          : [],
      );
      const due = new Set(queue);
      sources = sources.map((source, index) =>
        due.has(index) ? { ...source, refreshing: true } : source,
      );
      publish();
      const worker = async () => {
        while (next < queue.length && !controller.signal.aborted) {
          const index = queue[next++];
          const request = new AbortController();
          const abort = () => request.abort();
          controller.signal.addEventListener("abort", abort, { once: true });
          const timeout = setTimeout(abort, 25_000);
          try {
            const inventory = validateKubernetesInventory(
              await api<unknown>(
                `/nodes/${encodeURIComponent(sources[index].agentId)}/kubernetes/nodes`,
                { signal: request.signal },
              ),
            );
            if (controller.signal.aborted) return;
            retries.set(sources[index].agentId, {
              failures: 0,
              nextAt: performance.now() + REFRESH_MS,
            });
            sources[index] = {
              ...sources[index],
              status: "ready",
              inventory,
              refreshing: false,
              error: undefined,
            };
          } catch (reason) {
            if (controller.signal.aborted) return;
            const failures = Math.min(
              (retries.get(sources[index].agentId)?.failures ?? 0) + 1,
              2,
            );
            retries.set(sources[index].agentId, {
              failures,
              nextAt:
                performance.now() +
                Math.min(MAX_BACKOFF_MS, REFRESH_MS * 2 ** failures),
            });
            sources[index] = {
              ...sources[index],
              status: "error",
              refreshing: false,
              error: request.signal.aborted
                ? "集群采集超时，请检查节点连接"
                : reason instanceof Error
                  ? reason.message
                  : "采集失败",
            };
          } finally {
            clearTimeout(timeout);
            controller.signal.removeEventListener("abort", abort);
          }
          publish();
        }
      };
      await Promise.all(
        Array.from({ length: Math.min(3, queue.length) }, worker),
      );
      if (controller.signal.aborted) return;
      const nextReads = sources
        .filter((source) => source.status !== "offline")
        .map(
          (source) =>
            retries.get(source.agentId)?.nextAt ??
            performance.now() + REFRESH_MS,
        );
      timer = setTimeout(
        () => void poll(),
        nextReads.length
          ? Math.max(1000, Math.min(...nextReads) - performance.now())
          : MAX_BACKOFF_MS,
      );
    };
    void poll();
    return () => {
      generation.current++;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [key, pending, visible, revision]);

  const sources = snapshot.key === key ? snapshot.sources : pending;
  const summary = useMemo(
    () => aggregateKubernetesNpu(sources, now),
    [sources, now],
  );
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  return {
    sources,
    summary,
    loading: sources.some(
      (source) => source.status === "loading" || source.refreshing,
    ),
    refresh,
  };
}
