import type { UiComponentProps, UiPackageManifest, UiBootstrap, UiPlugin, UiBundledPlugin, UiHostServices, UiRuntime, UiI18n, UiElements, UiActions, UiButtonProps, UiResolver, UiSlotRef, UiSlotProps, UiRegionProps, UiRegionRenderProps, UiNotificationsProps, UiPageProps, MotorConsoleUiSdk } from '@mindie-motor/ui-sdk';
import { definePlugin, defineSlot, provideSlot } from '@mindie-motor/ui-sdk';
export * from '@mindie-motor/ui-sdk';
/** Public browser SDK. Trusted packages share this React instance and these contracts. */
import * as React from "react";
import { createPortal } from "react-dom";
import { api, post, download, setCsrfToken, parseStrictJson, createMutationIntent, createOperationKey, freezeUiGeneration, UI_GENERATION_CHANGED_EVENT } from "./api";
import { hasUnsavedChanges, useUnsavedChanges } from "./unsavedChanges";
import type { AuthStatus, Meta } from "./api";
import actionDefinitions from "./uiActionDefinitions.json";

import { UI_SDK_VERSION } from "@mindie-motor/ui-sdk";
export const UI_ACTIONS = Object.freeze(actionDefinitions);

const HostContext = React.createContext<UiHostServices>({ api, post, download, auth: null, meta: null,
  services: [], nodes: [], page: "", navigate: page => { window.location.hash = page; },
  notify: message => { window.dispatchEvent(new CustomEvent("motor-console:ui-notification", { detail: message })); }, workflows: {} });
export const UiHostProvider = HostContext.Provider;
export function useHost() { return React.useContext(HostContext); }
export function notify(message: string) { window.dispatchEvent(new CustomEvent("motor-console:ui-notification", { detail: message })); }
export function UiNotificationOutlet() {
  const [messages, setMessages] = React.useState<{ id: number; text: string }[]>([]);
  const sequence = React.useRef(0);
  const Provider = useUiContract<React.ComponentType<UiNotificationsProps>>("ui.notifications");
  React.useEffect(() => {
    const received = (event: Event) => {
      const text = (event as CustomEvent).detail;
      if (typeof text === "string" && text) setMessages(previous => [...previous.slice(-4), { id: ++sequence.current, text }]);
    };
    window.addEventListener("motor-console:ui-notification", received);
    return () => window.removeEventListener("motor-console:ui-notification", received);
  }, []);
  return <Provider messages={messages} dismiss={(id: number) => setMessages(previous => previous.filter(message => message.id !== id))} />;
}
export async function readSession(signal?: AbortSignal) {
  const meta = await api<Meta>("/meta", { signal });
  const auth = await api<AuthStatus>("/auth/status", { signal });
  setCsrfToken(auth.csrf_token);
  return { meta, auth };
}

const RuntimeContext = React.createContext<UiRuntime | null>(null);
export const UiRuntimeProvider = RuntimeContext.Provider;
export function useUiRuntime(): UiRuntime {
  const runtime = React.useContext(RuntimeContext);
  if (!runtime) throw Error("UI plugin runtime is unavailable. Check the selected composition.");
  return runtime;
}
export const useUiContract: UiResolver = <T,>(slot: string | UiSlotRef<T>): T => {
  const runtime = useUiRuntime();
  return typeof slot === "string" ? runtime.resolve<T>(slot) : runtime.resolve(slot);
};

export function useTranslation() {
  const language = useUiContract<UiI18n>("ui.i18n");
  return (source: string, key = source, values?: Record<string, unknown>) => language.translate(key, source, values);
}
export function UiText({ children }: { children: string }) { return <>{useTranslation()(children)}</>; }

