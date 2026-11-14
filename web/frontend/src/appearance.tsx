import { useEffect, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { THEME_STORAGE_KEY, MODE_STORAGE_KEY, DENSITY_STORAGE_KEY, MOTION_STORAGE_KEY, SIDEBAR_WIDTH_STORAGE_KEY } from "./browserStateKeys";

export { THEME_STORAGE_KEY, MODE_STORAGE_KEY, DENSITY_STORAGE_KEY, MOTION_STORAGE_KEY, SIDEBAR_WIDTH_STORAGE_KEY } from "./browserStateKeys";
export const SIDEBAR_WIDTH = { min: 200, max: 280, step: 8, default: 240 } as const;
export const THEMES = [
  { id: "blue", name: "蓝灰", color: "var(--theme-blue-preview)" },
  { id: "red", name: "红灰", color: "var(--theme-red-preview)" },
  { id: "teal", name: "青灰", color: "var(--theme-teal-preview)" },
] as const;
export const COLOR_MODES = ["light", "dark", "system"] as const;
export const DENSITIES = ["comfortable", "compact"] as const;
export const MOTION_PREFERENCES = ["enhanced", "standard", "reduced"] as const;
export type ThemeName = (typeof THEMES)[number]["id"];
export type ColorMode = (typeof COLOR_MODES)[number];
export type Density = (typeof DENSITIES)[number];
export type MotionPreference = (typeof MOTION_PREFERENCES)[number];
type Appearance = {
  theme: ThemeName;
  mode: ColorMode;
  density: Density;
  resolvedMode: "light" | "dark";
  motion: MotionPreference;
  sidebarWidth: number;
  resolvedMotion: MotionPreference;
  preferencesPersistent: boolean;
};
type Preferences = Pick<Appearance, "theme" | "mode" | "density" | "motion" | "sidebarWidth">;
let current: Appearance = {
  theme: "blue",
  mode: "system",
  density: "comfortable",
  resolvedMode: "light",
  motion: "standard",
  sidebarWidth: SIDEBAR_WIDTH.default,
  resolvedMotion: "standard",
  preferencesPersistent: true,
};
let storageUsable = true;
const listeners = new Set<() => void>();
const validTheme = (value: unknown): value is ThemeName =>
  THEMES.some((theme) => theme.id === value);
const validMode = (value: unknown): value is ColorMode =>
  COLOR_MODES.some((mode) => mode === value);
const validDensity = (value: unknown): value is Density =>
  DENSITIES.some((density) => density === value);
const validMotion = (value: unknown): value is MotionPreference =>
  MOTION_PREFERENCES.some((motion) => motion === value);
const validSidebarWidth = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) &&
  value >= SIDEBAR_WIDTH.min && value <= SIDEBAR_WIDTH.max &&
  (value - SIDEBAR_WIDTH.min) % SIDEBAR_WIDTH.step === 0;
const prefersDark = () =>
  window.matchMedia("(prefers-color-scheme: dark)").matches;
const prefersReducedMotion = () =>
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function storedPreference<T extends string>(
  key: string,
  fallback: T,
  valid: (value: unknown) => value is T,
): T {
  try {
    const value = localStorage.getItem(key);
    if (valid(value)) return value;
    if (value !== null) localStorage.setItem(key, fallback);
  } catch {
    storageUsable = false;
    /* Storage restrictions must never block rendering or login. */
  }
  return fallback;
}

function storedPreferences(): Preferences {
  storageUsable = true;
  return {
    theme: storedPreference(THEME_STORAGE_KEY, "blue", validTheme),
    mode: storedPreference(MODE_STORAGE_KEY, "system", validMode),
    density: storedPreference(DENSITY_STORAGE_KEY, "comfortable", validDensity),
    motion: storedPreference(MOTION_STORAGE_KEY, "standard", validMotion),
    sidebarWidth: storedSidebarWidth(),
  };
}

function storedSidebarWidth(): number {
  try {
    const value = localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY);
    if (value === null) return SIDEBAR_WIDTH.default;
    const width = Number(value);
    if (validSidebarWidth(width) && String(width) === value) return width;
    localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(SIDEBAR_WIDTH.default));
  } catch {
    storageUsable = false;
  }
  return SIDEBAR_WIDTH.default;
}

