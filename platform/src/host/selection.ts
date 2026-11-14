import { open, rename, unlink } from "node:fs/promises";
import type { FileHandle } from "node:fs/promises";
import type { Stats } from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname, resolve } from "node:path";
import { digest, directoryPath, generation, inspectArtifact, readComposition, readDocumentBytes, regularFile, sameFile } from "./artifacts.js";
import { planDocument } from "./planner.js";
import { fail, parseComposition } from "./schema.js";
import type { Composition } from "../sdk/index.js";

function bytes(composition: Composition): string { return `${JSON.stringify(composition, null, 2)}\n`; }
async function createNew(file: string): Promise<FileHandle> {
  await directoryPath(dirname(file));
  const handle = await open(file, "wx", 0o600);
  try {
    if (!sameFile(await handle.stat(), await regularFile(file))) fail("FILE", "New file changed while opening");
    return handle;
  } catch (error) { await handle.close(); throw error; }
}
async function writeNew(file: string, contents: string | Buffer): Promise<Stats> {
  const handle = await createNew(file);
  try {
    await handle.writeFile(contents); await handle.sync();
    const info = await handle.stat();
    if (!sameFile(info, await regularFile(file))) fail("FILE", "New file changed while writing");
    return info;
  } finally { await handle.close(); }
}
async function removeOwned(file: string, created: Stats): Promise<void> {
  if (!sameFile(created, await regularFile(file))) fail("FILE", "Refusing to remove a replaced selection temporary file");
  await unlink(file);
}
function rooted(composition: Composition, sourceFile: string): Composition {
  return { ...composition, packages: composition.packages.map(pkg => ({ ...pkg, root: resolve(dirname(sourceFile), pkg.root) })) };
}
export async function pinComposition(input: string, output: string): Promise<{ generation: string; restartRequired: true; codeExecuted: false }> {
  const document = await readComposition(input, true);
  const composition = rooted(structuredClone(document.composition), document.file);
  for (const pkg of composition.packages) pkg.sha256 = (await inspectArtifact(pkg, document.file, false)).selection.sha256;
  const file = resolve(output);
  const selectedGeneration = generation(composition);
  await planDocument({ file, composition, generation: selectedGeneration });
  await writeNew(file, bytes(composition));
  return { generation: selectedGeneration, restartRequired: true, codeExecuted: false };
}
/** Local compare-and-swap with an exclusive lock; backup is mandatory and never overwritten. */
export async function replaceComposition(config: string, candidate: string, expected: string, backup: string, expectedIsFileHash = false): Promise<{ generation: string; previousGeneration: string | null; previousFileSha256: string; restartRequired: true; codeExecuted: false }> {
  const target = resolve(config);
  const backupFile = resolve(backup);
  if (backupFile === target || backupFile === resolve(candidate)) fail("BACKUP", "Backup must have a separate new path");
  const lock = `${target}.lock`;
  const handle = await createNew(lock);
  const lockInfo = await handle.stat();
  const temporary = `${target}.${randomUUID()}.tmp`;
  let temporaryInfo: Stats | undefined;
  try {
    const previousBytes = await readDocumentBytes(target);
    const previousFileSha256 = digest(previousBytes);
    const previous = expectedIsFileHash ? undefined : await readComposition(target);
    if ((expectedIsFileHash ? previousFileSha256 : previous!.generation) !== expected) fail("REVISION_CONFLICT", "Selection generation/file hash changed; inspect the current composition before retrying");
    const next = await readComposition(candidate);
    const composition = parseComposition(rooted(next.composition, next.file));
    const nextGeneration = generation(composition);
    await planDocument({ file: target, composition, generation: nextGeneration });
    await writeNew(backupFile, previousBytes);
    temporaryInfo = await writeNew(temporary, bytes(composition));
    await regularFile(target);
    await directoryPath(dirname(temporary));
    await rename(temporary, target); temporaryInfo = undefined;
    return { generation: nextGeneration, previousGeneration: previous?.generation ?? null, previousFileSha256, restartRequired: true, codeExecuted: false };
  } finally {
    await handle.close();
    try { if (temporaryInfo) await removeOwned(temporary, temporaryInfo); }
    finally { await removeOwned(lock, lockInfo); }
  }
}