function translateChildren(children: React.ReactNode, translate: (source: string) => string): React.ReactNode {
  if (typeof children === "string") return translate(children);
  return Array.isArray(children) ? children.map(child => translateChildren(child, translate)) : children;
}
/** Native controls retain their DOM contracts; the selected design system owns their rendering. */
export function UiElement<T extends keyof React.JSX.IntrinsicElements>({ as, ...props }: { as: T } & React.JSX.IntrinsicElements[T]) {
  const elements = useUiContract<UiElements>("ui.element"), translate = useTranslation();
  const actions = useUiContract<UiActions>("ui.actions"), host = useHost();
  const translated = { ...props } as Record<string, unknown>;
  for (const key of ["title", "placeholder", "aria-label", "alt"])
    if (typeof translated[key] === "string") translated[key] = translate(translated[key]);
  if (translated.children !== undefined && as !== "textarea" && as !== "pre" && as !== "code")
    translated.children = translateChildren(translated.children as React.ReactNode, translate);
  if (as === "form" && typeof translated.onSubmit === "function") {
    const submit = translated.onSubmit as React.FormEventHandler<HTMLFormElement>;
    translated.onSubmit = (event: React.FormEvent<HTMLFormElement>) => {
      let invoked = false;
      try {
        const result = actions.invoke({ id: typeof translated["data-action-id"] === "string" ? translated["data-action-id"] : "form.submit", event, disabled: false,
          context: { page: host.page, principal: { id: host.auth?.user?.id, role: host.auth?.user?.role } },
          execute: () => { if (invoked) throw Error("A form action can only be invoked once"); invoked = true; return submit(event); } });
        if (!invoked || result === false) event.preventDefault();
        if (result && typeof (result as Promise<unknown>).then === "function")
          void Promise.resolve(result).catch(error => host.notify(String(error)));
      } catch (error) { event.preventDefault(); host.notify(String(error)); }
    };
  }
  return elements.render(as, translated as React.JSX.IntrinsicElements[T]);
}
export function UiRegion({ name, as = "div", children, ...props }: UiRegionProps) {
  const Renderer = useUiContract<React.ComponentType<UiComponentProps<UiRegionRenderProps>>>(name);
  const Default = React.useMemo(() => function NativeRegion({ as: _as, ...nativeProps }: UiRegionRenderProps) { return React.createElement(as, nativeProps); }, [as]);
  return <Renderer {...props} as={as} Default={Default}>{children}</Renderer>;
}
export function withUiSlot<P extends object>(slot: string | UiSlotRef<React.ComponentType<UiComponentProps<P>>>, Default: React.ComponentType<P>): React.ComponentType<P> {
  function Selected(props: P) {
    const runtime = useUiRuntime();
    const Provider = typeof slot === "string" ? runtime.resolve<React.ComponentType<UiComponentProps<P>>>(slot) : runtime.resolve(slot);
    const translate = useTranslation();
    const translated = { ...props } as Record<string, unknown>;
    for (const key of ["label", "title", "description", "placeholder", "aria-label", "hint", "subtitle"])
      if (typeof translated[key] === "string") translated[key] = translate(translated[key] as string);
    return <Provider {...translated as P} Default={Default} />;
  }
  Selected.displayName = `UiSlot(${slot})`;
  return Selected;
}
const NativeContent = ({ children }: { children?: React.ReactNode }) => <>{children}</>;
export function UiSlot<P extends object>({ name, children, Default = NativeContent as React.ComponentType<P>, ...props }: UiSlotProps<P> & { children?: React.ReactNode }) {
  const Provider = useUiContract<React.ComponentType<UiComponentProps<P>>>(name);
  return <Provider {...props as P} Default={Default}>{children}</Provider>;
}

export function useAction(id: string) {
  const actions = useUiContract<UiActions>("ui.actions");
  return (execute: () => unknown, context: Record<string, unknown> = {}) =>
    actions.invoke({ id, execute, disabled: false, context });
}

