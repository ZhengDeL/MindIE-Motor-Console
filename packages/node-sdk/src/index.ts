/** Public host contracts. Package authors may augment ContractMap with their own interfaces. */
import type { IncomingMessage, ServerResponse } from "node:http";

export const SDK_VERSION = "1.0.0";
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };
export interface ContractDeclaration { contract: string; version: string }
export interface DependencyDeclaration { contract: string; range: string; optional?: boolean }
export interface UiAsset { path: string; sha256: string }
export interface UiCatalogDescriptor {
  /** Contract of a separate browser SDK, never executable server code. */
  sdkVersion: string;
  catalogPath: string;
  assets: UiAsset[];
}
export interface PluginManifest {
  schemaVersion: 1;
  id: string;
  version: string;
  sdk: string;
  entry: string;
  provides: ContractDeclaration[];
  requires: DependencyDeclaration[];
  ui?: UiCatalogDescriptor;
}
export interface ContractSlot { id: string; version: string; cardinality: "one" | "optional" | "many" }
export interface PackageSelection { id: string; version: string; root: string; sha256: string }
export interface Composition {
  schemaVersion: 1;
  contracts: ContractSlot[];
  packages: PackageSelection[];
  selected: string[];
  bindings: Record<string, string | string[]>;
  settings: Record<string, JsonObject>;
}
export interface RequestContext {
  readonly generation: string;
  readonly requestId: string;
  readonly deadline: number;
  readonly signal: AbortSignal;
}
/** An HTTP provider must finish/destroy the response or reject. Bodies are opaque byte streams. */
export interface HttpHandler {
  handle(request: IncomingMessage, response: ServerResponse, context: RequestContext): Promise<void>;
}
export interface HttpTransport { address(): { host: string; port: number } | undefined }
export interface ContractMap {
  "console.http.handler": HttpHandler;
  "console.http.transport": HttpTransport;
  "console.ui.catalog": UiCatalogDescriptor;
}
export type ContractValue<K extends string> = K extends keyof ContractMap ? ContractMap[K] : unknown;
export type Cleanup = () => void | Promise<void>;
export interface PluginContext {
  readonly packageId: string;
  readonly generation: string;
  readonly settings: Readonly<JsonObject>;
  readonly signal: AbortSignal;
  /** False until the entire generation has started, and immediately false when draining begins. */
  readonly running: boolean;
  provide<K extends string>(contract: K, value: ContractValue<K>): void;
  consume<K extends string>(contract: K): ContractValue<K>;
  consumeOptional<K extends string>(contract: K): ContractValue<K> | undefined;
  consumeAll<K extends string>(contract: K): readonly ContractValue<K>[];
  onStart(start: (signal: AbortSignal) => void | Promise<void>): void;
  onStop(cleanup: Cleanup): void;
  /** Fail closed on an owned listener/process fault; the entire generation drains. */
  fail(error: Error): void;
  log(level: "info" | "warn" | "error", message: string): void;
}
export interface Plugin { activate(context: PluginContext): void | Promise<void> }
export type PluginState = "imported" | "selected" | "starting" | "running" | "draining" | "stopped" | "failed";
export interface PluginDiagnostic {
  id: string;
  version: string;
  sha256: string;
  state: PluginState;
  error?: string;
}
export class PluginError extends Error {
  constructor(readonly code: string, message: string) { super(message); this.name = "PluginError"; }
}

/** A narrow helper for plugin-specific runtime configuration validation. */
export function objectSetting(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new PluginError("SETTINGS", `${label} must be an object`);
  return value as JsonObject;
}
export function stringSetting(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim() || /[\x00-\x1f\x7f]/.test(value)) throw new PluginError("SETTINGS", `${label} must be a nonempty string without controls`);
  return value;
}
export function integerSetting(value: unknown, label: string, minimum: number, maximum: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum || value > maximum) throw new PluginError("SETTINGS", `${label} must be an integer in ${minimum}..${maximum}`);
  return value;
}
