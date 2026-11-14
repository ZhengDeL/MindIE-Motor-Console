import type { Composition, ContractSlot } from "../sdk/index.js";
import { inspectArtifact, readComposition } from "./artifacts.js";
import type { Artifact, CompositionDocument } from "./artifacts.js";
import { fail, satisfies } from "./schema.js";

export interface Plan extends CompositionDocument {
  artifacts: Map<string, Artifact>;
  order: string[];
  bindings: Map<string, string[]>;
  slots: Map<string, ContractSlot>;
}
export async function planDocument(document: CompositionDocument): Promise<Plan> {
  const { composition } = document;
  const artifacts = new Map<string, Artifact>();
  for (const selected of composition.packages) artifacts.set(selected.id, await inspectArtifact(selected, document.file));
  const selected = new Set(composition.selected);
  for (const id of selected) if (!artifacts.has(id)) fail("MISSING_PACKAGE", `Selected package ${id} has not been imported`);
  for (const id of Object.keys(composition.settings)) if (!artifacts.has(id)) fail("SETTINGS", `Settings refer to an unimported package ${id}`);
  const slots = new Map(composition.contracts.map(slot => [slot.id, slot]));
  for (const id of Object.keys(composition.bindings)) if (!slots.has(id)) fail("CONTRACT", `Binding for undeclared contract ${id}`);
  const bindings = new Map<string, string[]>();
  for (const slot of slots.values()) {
    const configured = Object.hasOwn(composition.bindings, slot.id) ? composition.bindings[slot.id] : undefined;
    const ids = configured === undefined ? [] : Array.isArray(configured) ? [...configured] : [configured];
    if (slot.cardinality === "one" && ids.length !== 1) fail("CARDINALITY", `${slot.id} requires exactly one provider`);
    if (slot.cardinality === "optional" && ids.length > 1) fail("CARDINALITY", `${slot.id} permits at most one provider`);
    for (const id of ids) {
      if (!selected.has(id)) fail("UNSELECTED_PROVIDER", `${slot.id} binds unselected package ${id}`);
      const provided = artifacts.get(id)!.manifest.provides.find(item => item.contract === slot.id);
      if (!provided) fail("CONTRACT", `${id} does not provide ${slot.id}`);
      if (provided.version !== slot.version) fail("CONTRACT_VERSION", `${id}: ${slot.id} provides ${provided.version}, slot requires ${slot.version}`);
    }
    bindings.set(slot.id, ids);
  }
  const dependencies = new Map<string, Set<string>>();
  for (const id of selected) {
    const manifest = artifacts.get(id)!.manifest;
    const dependenciesForId = new Set<string>();
    for (const provided of manifest.provides) if (!slots.has(provided.contract)) fail("CONTRACT", `${id} provides undeclared contract ${provided.contract}`);
    for (const dependency of manifest.requires) {
      const slot = slots.get(dependency.contract);
      if (!slot) fail("CONTRACT", `${id} requires undeclared contract ${dependency.contract}`);
      const providers = bindings.get(dependency.contract)!;
      if (!providers.length && !dependency.optional) fail("MISSING_DEPENDENCY", `${id} requires unbound ${dependency.contract}`);
      if (!satisfies(slot.version, dependency.range)) fail("DEPENDENCY_VERSION", `${id} requires ${dependency.contract} ${dependency.range}, selected ${slot.version}`);
      for (const provider of providers) dependenciesForId.add(provider);
    }
    dependencies.set(id, dependenciesForId);
  }
  const order: string[] = [];
  const visiting = new Set<string>();
  const visited = new Set<string>();
  function visit(id: string, chain: string[]): void {
    if (visiting.has(id)) fail("CYCLE", `Plugin dependency cycle: ${[...chain, id].join(" -> ")}`);
    if (visited.has(id)) return;
    visiting.add(id);
    for (const dependency of dependencies.get(id)!) visit(dependency, [...chain, id]);
    visiting.delete(id); visited.add(id); order.push(id);
  }
  for (const id of [...selected].sort()) visit(id, []);
  return { ...document, artifacts, order, bindings, slots };
}
export async function validateComposition(file: string): Promise<Plan> { return planDocument(await readComposition(file)); }
export function planSummary(plan: Plan): object {
  return {
    valid: true, generation: plan.generation, codeExecuted: false, order: plan.order,
    contracts: plan.composition.contracts,
    bindings: Object.fromEntries(plan.bindings),
    packages: [...plan.artifacts.values()].map(item => ({
      id: item.manifest.id, version: item.manifest.version, sha256: item.selection.sha256,
      state: plan.composition.selected.includes(item.manifest.id) ? "selected" : "imported",
    })),
  };
}