/** All native buttons, including icon/menu/tab buttons, use the selected renderer and action policy. */
export const UiButton = React.forwardRef<HTMLButtonElement, UiButtonProps>(function UiButton(
  { actionId, actionContext, onClick, ...props }, ref) {
  const Renderer = useUiContract<React.ComponentType<UiButtonProps & React.RefAttributes<HTMLButtonElement>>>("ui.button");
  const actions = useUiContract<UiActions>("ui.actions");
  const translate = useTranslation();
  const host = useHost();
  const id = actionId ?? String(props["data-action" as keyof typeof props] ?? "ui.activate");
  return <Renderer {...props} title={props.title && translate(props.title)}
    aria-label={props["aria-label"] && translate(props["aria-label"]!)} ref={ref} actionId={id} onClick={(event: React.MouseEvent<HTMLButtonElement>) => {
    if (props.disabled) { event.preventDefault(); return; }
    // A policy may suppress activation; prevent native submit until it explicitly invokes execute.
    const wasPrevented = event.defaultPrevented;
    let invoked = false;
    try {
    const result = actions.invoke({ id, event, disabled: !!props.disabled,
      context: { label: props["aria-label"] ?? props.title, page: host.page,
        principal: { id: host.auth?.user?.id, role: host.auth?.user?.role }, ...actionContext }, execute: () => {
        if (invoked) throw Error(`UI action can only be invoked once: ${id}`);
        invoked = true; return onClick?.(event);
      } });
    if ((!invoked || result === false) && !wasPrevented) event.preventDefault();
    if (result && typeof (result as Promise<unknown>).then === "function")
      void Promise.resolve(result).catch(error => host.notify(`UI action failed: ${error instanceof Error ? error.message : String(error)}`));
    } catch (error) { event.preventDefault(); host.notify(`UI action failed: ${error instanceof Error ? error.message : String(error)}`); }
  }} />;
});

