/** Persisted browser identifiers are data contracts, independent of display branding. */
export const THEME_STORAGE_KEY = "qinghuan-platform-theme";
export const MODE_STORAGE_KEY = "qinghuan-platform-color-mode";
export const DENSITY_STORAGE_KEY = "qinghuan-platform-density";
export const MOTION_STORAGE_KEY = "qinghuan-platform-motion";
export const SIDEBAR_WIDTH_STORAGE_KEY = "qinghuan-platform-navigation-width";
export const NAVIGATION_COLLAPSED_STORAGE_KEY = "qinghuan-platform-spatial-navigation-collapsed";
export const REFRESH_STORAGE_KEY = "qinghuan-platform-refresh-ms";
export const SERVICE_JOB_INTENTS_STORAGE_KEY = "qinghuan-platform-service-job-intents-v1";
export const IMAGE_OPERATION_STORAGE_PREFIX = "qinghuan-platform-image-operation:";

/** Used only to discard an obsolete client-side token; never read or migrate it. */
export const LEGACY_SESSION_TOKEN_STORAGE_KEY = "qinghuan-platform-token";

export function imageOperationStorageKey(principal: string): string {
  return `${IMAGE_OPERATION_STORAGE_PREFIX}${principal}`;
}
