import { createHash } from "node:crypto";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

// Compile-time identity of the reference UI, independent of checkout location and timestamps.
const root = fileURLToPath(new URL("..", import.meta.url));
const manifestPath = path.join(root, "public/ui-default-manifest.json");
const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
delete manifest.buildDigest;
const actions = [];
const definitions = JSON.parse(await readFile(path.join(root, "src/uiActionDefinitions.json"), "utf8"));
for (const filename of (await readdir(path.join(root, "src"))).filter(value => value.endsWith(".tsx")).sort()) {
  const source = await readFile(path.join(root, "src", filename), "utf8");
  for (const match of source.matchAll(/<(UiButton|Button)\b[^>]*?\bactionId="([^"]+)"/g))
    actions.push({ id: match[2], component: filename, renderer: match[1],
      description: definitions[match[2]]?.description ?? `组件本地动作 ${match[2]}`,
      context: definitions[match[2]]?.context ?? ["principal", "page", "label"] });
}
for (const [id, definition] of Object.entries(definitions))
  if (!actions.some(action => action.id === id)) actions.push({ id, component: "workflow", renderer: "action", ...definition });
await writeFile(path.join(root, "public/ui-action-catalog.json"), `${JSON.stringify({ schemaVersion: 1, actions }, null, 2)}\n`);
async function files(directory) {
  const entries = await readdir(path.join(root, directory), { withFileTypes: true });
  return (await Promise.all(entries.map(entry => entry.isDirectory() ? files(`${directory}/${entry.name}`) : [`${directory}/${entry.name}`]))).flat();
}
const inputs = [...await files("src"), "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json", "index.html", "public/ui-action-catalog.json"].sort();
const hash = createHash("sha256");
for (const filename of inputs) {
  hash.update(filename); hash.update("\0");
  hash.update((await readFile(path.join(root, filename), "utf8")).replace(/\r\n/g, "\n")); hash.update("\0");
}
hash.update(JSON.stringify(manifest));
manifest.buildDigest = hash.digest("hex");
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
process.stdout.write(`UI default build identity: ${manifest.buildDigest}\n`);