const VERSION = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;
const ID = /^[a-z][a-z0-9_.-]{0,95}$/;
const SLOT = /^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const MEMBER = /^[a-zA-Z0-9_-][a-zA-Z0-9_.-]*(?:\/[a-zA-Z0-9_-][a-zA-Z0-9_.-]*)*$/;
function compatible(actual: string, range: string): boolean {
  if (actual.length > 64 || !VERSION.test(actual) || range.length > 65) return false;
  if (range === actual) return true;
  if (!["^", "~"].includes(range[0]) || !VERSION.test(range.slice(1))) return false;
  const a = actual.split(".").map(value => BigInt(value)), b = range.slice(1).split(".").map(value => BigInt(value));
  if (a[0] !== b[0] || a[1] < b[1] || a[1] === b[1] && a[2] < b[2]) return false;
  if (range[0] === "~" || b[0] === 0n && b[1] > 0n) return a[1] === b[1];
  return b[0] !== 0n || a[1] === b[1] && a[2] === b[2];
}
function validateContributions(manifest: UiPackageManifest): void {
  if (manifest.contributes === undefined) return;
  const contributes = manifest.contributes;
  if (!contributes || typeof contributes !== "object" || Array.isArray(contributes)
      || Object.keys(contributes).some(key => key !== "pages"))
    throw Error(`Invalid UI contributions: ${manifest.id}`);
  if (contributes.pages === undefined) return;
  if (!Array.isArray(contributes.pages) || contributes.pages.length > 64) throw Error(`Invalid UI page contributions: ${manifest.id}`);
  const ids = new Set<string>(), paths = new Set<string>();
  const reference = manifest.id === "console.ui.default";
  const corePaths = ["overview", "services", "templates", "nodes", "images", "gateways", "logs", "features", "audit", "systemlogs", "accounts", "maintenance", "settings"];
  for (const page of contributes.pages) {
    if (!page || typeof page !== "object" || Object.keys(page).some(key => !["id", "slot", "path", "title", "roles", "section", "order"].includes(key))
        || !SLOT.test(page.id) || !page.id.startsWith(manifest.id + ".") || ids.has(page.id)
        || typeof page.slot !== "string" || !manifest.provides.some(item => item.slot === page.slot && item.kind !== "service" && item.version === UI_SDK_VERSION)
        || !reference && !page.slot.startsWith(manifest.id + ".") || typeof page.path !== "string" || paths.has(page.path)
        || (reference ? !corePaths.includes(page.path) : !page.path.startsWith(`extensions/${manifest.id}/`)
          || !/^[a-z][a-z0-9-]{0,47}$/.test(page.path.slice(`extensions/${manifest.id}/`.length)))
        || typeof page.title !== "string" || !page.title.trim() || page.title.length > 100 || /[\x00-\x1f]/.test(page.title)
        || page.section !== undefined && (typeof page.section !== "string" || !page.section.trim() || page.section.length > 60 || /[\x00-\x1f]/.test(page.section))
        || page.order !== undefined && (!Number.isInteger(page.order) || page.order < -10000 || page.order > 10000)
        || !Array.isArray(page.roles) || !page.roles.length || page.roles.length > 3
        || page.roles.some(role => !["admin", "operator", "viewer"].includes(role)) || new Set(page.roles).size !== page.roles.length)
      throw Error(`Invalid, conflicting or unnamespaced UI page: ${manifest.id}`);
    ids.add(page.id); paths.add(page.path);
  }
}
export function validateUiBootstrap(input: unknown, requiredSlots: readonly string[]): UiBootstrap {
  if (!input || typeof input !== "object") throw Error("Invalid UI bootstrap manifest");
  const graph = input as UiBootstrap;
  if (graph.schemaVersion !== 1 || graph.sdkVersion !== UI_SDK_VERSION || typeof graph.generation !== "string"
    || !graph.generation || graph.generation.length > 256 || !Array.isArray(graph.packages) || graph.packages.length > 128
    || !graph.bindings || typeof graph.bindings !== "object" || Array.isArray(graph.bindings)) throw Error("Unsupported UI graph, SDK version or generation");
  const packages = new Map<string, UiPackageManifest>();
  for (const key of ["hostGeneration", "providerGeneration"] as const)
    if (graph[key] !== undefined && !DIGEST.test(graph[key]!)) throw Error(`Invalid UI ${key}`);
  for (const manifest of graph.packages) {
    if (!manifest || !ID.test(manifest.id) || packages.has(manifest.id) || !VERSION.test(manifest.version) || manifest.version.length > 64
      || manifest.sdkVersion !== UI_SDK_VERSION || !Array.isArray(manifest.provides) || manifest.provides.length > 512
      || (manifest.requires !== undefined && (!Array.isArray(manifest.requires) || manifest.requires.length > 512)))
      throw Error(`Invalid or duplicate UI package: ${manifest?.id}`);
    const slots = new Set<string>();
    for (const contribution of manifest.provides) {
      if (!contribution || !SLOT.test(contribution.slot) || !VERSION.test(contribution.version) || contribution.version.length > 64 || slots.has(contribution.slot))
        throw Error(`Invalid or duplicate provider in ${manifest.id}`);
      slots.add(contribution.slot);
      if (contribution.kind !== undefined && !["component", "service"].includes(contribution.kind)) throw Error(`Invalid UI contract kind: ${manifest.id}`);
      if (contribution.kind === "service") {
        const methods = contribution.methods;
        if (!contribution.slot.startsWith(manifest.id + ".") || !Array.isArray(methods) || !methods.length || methods.length > 64
            || methods.some(method => typeof method !== "string" || !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(method) || ["constructor", "prototype", "__proto__"].includes(method))
            || new Set(methods).size !== methods.length) throw Error(`Invalid UI service method contract: ${manifest.id}`);
      } else if (contribution.methods !== undefined) throw Error(`Component UI contract declares service methods: ${manifest.id}`);
    }
    validateContributions(manifest);
    if (manifest.entry && (typeof manifest.entry.path !== "string"
      || !/^\/api\/ui-plugins\/assets\/[a-f0-9]{64}\/[a-zA-Z0-9_.-]+\.(?:m?js)$/.test(manifest.entry.path)
      || !DIGEST.test(manifest.entry.sha256))) throw Error(`Untrusted UI artifact path/digest: ${manifest.id}`);
    if (manifest.assets !== undefined) {
      if (!Array.isArray(manifest.assets) || manifest.assets.length > 64) throw Error(`Invalid UI assets: ${manifest.id}`);
      const names = new Set<string>();
      for (const asset of manifest.assets) {
        if (!asset || !MEMBER.test(asset.path) || !DIGEST.test(asset.sha256) || names.has(asset.path)) throw Error(`Invalid or duplicate UI asset: ${manifest.id}`);
        names.add(asset.path);
      }
    }
    packages.set(manifest.id, manifest);
  }
  for (const slot of requiredSlots) if (!Object.hasOwn(graph.bindings, slot)) throw Error(`Required UI slot is unbound: ${slot}`);
  for (const [slot, selected] of Object.entries(graph.bindings)) {
    if (!SLOT.test(slot) || typeof selected !== "string" || !packages.get(selected)?.provides.some(p => p.slot === slot
        && (!requiredSlots.includes(slot) || compatible(p.version, "^1.0.0"))))
      throw Error(`Missing, incompatible or ambiguous UI provider: ${slot} -> ${String(selected)}`);
  }
  const selected = new Set(Object.values(graph.bindings));
  const pageIds = new Set<string>(), pageRoutes = new Set<string>();
  for (const manifest of graph.packages) for (const page of manifest.contributes?.pages ?? []) {
    if (manifest.id !== "console.ui.default" && graph.bindings[page.slot] !== manifest.id) continue;
    if (pageIds.has(page.id) || pageRoutes.has(page.path)) throw Error(`Selected UI page identity or route conflicts: ${page.id}`);
    pageIds.add(page.id); pageRoutes.add(page.path);
  }
  const visited = new Set<string>(), visiting = new Set<string>();
  function visit(id: string) {
    if (visiting.has(id)) throw Error(`UI dependency cycle: ${[...visiting, id].join(" -> ")}`);
    if (visited.has(id)) return;
    visiting.add(id);
    const required = new Set<string>();
    for (const dependency of packages.get(id)!.requires ?? []) {
      if (!dependency || !SLOT.test(dependency.slot) || typeof dependency.range !== "string" || required.has(dependency.slot)
        || dependency.optional !== undefined && typeof dependency.optional !== "boolean") throw Error(`Invalid or duplicate dependency in ${id}`);
      required.add(dependency.slot);
      const target = Object.hasOwn(graph.bindings, dependency.slot) ? graph.bindings[dependency.slot] : undefined;
      const provided = target ? packages.get(target)?.provides.find(p => p.slot === dependency.slot) : undefined;
      if (!provided) { if (dependency.optional) continue; throw Error(`Missing UI dependency: ${id} requires ${dependency.slot}`); }
      if (!compatible(provided.version, dependency.range)) throw Error(`UI dependency version mismatch: ${id} requires ${dependency.slot}@${dependency.range}`);
      if (target && target !== id) visit(target);
    }
    visiting.delete(id); visited.add(id);
  }
  selected.forEach(visit);
  return graph;
}

