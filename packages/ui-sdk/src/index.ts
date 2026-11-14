/** Public contracts are defined here, independently of the Console implementation. */
import type * as React from "react";
import type { createPortal } from "react-dom";
import type { AuthStatus, Meta, NodeInfo, Service } from "./dto.js";
export * from "./dto.js";
export * from "./components.js";
import type { BuiltinUiContracts } from "./components.js";

export const UI_SDK_VERSION = "1.0.0";
export type UiComponentProps<P extends object = { children?: React.ReactNode }> = P & { Default: React.ComponentType<P> };
/** Values cross an untyped module boundary and are validated before resolution. */
export type UiProvider = unknown;
export type UiPageProps = { page: UiPageContribution };
export type UiPageComponent = React.ComponentType<UiPageProps>;
export type UiChangeEvent<T extends Element> = React.ChangeEvent<T>;
export type UiPageContribution = {
  id: string; slot: string; path: string; title: string;
  roles: readonly ("admin" | "operator" | "viewer")[]; section?: string; order?: number;
};
export type UiPackageManifest = {
  id: string; version: string; sdkVersion: string; buildDigest?: string;
  provides: { slot: string; version: string; kind?: "component" | "service"; methods?: string[] }[];
  requires?: { slot: string; range: string; optional?: boolean }[];
  contributes?: { pages?: UiPageContribution[] };
  entry?: { path: string; sha256: string }; assets?: { path: string; sha256: string }[];
};
export type UiBootstrap = { schemaVersion: 1; sdkVersion: string; generation: string;
  hostGeneration?: string; providerGeneration?: string; packages: UiPackageManifest[]; bindings: Record<string, string> };
export type UiSlotRef<T> = { readonly slot: string; readonly version: string; readonly contract?: (value: T) => T };
/** The phantom invariant contract prevents accidentally consuming another slot's type. */
export function defineSlot<T>(slot: string, version = "1.0.0"): UiSlotRef<T> {
  if (!/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(slot) || version.length > 64 || !/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/.test(version)) throw Error("Invalid UI slot reference");
  return Object.freeze({ slot, version });
}
export function provideSlot<T>(reference: UiSlotRef<T>, implementation: T): Readonly<Record<string, T>> {
  return Object.freeze({ [reference.slot]: implementation });
}
export type UiResolver = {
  <K extends keyof BuiltinUiContracts>(slot: K): BuiltinUiContracts[K];
  <T>(reference: UiSlotRef<T>): T;
  /** Compatibility escape hatch for pre-1.0 host modules; prefer a typed reference. */
  <T>(slot: string): T;
};
export type UiActivationContext = { generation: string; signal: AbortSignal; selectedSlots: readonly string[];
  onStop: (stop: () => void | Promise<void>) => void; consume: UiResolver;
  assetUrl: (member: string) => string; loadAsset: (member: string) => Promise<Uint8Array> };
export type UiPlugin = { id: string; version: string; buildDigest?: string;
  providers: Readonly<Record<string, unknown>>; activate?: (context: UiActivationContext) => void | Promise<void> };
/** Known contracts are checked even when a plugin also contributes independent slots. */
export function definePlugin<T extends UiPlugin>(plugin: T & { providers: Partial<BuiltinUiContracts> }): T { return plugin; }
export type UiBundledPlugin = { manifest: UiPackageManifest; load: () => Promise<UiPlugin> };
export type UiRuntime = { generation: string; bindings: Readonly<Record<string, string>>;
  pages: readonly UiPageContribution[]; resolve: UiResolver; stop: () => Promise<void> };
export type MutationIntent = { readonly key: string; submit<T>(path: string, body?: unknown): Promise<T> };
export type UiClient = {
  api<T>(path: string, options?: RequestInit, responseLimitBytes?: number): Promise<T>;
  post<T>(path: string, body?: unknown, operationKey?: string): Promise<T>;
  download(data: unknown, name: string): void;
  readSession(signal?: AbortSignal): Promise<{ meta: Meta; auth: AuthStatus }>;
  setCsrfToken(value: string | null | undefined): void;
  parseStrictJson(text: string): unknown;
  createMutationIntent(prefix?: string, existingKey?: string): MutationIntent;
  createOperationKey(prefix?: string): string;
};
export type UiHostServices = Pick<UiClient, "api" | "post" | "download"> & {
  auth: AuthStatus | null; meta: Meta | null; services: Service[]; nodes: NodeInfo[];
  page: string; navigate(page: string): void; notify(message: string): void;
  workflows: { createService?: () => void; editService?: (service: Service) => void;
    openService?: (service: Service) => void; serviceAction?: (service: Service, action: string) => void };
};
export type UiActionInvocation = { id: string; event?: React.SyntheticEvent<HTMLElement>;
  execute(): unknown; disabled: boolean; context: Record<string, unknown> };
export type UiActions = { invoke(action: UiActionInvocation): unknown;
  register?: (id: string, handler: (action: UiActionInvocation) => unknown) => () => void };
export type UiI18n = { locale: string; translate(key: string, source: string, values?: Record<string, unknown>): string };
export type UiElements = { render<T extends keyof React.JSX.IntrinsicElements>(tag: T, props: React.JSX.IntrinsicElements[T]): React.ReactNode };
export type UiButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { actionId?: string; actionContext?: Record<string, unknown> };
export type UiRegionProps = React.HTMLAttributes<HTMLElement> & React.RefAttributes<HTMLElement> & { name: string; as?: keyof React.JSX.IntrinsicElements };
export type UiRegionRenderProps = Omit<UiRegionProps, "name">;
export type UiSlotProps<P extends object = { children?: React.ReactNode }> = P & { name: string; Default?: React.ComponentType<P> };
export type MotorConsoleUiSdk = {
  readonly sdkVersion: "1.0.0"; readonly React: typeof React; readonly createElement: typeof React.createElement;
  readonly createPortal: typeof createPortal; readonly client: UiClient;
  readonly UI_ACTIONS: Readonly<Record<string, { description: string; context: readonly string[] }>>;
  definePlugin: typeof definePlugin; defineSlot: typeof defineSlot; provideSlot: typeof provideSlot;
  useHost(): UiHostServices; UiHostProvider: React.Provider<UiHostServices>;
  notify(message: string): void; useUiContract: UiResolver; useSlot: UiResolver;
  useAction(id: string): (execute: () => unknown, context?: Record<string, unknown>) => unknown;
  useTranslation(): (source: string, key?: string, values?: Record<string, unknown>) => string;
  useUnsavedChanges(dirty: boolean): void; hasUnsavedChanges(): boolean;
  UiText: React.ComponentType<{ children: string }>;
  UiElement: <T extends keyof React.JSX.IntrinsicElements>(props: { as: T } & React.JSX.IntrinsicElements[T]) => React.ReactNode;
  UiSlot: <P extends object>(props: UiSlotProps<P>) => React.ReactNode;
  UiRegion: React.ComponentType<UiRegionProps>;
  UiButton: React.ForwardRefExoticComponent<UiButtonProps & React.RefAttributes<HTMLButtonElement>>;
  withUiSlot<P extends object>(slot: string | UiSlotRef<React.ComponentType<UiComponentProps<P>>>, Default: React.ComponentType<P>): React.ComponentType<P>;
};
declare global { var MotorConsoleUiSdk: MotorConsoleUiSdk; }
export function getUiSdk(): MotorConsoleUiSdk {
  const sdk = globalThis.MotorConsoleUiSdk;
  if (!sdk || sdk.sdkVersion !== UI_SDK_VERSION) throw Error("Compatible Motor Console UI SDK is not available");
  return sdk;
}
