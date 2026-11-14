import type { PlatformBranding } from "@mindie-motor/ui-sdk";
export type { PlatformBranding } from "@mindie-motor/ui-sdk";
import { useSyncExternalStore } from "react";
import { PRODUCT_CATEGORY, PRODUCT_NAME } from "./brand";



export const DEFAULT_BRANDING: PlatformBranding = Object.freeze({
  name: PRODUCT_NAME, subtitle: PRODUCT_CATEGORY, icon_url: null, revision: 0,
});
const listeners = new Set<() => void>();
let current = DEFAULT_BRANDING;
let readGeneration = 0;
let authoritativeGeneration: number | null = null;
const invalidText = /[\p{Cc}\p{Cf}]/u;
export const captureBrandingReadGeneration = () => readGeneration;
/** Explicit operator reload starts a new read epoch; in-flight metadata belongs to the old one. */
export function beginBrandingReload() {
  authoritativeGeneration = ++readGeneration;
  return readGeneration;
}

/** Public display metadata never changes the product ID, native profile or authentication contract. */
export function parseBranding(value: unknown): PlatformBranding | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const data = value as Partial<PlatformBranding>;
  if (typeof data.name !== "string" || !data.name.trim() || Array.from(data.name).length > 40 || invalidText.test(data.name) ||
      typeof data.subtitle !== "string" || Array.from(data.subtitle).length > 80 || invalidText.test(data.subtitle) ||
      !Number.isSafeInteger(data.revision) || (data.revision as number) < 0 ||
      data.icon_url !== null && (typeof data.icon_url !== "string" || !/^\/api\/branding\/icons\/[0-9a-f]{64}\.png$/.test(data.icon_url))) return null;
  return { name: data.name, subtitle: data.subtitle, icon_url: data.icon_url, revision: data.revision as number,
    ...(typeof data.updated_at === "string" ? { updated_at: data.updated_at } : {}) };
}

export function publishBranding(value: unknown, generation = readGeneration): PlatformBranding | null {
  const next = parseBranding(value);
  if (!next || generation !== readGeneration) return null;
  const authoritative = authoritativeGeneration === generation;
  if (authoritative) authoritativeGeneration = null;
  if (!authoritative && next.revision < current.revision) return current;
  // A committed revision identifies one immutable configuration. Late metadata
  // reads cannot replace a just-saved value with a different equal revision.
  if (!authoritative && next.revision === current.revision) return current;
  current = next;
  for (const listener of listeners) listener();
  return current;
}

const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const useBranding = () => useSyncExternalStore(subscribe, () => current, () => DEFAULT_BRANDING);

export function applyDocumentBranding(branding: PlatformBranding) {
  document.title = branding.name;
  const description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
  if (description) description.content = branding.subtitle ? `${branding.name} · ${branding.subtitle}` : branding.name;
  let icon = document.querySelector<HTMLLinkElement>('link[data-platform-brand-icon]');
  if (!branding.icon_url) {
    // Removing a link alone can leave the last favicon in the browser's tab cache.
    if (icon) { const blank = document.createElement("canvas"); blank.width = 1; blank.height = 1; icon.href = blank.toDataURL("image/png"); }
    return;
  }
  if (!icon) {
    icon = document.createElement("link");
    icon.rel = "icon"; icon.type = "image/png"; icon.dataset.platformBrandIcon = "true";
    document.head.append(icon);
  }
  icon.href = branding.icon_url;
}