async function loadArtifact(manifest: UiPackageManifest, signal: AbortSignal): Promise<UiPlugin> {
  if (!manifest.entry) throw Error(`Selected UI package has no prepared artifact: ${manifest.id}`);
  if (!globalThis.crypto?.subtle) throw Error("UI artifact verification requires HTTPS or localhost. Open the console through HTTPS before loading imported UI packages.");
  const response = await fetch(manifest.entry.path, { credentials: "same-origin", cache: "no-store", redirect: "error", signal });
  if (!response.ok) throw Error(`UI artifact could not be read: ${manifest.id} (${response.status})`);
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > 8 * 1024 * 1024) throw Error(`UI entry exceeds 8 MiB: ${manifest.id}`);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
  if (digest !== manifest.entry.sha256) throw Error(`UI artifact digest mismatch: ${manifest.id}`);
  const url = URL.createObjectURL(new Blob([bytes], { type: "text/javascript" }));
  try { return (await import(/* @vite-ignore */ url)).default; }
  finally { URL.revokeObjectURL(url); }
}

export async function startUiRuntime(graph: UiBootstrap, builtins: readonly (UiPlugin | UiBundledPlugin)[], requiredSlots: readonly string[]): Promise<UiRuntime> {
  validateUiBootstrap(graph, requiredSlots);
  const selected = new Set(Object.values(graph.bindings));
  const loaded = new Map<string, UiPlugin>();
  const abort = new AbortController(), cleanups: (() => void | Promise<void>)[] = [];
  let stopPromise: Promise<void> | undefined;
  const resolve: UiResolver = <T,>(reference: string | UiSlotRef<T>): T => {
    const slot = typeof reference === "string" ? reference : reference.slot;
    const selectedId = Object.hasOwn(graph.bindings, slot) ? graph.bindings[slot] : undefined;
    if (typeof reference !== "string") {
      const version = graph.packages.find(manifest => manifest.id === selectedId)?.provides.find(item => item.slot === slot)?.version;
      if (!version || !compatible(version, reference.version)) throw Error(`UI slot reference version mismatch: ${slot}@${reference.version}`);
    }
    const providers = selectedId ? loaded.get(selectedId)?.providers : undefined;
    const provider = providers && Object.hasOwn(providers, slot) ? providers[slot] : undefined;
    if (!provider) throw Error(`Selected UI provider unavailable: ${slot} (${selectedId ?? "unbound"})`);
    return provider as T;
  };
  async function bounded<T>(work: Promise<T>, milliseconds: number, label: string): Promise<T> {
    let timer: ReturnType<typeof setTimeout>;
    try { return await Promise.race([work, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(Error(`${label} timed out`)), milliseconds);
    })]); } finally { clearTimeout(timer!); }
  }
  const stop = (): Promise<void> => {
    if (stopPromise) return stopPromise;
    const deadline = performance.now() + 3000;
    // Publish the shared completion before abort listeners can reenter stop().
    stopPromise = Promise.resolve().then(async () => {
      const errors: string[] = [];
      for (const cleanup of cleanups.splice(0).reverse()) {
        try {
          const pending = Promise.resolve().then(cleanup);
          const remaining = deadline - performance.now();
          if (remaining <= 0) {
            void pending.catch(() => undefined);
            throw Error("UI cleanup completion is unknown after the total shutdown budget expired");
          }
          await bounded(pending, remaining, "UI cleanup");
        } catch (error) { errors.push(String(error)); }
      }
      if (errors.length) console.error("UI lifecycle cleanup failed", errors);
    });
    abort.abort();
    return stopPromise;
  };
  try {
    async function load(id: string): Promise<void> {
      if (loaded.has(id)) return;
      const manifest = graph.packages.find(p => p.id === id)!;
      for (const dependency of manifest.requires ?? []) {
        const target = Object.hasOwn(graph.bindings, dependency.slot) ? graph.bindings[dependency.slot] : undefined;
        if (target && target !== id) await load(target);
      }
      const matching = builtins.filter(candidate => {
        const identity = "manifest" in candidate ? candidate.manifest : candidate;
        return identity.id === id && identity.version === manifest.version;
      });
      if (matching.length > 1) throw Error(`Duplicate compiled UI package: ${id}@${manifest.version}`);
      const bundled = matching[0];
      if (bundled && "manifest" in bundled && bundled.manifest.buildDigest !== undefined && bundled.manifest.buildDigest !== manifest.buildDigest)
        throw Error(`Compiled UI package does not match the server build: ${id}. Save drafts and reload the matching release.`);
      const plugin = await bounded(Promise.resolve(bundled ? "load" in bundled ? bundled.load() : bundled : loadArtifact(manifest, abort.signal)),
        15000, `UI package loading ${id}`);
      abort.signal.throwIfAborted();
      if (!plugin || plugin.id !== id || plugin.version !== manifest.version || !plugin.providers || typeof plugin.providers !== "object")
        throw Error(`UI module identity does not match its pinned manifest: ${id}`);
      if (plugin.buildDigest !== undefined && plugin.buildDigest !== manifest.buildDigest)
        throw Error(`Compiled UI package does not match the server build: ${id}. Save drafts and reload the matching release.`);
      const declared = new Set(manifest.provides.map(p => p.slot));
      for (const [slot, provider] of Object.entries(plugin.providers)) {
        if (!declared.has(slot) || !provider || !["function", "object"].includes(typeof provider)) throw Error(`Undeclared or invalid UI provider: ${id}/${slot}`);
      }
      for (const slot of declared) if (!Object.hasOwn(plugin.providers, slot) || !plugin.providers[slot]) throw Error(`Declared UI provider missing: ${id}/${slot}`);
      const methods: Record<string, string> = { "ui.actions": "invoke", "ui.icons": "render", "ui.i18n": "translate", "ui.element": "render" };
      for (const [slot, provider] of Object.entries(plugin.providers)) {
        const custom = manifest.provides.find(item => item.slot === slot);
        if (Object.hasOwn(methods, slot) || custom?.kind === "service") {
          for (const method of custom?.kind === "service" ? custom.methods! : [methods[slot]])
            if (typeof (provider as Record<string, unknown>)[method] !== "function") throw Error(`Invalid UI contract implementation: ${id}/${slot}/${method}`);
          if (slot === "ui.i18n") {
            const locale = (provider as UiI18n).locale;
            if (typeof locale !== "string" || locale.length > 64 || Intl.getCanonicalLocales(locale).length !== 1)
              throw Error(`Invalid UI language locale: ${id}`);
          }
        } else if (typeof provider !== "function" && !(provider as Record<string, unknown>).$$typeof) {
          throw Error(`UI component provider must be a React component: ${id}/${slot}`);
        }
      }
      loaded.set(id, plugin);
      const assetUrl = (member: string) => {
        if (!MEMBER.test(member) || !manifest.entry) throw Error(`UI asset is not in a pinned package: ${id}/${member}`);
        return manifest.entry.path.slice(0, manifest.entry.path.lastIndexOf("/") + 1) + member;
      };
      const loadAsset = async (member: string) => {
        const declared = manifest.assets?.find(asset => asset.path === member);
        if (!declared) throw Error(`UI asset has no declared digest: ${id}/${member}`);
        if (!crypto.subtle) throw Error("UI asset verification requires HTTPS or localhost");
        const response = await fetch(assetUrl(member), { credentials: "same-origin", redirect: "error", signal: abort.signal });
        if (!response.ok) throw Error(`UI asset could not be read: ${id}/${member}`);
        const bytes = await response.arrayBuffer();
        if (bytes.byteLength > 8 * 1024 * 1024) throw Error(`UI asset exceeds 8 MiB: ${id}/${member}`);
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), value => value.toString(16).padStart(2, "0")).join("");
        if (digest !== declared.sha256) throw Error(`UI asset digest mismatch: ${id}/${member}`);
        return new Uint8Array(bytes);
      };
      const dependencies = new Set(manifest.requires?.map(dependency => dependency.slot));
      const consume: UiResolver = <T,>(reference: string | UiSlotRef<T>): T => {
        abort.signal.throwIfAborted();
        const slot = typeof reference === "string" ? reference : reference.slot;
        if (!dependencies.has(slot)) throw Error(`Undeclared UI dependency: ${id} attempted to consume ${slot}`);
        return typeof reference === "string" ? resolve<T>(reference) : resolve(reference);
      };
      await bounded(Promise.resolve(plugin.activate?.({ generation: graph.generation, signal: abort.signal,
        selectedSlots: Object.freeze(Object.entries(graph.bindings).filter(([, provider]) => provider === id).map(([slot]) => slot)),
        onStop: fn => {
          if (stopPromise) throw Error("UI generation is stopping; cleanup registration is closed");
          cleanups.push(fn);
        }, consume, assetUrl, loadAsset })), 15000, `UI activation ${id}`);
      abort.signal.throwIfAborted();
    }
    for (const id of selected) await load(id);
    return { generation: graph.generation, bindings: Object.freeze({ ...graph.bindings }), pages: Object.freeze(graph.packages.flatMap(manifest => (manifest.contributes?.pages ?? []).filter(page => manifest.id === "console.ui.default" || graph.bindings[page.slot] === manifest.id))), resolve, stop };
  } catch (error) { await stop(); throw error; }
}

