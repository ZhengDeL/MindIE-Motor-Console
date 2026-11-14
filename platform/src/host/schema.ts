import { PluginError, SDK_VERSION } from "../sdk/index.js";
import type { Composition, JsonValue, PluginManifest } from "../sdk/index.js";

const identifier = /^[a-z][a-z0-9]*(?:[._/-][a-z0-9]+)*$/;
const versionPattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
export function fail(code: string, message: string): never { throw new PluginError(code, message); }
export function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("SCHEMA", `${label} must be an object`);
  return value as Record<string, unknown>;
}
function keys(value: Record<string, unknown>, allowed: string[], label: string): void {
  for (const key of Object.keys(value)) if (!allowed.includes(key)) fail("SCHEMA", `${label}: unknown field ${key}`);
}
function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value) || value.length > 1024) fail("SCHEMA", `${label} must be an array of at most 1024 entries`);
  return value;
}
export function string(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.length || value.length > 4096 || /[\x00-\x1f\x7f]/.test(value)) fail("SCHEMA", `${label} must be a nonempty string without controls`);
  return value;
}
function id(value: unknown, label: string): string {
  const result = string(value, label);
  if (!identifier.test(result) || result.length > 160) fail("SCHEMA", `${label} is not a valid identifier`);
  return result;
}
export function version(value: unknown, label: string): string {
  const result = string(value, label);
  if (!versionPattern.test(result) || result.split(".").some(part => !Number.isSafeInteger(Number(part)))) fail("VERSION", `${label} must be an exact major.minor.patch version`);
  return result;
}
function range(value: unknown, label: string): string {
  const result = string(value, label);
  version(result.replace(/^[~^=]/, ""), label);
  return result;
}
export function satisfies(actual: string, requested: string): boolean {
  const operator = /^[~^=]/.test(requested) ? requested[0] : "=";
  const requestedVersion = requested.replace(/^[~^=]/, "");
  const a = actual.split(".").map(Number) as [number, number, number];
  const b = requestedVersion.split(".").map(Number) as [number, number, number];
  const compare = a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
  if (operator === "=") return compare === 0;
  if (compare < 0 || a[0] !== b[0]) return false;
  if (operator === "~") return a[1] === b[1];
  return b[0] > 0 || (b[1] > 0 ? a[1] === b[1] : a[1] === 0 && a[2] === b[2]);
}
function relativePath(value: unknown, label: string): string {
  const result = string(value, label);
  if (result.includes("\\") || result.startsWith("/") || result.split("/").some(part => !part || part === "." || part === "..") || result.includes(":")) fail("PATH", `${label} must be a relative forward-slash path without traversal`);
  return result;
}
function unique(values: string[], label: string): void {
  if (new Set(values).size !== values.length) fail("DUPLICATE", `${label} contains duplicate entries`);
}
function json(value: unknown, label: string, depth = 0): asserts value is JsonValue {
  if (depth > 64) fail("SCHEMA", `${label} exceeds JSON depth 64`);
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (Array.isArray(value)) { for (const item of value) json(item, label, depth + 1); return; }
  if (typeof value === "object" && value) {
    for (const [key, item] of Object.entries(value)) {
      if (["__proto__", "constructor", "prototype"].includes(key)) fail("SCHEMA", `${label} contains a reserved key`);
      json(item, label, depth + 1);
    }
    return;
  }
  fail("SCHEMA", `${label} contains a non-JSON value`);
}
export function parseManifest(value: unknown): PluginManifest {
  const item = record(value, "manifest");
  keys(item, ["schemaVersion", "id", "version", "sdk", "entry", "provides", "requires", "ui"], "manifest");
  if (item.schemaVersion !== 1) fail("SCHEMA", "manifest.schemaVersion must be 1");
  id(item.id, "manifest.id"); version(item.version, "manifest.version");
  if (!satisfies(SDK_VERSION, range(item.sdk, "manifest.sdk"))) fail("SDK_VERSION", `${item.id}: incompatible host SDK ${item.sdk}`);
  if (!/\.(js|mjs)$/.test(relativePath(item.entry, "manifest.entry"))) fail("PATH", "manifest.entry must be JavaScript ESM (.js or .mjs)");
  const provides = array(item.provides, "manifest.provides").map(value => {
    const entry = record(value, "provides entry"); keys(entry, ["contract", "version"], "provides entry");
    return { contract: id(entry.contract, "provides.contract"), version: version(entry.version, "provides.version") };
  });
  unique(provides.map(entry => entry.contract), "manifest.provides");
  const requires = array(item.requires, "manifest.requires").map(value => {
    const entry = record(value, "requires entry"); keys(entry, ["contract", "range", "optional"], "requires entry");
    if (entry.optional !== undefined && typeof entry.optional !== "boolean") fail("SCHEMA", "requires.optional must be boolean");
    return { contract: id(entry.contract, "requires.contract"), range: range(entry.range, "requires.range") };
  });
  unique(requires.map(entry => entry.contract), "manifest.requires");
  if (item.ui !== undefined) {
    const ui = record(item.ui, "manifest.ui"); keys(ui, ["sdkVersion", "catalogPath", "assets"], "manifest.ui");
    version(ui.sdkVersion, "ui.sdkVersion"); relativePath(ui.catalogPath, "ui.catalogPath");
    const assets = array(ui.assets, "ui.assets").map(value => {
      const asset = record(value, "ui asset"); keys(asset, ["path", "sha256"], "ui asset");
      sha256(asset.sha256, "ui asset.sha256"); return relativePath(asset.path, "ui asset.path");
    });
    unique(assets, "ui.assets");
    if (!assets.includes(ui.catalogPath as string)) fail("SCHEMA", "UI catalogPath must be listed in hashed assets");
  }
  return item as unknown as PluginManifest;
}
export function sha256(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) fail("INTEGRITY", `${label} must be lowercase SHA256`);
  return value;
}
export function parseComposition(value: unknown, allowUnpinned = false): Composition {
  const item = record(value, "composition");
  keys(item, ["schemaVersion", "contracts", "packages", "selected", "bindings", "settings"], "composition");
  if (item.schemaVersion !== 1) fail("SCHEMA", "composition.schemaVersion must be 1");
  const contracts = array(item.contracts, "contracts").map(value => {
    const contract = record(value, "contract"); keys(contract, ["id", "version", "cardinality"], "contract");
    id(contract.id, "contract.id"); version(contract.version, "contract.version");
    if (!["one", "optional", "many"].includes(contract.cardinality as string)) fail("SCHEMA", "contract.cardinality must be one, optional or many");
    return contract;
  });
  unique(contracts.map(entry => entry.id as string), "contracts");
  const packages = array(item.packages, "packages").map(value => {
    const pkg = record(value, "package"); keys(pkg, ["id", "version", "root", "sha256"], "package");
    id(pkg.id, "package.id"); version(pkg.version, "package.version"); string(pkg.root, "package.root");
    if (!(allowUnpinned && pkg.sha256 === "PIN")) sha256(pkg.sha256, "package.sha256");
    return pkg;
  });
  unique(packages.map(entry => entry.id as string), "packages");
  const selected = array(item.selected, "selected").map(value => id(value, "selected id")); unique(selected, "selected");
  const bindings = record(item.bindings, "bindings");
  for (const [key, value] of Object.entries(bindings)) {
    id(key, "binding contract");
    if (Array.isArray(value)) unique(array(value, "binding providers").map(value => id(value, "binding provider")), "binding providers");
    else id(value, "binding provider");
  }
  const settings = record(item.settings, "settings"); json(settings, "settings");
  for (const [key, value] of Object.entries(settings)) { id(key, "settings package"); record(value, `settings.${key}`); }
  return item as unknown as Composition;
}

export function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
}
