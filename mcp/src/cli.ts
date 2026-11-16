#!/usr/bin/env node
import { resolve } from "node:path";
import { PluginHost, pinComposition, planSummary, validateComposition } from "@mindie/console-platform/host";
import { McpError } from "./contracts.js";

function args(values: string[], names: string[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (let index = 0; index < values.length; index += 2) {
    const flag = values[index]; const value = values[index + 1];
    if (!flag?.startsWith("--") || !names.includes(flag.slice(2)) || !value || value.startsWith("--") || Object.hasOwn(result, flag.slice(2))) throw new McpError("ARGUMENTS", "Unknown, duplicate or incomplete command option");
    result[flag.slice(2)] = value;
  }
  for (const name of names) if (!Object.hasOwn(result, name)) throw new McpError("ARGUMENTS", `Missing --${name}`);
  return result;
}
function print(value: unknown, stream: NodeJS.WritableStream = process.stdout): void { stream.write(`${JSON.stringify(value, null, 2)}\n`); }
async function main(): Promise<void> {
  const [command, ...values] = process.argv.slice(2);
  if (!command || command === "help" || command === "--help") {
    process.stdout.write("console-mcp pin --input TEMPLATE --output NEW_CONFIG\nconsole-mcp validate --config CONFIG\nconsole-mcp start --config CONFIG\n\nOnly explicitly selected, exact-version, SHA256-pinned plugins execute on start.\nThe start command reserves stdout for MCP stdio and sends all diagnostics to stderr.\nThe reference authentication plugin uses private service credentials, not OAuth.\n"); return;
  }
  if (command === "pin") {
    const options = args(values, ["input", "output"]);
    print(await pinComposition(resolve(options.input!), resolve(options.output!))); return;
  }
  if (command !== "validate" && command !== "start") throw new McpError("ARGUMENTS", "Unsupported command; use help");
  const options = args(values, ["config"]);
  const plan = await validateComposition(resolve(options.config!));
  // A headless MCP composition has no implicit packages and must resolve the four explicit slots.
  for (const [contract, cardinality] of [["console.mcp.server", "one"], ["console.mcp.auth", "one"], ["console.mcp.control", "one"], ["console.mcp.toolsets", "many"]] as const) {
    if (plan.slots.get(contract)?.cardinality !== cardinality || !plan.bindings.get(contract)?.length) throw new McpError("CONTRACT", `MCP composition requires ${contract} (${cardinality})`);
  }
  if (command === "validate") { print(planSummary(plan)); return; }
  const host = new PluginHost(plan, { log: event => print(event, process.stderr) });
  const stop = () => { void host.stop(); };
  const serverProvider = plan.bindings.get("console.mcp.server")![0]!;
  const stdio = plan.composition.settings[serverProvider]?.transport === "stdio";
  process.once("SIGINT", stop); process.once("SIGTERM", stop);
  if (stdio) process.stdin.once("end", stop);
  try {
    await host.start(); print(host.diagnostics(), process.stderr);
    await host.whenStopped;
    const diagnostics = host.diagnostics(); print(diagnostics, process.stderr);
    if (diagnostics.packages.some(item => item.state === "failed")) process.exitCode = 1;
  } catch (error) { print(host.diagnostics(), process.stderr); throw error; }
  finally { process.removeListener("SIGINT", stop); process.removeListener("SIGTERM", stop); if (stdio) process.stdin.removeListener("end", stop); }
}
void main().catch(error => {
  const code = error instanceof Error && "code" in error && typeof error.code === "string" && /^[A-Z0-9_]{1,64}$/.test(error.code) ? error.code : "MCP_HOST_ERROR";
  print({ valid: false, code, error: error instanceof Error ? error.message : "MCP host failed" }, process.stderr);
  process.exitCode = 1;
});