/** Extension routes are never core authentication, service or management routes. */
export function isUiPagePath(path: string): boolean { return /^extensions\/[a-z][a-z0-9_.-]{0,95}\/[a-z][a-z0-9-]{0,47}$/.test(path); }
export function UiContributedPage({ page }: UiPageProps) {
  const host = useHost();
  const allowed = host.auth?.authenticated && page.roles.includes(host.meta?.mode === "demo" ? "admin" : host.auth.user?.role ?? "viewer");
  const runtime = useUiRuntime();
  if (!allowed) return <p role="alert">当前账号没有访问此页面的权限。</p>;
  const Page = runtime.resolve<React.ComponentType<UiPageProps>>(page.slot);
  return <Page page={page} />;
}

export class UiFailureBoundary extends React.Component<{ children: React.ReactNode; onFailure?: () => Promise<void> | void }, { error: string }> {
  state = { error: "" };
  static getDerivedStateFromError(error: Error) { return { error: error.message }; }
  componentDidCatch(error: Error) {
    freezeUiGeneration(`UI 插件渲染失败：${error.message}。请保留草稿并检查所选提供者。`);
    void Promise.resolve(this.props.onFailure?.()).catch(cause => console.error("UI failure cleanup failed", cause));
  }
  render() { return this.state.error ? <UiBootDiagnostic message={this.state.error} /> : this.props.children; }
}
export function UiBootDiagnostic({ message }: { message: string }) {
  return <main className="ui-boot-diagnostic"><section role="alert">
    <h1>UI 插件无法启动</h1><p>{message}</p><p>请由管理员核对 UI 装配、版本与制品摘要，修正后重新加载。</p>
    <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>重新加载</button>
  </section></main>;
}
export function UiGenerationGuard({ generation }: { generation: string }) {
  const [changed, setChanged] = React.useState(false), [error, setError] = React.useState("");
  React.useEffect(() => {
    const controller = new AbortController();
    const check = async () => { try {
      const response = await fetch("/api/ui-plugins/bootstrap", { cache: "no-store", credentials: "same-origin", signal: controller.signal });
      if (!response.ok) throw Error(`UI composition check failed (${response.status})`);
      const next = await response.json();
      if (next.generation !== generation) { setChanged(true); freezeUiGeneration(); }
      setError("");
    } catch (cause) { if (!controller.signal.aborted) setError(String(cause)); } };
    const timer = window.setInterval(() => { if (!document.hidden) void check(); }, 30000);
    const visible = () => { if (!document.hidden) void check(); };
    const frozen = () => setChanged(true);
    document.addEventListener("visibilitychange", visible);
    window.addEventListener(UI_GENERATION_CHANGED_EVENT, frozen);
    return () => { controller.abort(); clearInterval(timer); document.removeEventListener("visibilitychange", visible); window.removeEventListener(UI_GENERATION_CHANGED_EVENT, frozen); };
  }, [generation]);
  if (!changed && !error) return null;
  return <aside className="ui-generation-notice" role="alert">{changed
    ? `UI 插件配置已有更新。当前页面继续使用原代次；${hasUnsavedChanges() ? "请先保存未提交的草稿，再" : "请"}刷新页面。` : error}
    {changed && <UiButton actionId="ui.generation.refresh" type="button" className="btn" onClick={() => { if (!hasUnsavedChanges() || window.confirm("仍有未保存的草稿。确认刷新并放弃这些内容？")) window.location.reload(); }}>刷新页面</UiButton>}</aside>;
}

export const browserUiSdk = Object.freeze({ sdkVersion: UI_SDK_VERSION, UI_ACTIONS, React, createElement: React.createElement,
  client: Object.freeze({ api, post, download, readSession, setCsrfToken, parseStrictJson, createMutationIntent, createOperationKey }), useUnsavedChanges, hasUnsavedChanges,
  definePlugin, defineSlot, provideSlot, useHost, UiHostProvider, notify, createPortal, useUiContract, useSlot: useUiContract, useAction, useTranslation, UiText, UiElement, UiSlot, UiRegion, UiButton, withUiSlot } satisfies MotorConsoleUiSdk);
Object.defineProperty(globalThis, "MotorConsoleUiSdk", { value: browserUiSdk, configurable: false, writable: false });
