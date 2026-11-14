#!/usr/bin/env node
import { PluginError } from "./sdk/index.js";
import { PluginHost } from "./host/runtime.js";
import { planSummary, validateComposition } from "./host/planner.js";
import { pinComposition, replaceComposition } from "./host/selection.js";
import { importArtifact } from "./host/importer.js";
import { digest, inspectArtifact, packageDigest, readDocumentBytes, readJson } from "./host/artifacts.js";
import { parseManifest } from "./host/schema.js";
import { join, resolve } from "node:path";

function print(value: unknown): void { process.stdout.write(`${JSON.stringify(value, null, 2)}\n`); }
function argumentsFor(values: string[], allowed: string[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (let index = 0; index < values.length; index += 2) {
    const flag = values[index]; const value = values[index + 1];
    if (!flag?.startsWith("--") || !allowed.includes(flag.slice(2)) || !value || value.startsWith("--") || Object.hasOwn(result, flag.slice(2))) throw new PluginError("ARGUMENTS", "Unknown, duplicate or incomplete command option");
    result[flag.slice(2)] = value;
  }
  for (const flag of allowed) if (!Object.hasOwn(result, flag)) throw new PluginError("ARGUMENTS", `Missing --${flag}`);
  return result;
}
const usage = `console-platform inspect --source REVIEWED_DIRECTORY
console-platform import --source REVIEWED_DIRECTORY --store ARTIFACT_DIRECTORY
console-platform pin --input TEMPLATE --output NEW_CONFIG
console-platform validate --config CONFIG
console-platform start --config CONFIG
console-platform switch --config CONFIG --candidate VALIDATED_CONFIG --expected-generation SHA256 --backup NEW_BACKUP
console-platform recover --config CONFIG --candidate PREVIOUS_CONFIG --expected-generation SHA256 --backup NEW_BACKUP
console-platform config-hash --config CONFIG
console-platform recover --config DAMAGED_CONFIG --candidate PREVIOUS_CONFIG --expected-file-sha256 SHA256 --backup NEW_BACKUP

All packages are local, exact-version and SHA256-pinned. Only start executes selected code.
Switch/recover change next-start selection only. They never migrate data or replay operations.
Stop the host before switching identity, storage or execution providers. Retain old package roots.
The recovery command is local; there is no recovery HTTP endpoint or implicit fallback.`;
async function main(): Promise<void> {
  const [command, ...values] = process.argv.slice(2);
  if (command === "help" || command === "--help" || !command) { process.stdout.write(`${usage}\n`); return; }
  if (command === "inspect") {
    const args = argumentsFor(values, ["source"]);
    const root = resolve(args.source!);
    const manifest = parseManifest(await readJson(join(root, "manifest.json")));
    const selection = { id: manifest.id, version: manifest.version, root, sha256: await packageDigest(root) };
    const artifact = await inspectArtifact(selection, join(process.cwd(), "composition.json"));
    print({ package: selection, manifest: artifact.manifest, codeExecuted: false, selected: false }); return;
  }
  if (command === "import") {
    const args = argumentsFor(values, ["source", "store"]);
    print(await importArtifact(args.source!, args.store!)); return;
  }
  if (command === "config-hash") {
    const args = argumentsFor(values, ["config"]);
    print({ fileSha256: digest(await readDocumentBytes(args.config!)), codeExecuted: false }); return;
  }
  if (command === "pin") {
    const args = argumentsFor(values, ["input", "output"]);
    print(await pinComposition(args.input!, args.output!)); return;
  }
  if (command === "switch" || command === "recover") {
    const rawRecovery = command === "recover" && values.includes("--expected-file-sha256");
    const expectedFlag = rawRecovery ? "expected-file-sha256" : "expected-generation";
    const args = argumentsFor(values, ["config", "candidate", expectedFlag, "backup"]);
    print(await replaceComposition(args.config!, args.candidate!, args[expectedFlag]!, args.backup!, rawRecovery)); return;
  }
  if (command !== "start" && command !== "validate") throw new PluginError("ARGUMENTS", "Unsupported command; use help");
  const args = argumentsFor(values, ["config"]);
  const plan = await validateComposition(args.config!);
  if (command === "validate") { print(planSummary(plan)); return; }
  const host = new PluginHost(plan, { log: event => process.stderr.write(`${JSON.stringify(event)}\n`) });
  const stop = () => { void host.stop(); };
  process.once("SIGINT", stop); process.once("SIGTERM", stop);
  try {
    await host.start(); print(host.diagnostics());
    await host.whenStopped;
    const diagnostics = host.diagnostics(); print(diagnostics);
    if (diagnostics.packages.some(item => item.state === "failed")) process.exitCode = 1;
  } catch (error) {
    print(host.diagnostics()); throw error;
  } finally { process.removeListener("SIGINT", stop); process.removeListener("SIGTERM", stop); }
}
void main().catch(error => {
  const code = error instanceof Error && "code" in error && typeof error.code === "string" && /^[A-Z0-9_]{1,64}$/.test(error.code) ? error.code : "HOST_ERROR";
  process.stderr.write(`${JSON.stringify({ valid: false, code, error: error instanceof Error ? error.message : "Host failed" })}\n`);
  process.exitCode = 1;
});
