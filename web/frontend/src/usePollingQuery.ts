import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { DATA_CHANGED_EVENT } from "./brand";

type QueryOptions<T> = {
  enabled?: boolean;
  intervalMs?: number;
  maxIntervalMs?: number;
  invalidateOn?: readonly string[];
  onSuccess: (value: T) => void;
  onError: (error: Error) => void;
};

/** A mounted query owns one flight. Polling waits for completion and pauses while hidden. */
export function usePollingQuery<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  { enabled = true, intervalMs = 0, maxIntervalMs, invalidateOn = [], onSuccess, onError }: QueryOptions<T>,
) {
  const callbacks = useRef({ load, onSuccess, onError });
  useLayoutEffect(() => { callbacks.current = { load, onSuccess, onError }; });
  const scope = useRef<{ run: () => Promise<void>; dispose: () => void; reschedule: () => void } | null>(null);
  const interval = maxIntervalMs !== undefined && Number.isFinite(maxIntervalMs) && maxIntervalMs > 0
    ? Math.min(intervalMs, maxIntervalMs) : intervalMs;
  const timing = useRef(interval);
  useLayoutEffect(() => { timing.current = interval; scope.current?.reschedule(); }, [interval]);
  const [refreshing, setRefreshing] = useState(false);
  const [successfulKey, setSuccessfulKey] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => scope.current?.run() ?? Promise.resolve(), []);
  const cancel = useCallback(() => {
    scope.current?.dispose();
    scope.current = null;
    setRefreshing(false);
  }, []);
  const invalidate = useCallback(() => {
    scope.current?.dispose();
    scope.current = null;
    setRefreshing(false);
    setRevision((previous) => previous + 1);
  }, []);
  const invalidationPaths = invalidateOn.join("\n");
  useEffect(() => {
    if (!invalidationPaths) return;
    const paths = invalidationPaths.split("\n");
    const changed = (event: Event) => {
      const path = (event as CustomEvent<{ path?: string }>).detail?.path;
      if (path && paths.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))) invalidate();
    };
    window.addEventListener(DATA_CHANGED_EVENT, changed);
    return () => window.removeEventListener(DATA_CHANGED_EVENT, changed);
  }, [invalidationPaths, invalidate]);

  useLayoutEffect(() => {
    if (!enabled) {
      setRefreshing(false);
      return;
    }
    let disposed = false;
    let pending: Promise<void> | null = null;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let retryAfterMs = 0;
    const clearTimer = () => {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
    };
    const schedule = () => {
      clearTimer();
      const interval = timing.current;
      if (disposed || !interval || document.hidden || navigator.onLine === false) return;
      const backoff = Math.min(300_000, interval * 2 ** Math.min(failures, 4));
      timer = setTimeout(() => void run(), Math.max(backoff, retryAfterMs));
    };
    const run = (): Promise<void> => {
      if (disposed) return Promise.resolve();
      if (pending) return pending;
      clearTimer();
      controller = new AbortController();
      const request = controller;
      const deadline = setTimeout(() => request.abort(new DOMException("读取超过 90 秒，请检查连接后重试。", "TimeoutError")), 90_000);
      setRefreshing(true);
      const work = callbacks.current.load;
      pending = Promise.resolve().then(async () => {
        try {
          if (disposed || request.signal.aborted) return;
          const value = await work(request.signal);
          if (disposed) return;
          if (request.signal.aborted) throw request.signal.reason;
          failures = 0;
          retryAfterMs = 0;
          callbacks.current.onSuccess(value);
          setSuccessfulKey(key);
        } catch (reason) {
          if (disposed || (request.signal.aborted && request.signal.reason?.name !== "TimeoutError")) return;
          failures++;
          const error = reason instanceof Error ? reason : Error("无法读取数据，请重试");
          const retry = (error as Error & { retryAfterMs?: number }).retryAfterMs;
          retryAfterMs = typeof retry === "number" && Number.isFinite(retry)
            ? Math.max(0, Math.min(retry, 300_000)) : 0;
          callbacks.current.onError(error);
        } finally {
          clearTimeout(deadline);
          pending = null;
          if (!disposed) {
            setRefreshing(false);
            schedule();
          }
        }
      });
      return pending;
    };
    const resume = () => {
      if (document.hidden || navigator.onLine === false) clearTimer();
      else if (timing.current) void run();
    };
    const current = {
      run,
      reschedule: () => {
        clearTimer();
        if (!pending && timing.current && !document.hidden && navigator.onLine !== false) void run();
      },
      dispose: () => {
        disposed = true;
        clearTimer();
        controller?.abort();
      },
    };
    scope.current = current;
    document.addEventListener("visibilitychange", resume);
    window.addEventListener("online", resume);
    window.addEventListener("offline", resume);
    if (!document.hidden && navigator.onLine !== false) void run();
    return () => {
      current.dispose();
      if (scope.current === current) scope.current = null;
      document.removeEventListener("visibilitychange", resume);
      window.removeEventListener("online", resume);
      window.removeEventListener("offline", resume);
    };
  }, [key, enabled, revision]);
  return { refresh, refreshing, cancel, invalidate, hasSuccess: successfulKey === key };
}
