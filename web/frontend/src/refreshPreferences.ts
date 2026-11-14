import { useSyncExternalStore } from "react";

const KEY = "qinghuan-platform-refresh-ms";
export const REFRESH_OPTIONS = [
  [0, "手动刷新"], [10_000, "10 秒"], [30_000, "30 秒"],
  [60_000, "1 分钟"], [120_000, "2 分钟"], [300_000, "5 分钟"],
] as const;
const listeners = new Set<() => void>();
let value = 30_000;
function read() {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved !== null && REFRESH_OPTIONS.some(([interval]) => interval === Number(saved))) value = Number(saved);
  } catch { /* Restricted storage does not prevent manual or automatic refresh. */ }
  return value;
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  const changed = (event: StorageEvent) => {
    if (event.key === KEY || event.key === null) { read(); listener(); }
  };
  window.addEventListener("storage", changed);
  return () => { listeners.delete(listener); window.removeEventListener("storage", changed); };
}
export function useRefreshPreference() {
  const interval = useSyncExternalStore(subscribe, read);
  return [interval, (next: number) => {
    if (!REFRESH_OPTIONS.some(([candidate]) => candidate === next)) return;
    value = next;
    try { localStorage.setItem(KEY, String(next)); } catch { /* This session remains usable without persistence. */ }
    listeners.forEach((listener) => listener());
  }] as const;
}
