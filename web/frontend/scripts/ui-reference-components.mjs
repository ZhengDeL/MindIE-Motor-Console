import { readFile, readdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
const contributions = [];
for (const filename of (await readdir(path.join(root, "src"))).filter(value => value.endsWith(".tsx")).sort()) {
  const source = await readFile(path.join(root, "src", filename), "utf8");
  for (const match of source.matchAll(/(?:export )?const [A-Za-z0-9]+ = withUiSlot\("([^"]+)", (Default[A-Za-z0-9]+)\);/g))
    contributions.push({ slot: match[1], file: filename.slice(0, -4), name: match[2] });
}
contributions.sort((a, b) => a.slot < b.slot ? -1 : a.slot > b.slot ? 1 : 0);
const declarations = contributions.map(item => `  ${JSON.stringify(item.slot)}: () => import(${JSON.stringify(`./${item.file}`)}).then(module => module.${item.name}),`).join("\n");
const source = `/** Concrete reference providers. Resolve selected implementations during activation, before interactive rendering. */
import { createElement } from "react";
import type { ComponentType } from "react";
const loaders: Record<string, () => Promise<ComponentType<any>>> = {
${declarations}
};
const implementations = new Map<string, ComponentType<any>>();
const pending = new Map<string, Promise<void>>();
function prepare(slot: string): Promise<void> {
  if (implementations.has(slot)) return Promise.resolve();
  const active = pending.get(slot);
  if (active) return active;
  const loading = loaders[slot]().then(implementation => { implementations.set(slot, implementation); });
  pending.set(slot, loading);
  return loading;
}
export const referenceUiComponents: Readonly<Record<string, ComponentType<any>>> = Object.freeze(Object.fromEntries(
  Object.keys(loaders).map(slot => [slot, function ConcreteReferenceProvider(props: any) {
    const Implementation = implementations.get(slot);
    if (!Implementation) throw Error(\x60Selected reference component was not initialized: \x24{slot}\x60);
    return createElement(Implementation, props);
  }]),
));
export async function prepareReferenceUiComponents(selectedSlots: readonly string[]): Promise<void> {
  await Promise.all(selectedSlots.filter(slot => Object.hasOwn(loaders, slot)).map(prepare));
}
`;
await writeFile(path.join(root, "src/referenceUiComponents.tsx"), source);
