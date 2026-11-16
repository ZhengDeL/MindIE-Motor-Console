import { pathToFileURL } from "node:url";
import { PluginError } from "../sdk/index.js";
import type { Cleanup, ContractValue, Plugin, PluginContext, PluginDiagnostic } from "../sdk/index.js";
import { packageDigest } from "./artifacts.js";
import { fail } from "./schema.js";
import { validateComposition } from "./planner.js";
import type { Plan } from "./planner.js";

// ESM dependency caches are process-wide. A changed root requires a fresh process, not mixed code.
const processArtifactPins = new Map<string, string>();

function freeze<T>(value: T): T {
  if (value && typeof value === "object") { for (const child of Object.values(value)) freeze(child); Object.freeze(value); }
  return value;
}
async function bounded<T>(promise: Promise<T>, milliseconds: number, label: string): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new PluginError("TIMEOUT", `${label} exceeded ${milliseconds} ms`)), milliseconds); });
  try { return await Promise.race([promise, timeout]); } finally { if (timer) clearTimeout(timer); }
}
export interface HostOptions {
  startupTimeoutMs?: number;
  shutdownTimeoutMs?: number;
  log?: (event: { level: string; packageId: string; generation: string; message: string }) => void;
}
/** A single immutable generation. Switching selection always requires a new process. */
export class PluginHost {
  readonly generation: string;
  private readonly controller = new AbortController();
  private readonly values = new Map<string, Map<string, unknown>>();
  private readonly cleanups: { id: string; cleanup: Cleanup }[] = [];
  private readonly starts: { id: string; start: (signal: AbortSignal) => void | Promise<void> }[] = [];
  private readonly states = new Map<string, PluginDiagnostic>();
  private phase: "new" | "starting" | "running" | "draining" | "stopped" = "new";
  private stopPromise: Promise<void> | undefined;
  private activePackage: string | undefined;
  private resolveStopped!: () => void;
  readonly whenStopped = new Promise<void>(resolve => { this.resolveStopped = resolve; });
  constructor(readonly plan: Plan, private readonly options: HostOptions = {}) {
    this.generation = plan.generation;
    freeze(plan.composition);
    for (const item of plan.artifacts.values()) this.states.set(item.manifest.id, {
      id: item.manifest.id, version: item.manifest.version, sha256: item.selection.sha256,
      state: plan.composition.selected.includes(item.manifest.id) ? "selected" : "imported",
    });
  }
  get running(): boolean { return this.phase === "running"; }
  diagnostics(): { generation: string; state: string; packages: PluginDiagnostic[] } {
    return { generation: this.generation, state: this.phase, packages: [...this.states.values()].map(item => ({ ...item })) };
  }
  private context(id: string): PluginContext {
    const host = this;
    const artifact = this.plan.artifacts.get(id)!;
    const allowed = new Map(artifact.manifest.requires.map(item => [item.contract, item]));
    const provided = new Set(artifact.manifest.provides.map(item => item.contract));
    const read = (contract: string): unknown[] => {
      if (!allowed.has(contract)) fail("UNDECLARED_DEPENDENCY", `${id} attempted to consume undeclared ${contract}`);
      return host.plan.bindings.get(contract)!.map(provider => {
        const values = host.values.get(provider);
        if (!values?.has(contract)) fail("NOT_READY", `${provider} has not provided ${contract}`);
        return values.get(contract);
      });
    };
    return {
      packageId: id, generation: host.generation,
      settings: freeze(structuredClone(Object.hasOwn(host.plan.composition.settings, id) ? host.plan.composition.settings[id] ?? {} : {})),
      signal: host.controller.signal,
      get running() { return host.running; },
      provide<K extends string>(contract: K, value: ContractValue<K>): void {
        if (host.activePackage !== id || host.phase !== "starting") fail("LIFECYCLE", `${id} may provide only during activation`);
        if (!provided.has(contract)) fail("UNDECLARED_PROVIDE", `${id} attempted to provide undeclared ${contract}`);
        if (value === undefined || value === null) fail("INVALID_PROVIDE", `${id}: ${contract} cannot be null or undefined`);
        const values = host.values.get(id)!;
        if (values.has(contract)) fail("DUPLICATE", `${id} provided ${contract} twice`);
        values.set(contract, value);
      },
      consume<K extends string>(contract: K): ContractValue<K> {
        if (host.plan.slots.get(contract)?.cardinality === "many") fail("CARDINALITY", `Use consumeAll for ${contract}`);
        const values = read(contract);
        if (values.length !== 1) fail("UNAVAILABLE", `${contract} is unavailable`);
        return values[0] as ContractValue<K>;
      },
      consumeOptional<K extends string>(contract: K): ContractValue<K> | undefined {
        if (!allowed.get(contract)?.optional) fail("DEPENDENCY", `${id} must declare ${contract} optional to consumeOptional`);
        if (host.plan.slots.get(contract)?.cardinality === "many") fail("CARDINALITY", `Use consumeAll for ${contract}`);
        return read(contract)[0] as ContractValue<K> | undefined;
      },
      consumeAll<K extends string>(contract: K): readonly ContractValue<K>[] {
        if (host.plan.slots.get(contract)?.cardinality !== "many") fail("CARDINALITY", `consumeAll requires a many slot: ${contract}`);
        return Object.freeze(read(contract)) as readonly ContractValue<K>[];
      },
      onStart(start): void {
        if (host.activePackage !== id || host.phase !== "starting") fail("LIFECYCLE", "Start hooks may only be registered during activation");
        host.starts.push({ id, start });
      },
      onStop(cleanup): void {
        if (host.controller.signal.aborted) fail("LIFECYCLE", "Generation is stopping; cleanup registration is closed");
        host.cleanups.push({ id, cleanup });
      },
      fail(error): void {
        const state = host.states.get(id)!;
        state.state = "failed"; state.error = error.message;
        void host.stop();
      },
      log(level, message): void { host.options.log?.({ level, packageId: id, generation: host.generation, message }); },
    };
  }
  async start(): Promise<this> {
    if (this.phase !== "new") fail("LIFECYCLE", "A host generation can only start once");
    this.phase = "starting";
    let current: string | undefined;
    try {
      for (const id of this.plan.order) {
        this.controller.signal.throwIfAborted();
        current = id;
        const artifact = this.plan.artifacts.get(id)!;
        this.states.get(id)!.state = "starting";
        // Recheck immediately before import; only explicitly selected modules are imported.
        const actualDigest = await packageDigest(artifact.root);
        this.controller.signal.throwIfAborted();
        if (actualDigest !== artifact.selection.sha256) fail("INTEGRITY", `${id}: artifact changed since validation`);
        const previousPin = processArtifactPins.get(artifact.root);
        if (previousPin && previousPin !== artifact.selection.sha256) fail("RESTART_REQUIRED", `${id}: package root changed in this process; restart before using the new artifact`);
        processArtifactPins.set(artifact.root, artifact.selection.sha256);
        const module = await bounded(import(pathToFileURL(artifact.entry).href) as Promise<{ default?: Plugin }>, this.options.startupTimeoutMs ?? 30000, `${id} module import`);
        this.controller.signal.throwIfAborted();
        if (!module.default || typeof module.default.activate !== "function") fail("PLUGIN_EXPORT", `${id} must export default { activate(context) }`);
        this.values.set(id, new Map());
        this.activePackage = id;
        await bounded(Promise.resolve().then(() => {
          this.controller.signal.throwIfAborted();
          return module.default!.activate(this.context(id));
        }), this.options.startupTimeoutMs ?? 30000, `${id} activation`);
        this.controller.signal.throwIfAborted();
        this.activePackage = undefined;
        for (const declaration of artifact.manifest.provides) if (!this.values.get(id)!.has(declaration.contract)) fail("MISSING_PROVIDE", `${id} did not provide declared ${declaration.contract}`);
      }
      for (const { id, start } of this.starts) {
        this.controller.signal.throwIfAborted();
        current = id;
        await bounded(Promise.resolve().then(() => {
          this.controller.signal.throwIfAborted();
          return start(this.controller.signal);
        }), this.options.startupTimeoutMs ?? 30000, `${id} startup`);
        this.controller.signal.throwIfAborted();
      }
      if (this.controller.signal.aborted) fail("LIFECYCLE", "Generation was stopped during startup");
      this.phase = "running";
      for (const id of this.plan.order) this.states.get(id)!.state = "running";
      return this;
    } catch (error) {
      this.activePackage = undefined;
      if (current) {
        const state = this.states.get(current)!;
        state.state = "failed"; state.error = error instanceof Error ? error.message : "Plugin startup failed";
      }
      await this.stop();
      throw error;
    }
  }
  stop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise;
    this.phase = "draining";
    const deadline = performance.now() + (this.options.shutdownTimeoutMs ?? 10000);
    // Publish the shared completion before abort listeners can reenter stop().
    this.stopPromise = Promise.resolve().then(async () => {
      for (const id of this.plan.order) if (this.states.get(id)!.state === "running" || this.states.get(id)!.state === "starting") this.states.get(id)!.state = "draining";
      for (const { id, cleanup } of [...this.cleanups].reverse()) {
        try {
          const pending = Promise.resolve().then(cleanup);
          const remaining = deadline - performance.now();
          if (remaining <= 0) {
            // Invoke every registered cleanup, but never restart an exhausted total wait budget.
            void pending.catch(() => undefined);
            throw new PluginError("TIMEOUT", `${id} cleanup completion is unknown after the total shutdown budget expired`);
          }
          await bounded(pending, remaining, `${id} cleanup`);
        }
        catch (error) {
          const state = this.states.get(id)!;
          state.state = "failed"; state.error = error instanceof Error ? error.message : "Plugin cleanup failed";
        }
      }
      this.values.clear();
      for (const state of this.states.values()) if (state.state === "draining") state.state = "stopped";
      this.phase = "stopped";
      this.resolveStopped();
    });
    this.controller.abort(new PluginError("SHUTDOWN", "Host generation is stopping"));
    return this.stopPromise;
  }
}
export async function bootstrap(configFile: string, options: HostOptions = {}): Promise<PluginHost> {
  return new PluginHost(await validateComposition(configFile), options).start();
}