function apply(preferences: Preferences) {
  const resolvedMode =
    preferences.mode === "system"
      ? prefersDark()
        ? "dark"
        : "light"
      : preferences.mode;
  const resolvedMotion = prefersReducedMotion() ? "reduced" : preferences.motion;
  const next: Appearance = { ...preferences, resolvedMode, resolvedMotion, preferencesPersistent: storageUsable };
  document.documentElement.dataset.theme = next.theme;
  document.documentElement.dataset.appearance = next.resolvedMode;
  document.documentElement.dataset.density = next.density;
  document.documentElement.dataset.motion = next.resolvedMotion;
  document.documentElement.dataset.motionPreference = next.motion;
  document.documentElement.style.colorScheme = next.resolvedMode;
  document.documentElement.style.setProperty("--sidebar-width", `${next.sidebarWidth}px`);
  if (
    current.theme !== next.theme ||
    current.mode !== next.mode ||
    current.density !== next.density ||
    current.resolvedMode !== next.resolvedMode ||
    current.motion !== next.motion ||
    current.sidebarWidth !== next.sidebarWidth ||
    current.resolvedMotion !== next.resolvedMotion ||
    current.preferencesPersistent !== next.preferencesPersistent
  ) {
    current = next;
    listeners.forEach((listener) => listener());
  }
}
export function initializeAppearance() {
  apply(storedPreferences());
}
export function setAppearance(preferences: Partial<Preferences>) {
  const theme =
    preferences.theme === undefined
      ? current.theme
      : validTheme(preferences.theme)
        ? preferences.theme
        : "blue";
  const mode =
    preferences.mode === undefined
      ? current.mode
      : validMode(preferences.mode)
        ? preferences.mode
        : "system";
  const density =
    preferences.density === undefined
      ? current.density
      : validDensity(preferences.density)
        ? preferences.density
        : "comfortable";
  const motion = preferences.motion === undefined
    ? current.motion
    : validMotion(preferences.motion) ? preferences.motion : "enhanced";
  const sidebarWidth = preferences.sidebarWidth === undefined
    ? current.sidebarWidth
    : validSidebarWidth(preferences.sidebarWidth) ? preferences.sidebarWidth : SIDEBAR_WIDTH.default;
  storageUsable = true;
  for (const [key, value] of [
    [THEME_STORAGE_KEY, theme],
    [MODE_STORAGE_KEY, mode],
    [DENSITY_STORAGE_KEY, density],
    [MOTION_STORAGE_KEY, motion],
    [SIDEBAR_WIDTH_STORAGE_KEY, String(sidebarWidth)],
  ]) {
    try {
      localStorage.setItem(key, value);
    } catch {
      storageUsable = false;
      /* Active preferences remain usable without persistent storage. */
    }
  }
  apply({ theme, mode, density, motion, sidebarWidth });
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
const snapshot = () => current;
export function useAppearance() {
  const appearance = useSyncExternalStore(subscribe, snapshot, snapshot);
  return {
    ...appearance,
    motionReduced: appearance.resolvedMotion === "reduced",
    setAppearance,
  };
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    initializeAppearance();
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const systemChanged = () => {
      apply({ theme: current.theme, mode: current.mode, density: current.density, motion: current.motion, sidebarWidth: current.sidebarWidth });
    };
    const storageChanged = (event: StorageEvent) => {
      if (
        event.key === null ||
        event.key === THEME_STORAGE_KEY ||
        event.key === MODE_STORAGE_KEY ||
        event.key === DENSITY_STORAGE_KEY ||
        event.key === MOTION_STORAGE_KEY ||
        event.key === SIDEBAR_WIDTH_STORAGE_KEY
      )
        initializeAppearance();
    };
    media.addEventListener("change", systemChanged);
    motion.addEventListener("change", systemChanged);
    window.addEventListener("storage", storageChanged);
    systemChanged();
    return () => {
      media.removeEventListener("change", systemChanged);
      motion.removeEventListener("change", systemChanged);
      window.removeEventListener("storage", storageChanged);
    };
  }, []);
  return <>{children}</>;
}
