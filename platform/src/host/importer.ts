import { cp, lstat, mkdir, mkdtemp, realpath, rename, rm } from "node:fs/promises";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { directoryPath, inspectArtifact, packageDigest, readJson } from "./artifacts.js";
import { fail, parseManifest } from "./schema.js";
import type { PackageSelection } from "../sdk/index.js";

/** Import a reviewed local directory, without importing JavaScript or running package-manager hooks. */
export async function importArtifact(source: string, store: string): Promise<{ package: PackageSelection; codeExecuted: false; selected: false }> {
  const sourceRoot = resolve(source);
  const artifactStore = resolve(store);
  const nested = relative(sourceRoot, artifactStore);
  if (!nested || (!isAbsolute(nested) && nested !== ".." && !nested.startsWith(`..${sep}`))) fail("PATH", "Artifact store must not be inside the source package");
  await directoryPath(sourceRoot);
  await directoryPath(artifactStore, true);
  const manifest = parseManifest(await readJson(join(sourceRoot, "manifest.json")));
  const sha256 = await packageDigest(sourceRoot);
  const selection = { id: manifest.id, version: manifest.version, root: sourceRoot, sha256 };
  await inspectArtifact(selection, join(sourceRoot, "composition.json"));
  await mkdir(artifactStore, { recursive: true, mode: 0o700 });
  await directoryPath(artifactStore);
  const storeInfo = await lstat(artifactStore);
  if (!storeInfo.isDirectory() || storeInfo.isSymbolicLink()) fail("ARTIFACT", "Artifact store must be a real directory");
  const storeReal = await realpath(artifactStore);
  const destination = join(artifactStore, sha256);
  const published = { ...selection, root: destination };
  const existing = await lstat(destination).catch(error => { if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined; throw error; });
  if (existing) {
    await inspectArtifact(published, join(artifactStore, "composition.json"));
    return { package: published, codeExecuted: false, selected: false };
  }
  const staging = await mkdtemp(join(artifactStore, ".import-"));
  await directoryPath(staging);
  const stagingInfo = await lstat(staging);
  const payload = join(staging, "package");
  try {
    await directoryPath(sourceRoot);
    await cp(sourceRoot, payload, { recursive: true, dereference: false, force: false, errorOnExist: true });
    if (await packageDigest(payload) !== sha256) fail("INTEGRITY", "Source package changed during import");
    await inspectArtifact({ ...published, root: payload }, join(artifactStore, "composition.json"));
    await directoryPath(artifactStore);
    await directoryPath(staging);
    const currentStore = await lstat(artifactStore);
    if (currentStore.dev !== storeInfo.dev || currentStore.ino !== storeInfo.ino) fail("PATH", "Artifact store changed during import");
    try { await rename(payload, destination); }
    catch (error) {
      if (!["EEXIST", "ENOTEMPTY", "EPERM"].includes((error as NodeJS.ErrnoException).code ?? "")) throw error;
      // A concurrent importer may have published the same digest. It must verify identically.
      await inspectArtifact(published, join(artifactStore, "composition.json"));
    }
    return { package: published, codeExecuted: false, selected: false };
  } finally {
    const rel = relative(artifactStore, staging);
    if (isAbsolute(rel) || !rel.startsWith(".import-") || rel.includes(sep) || resolve(artifactStore, rel) !== staging) fail("PATH", "Unsafe import cleanup path");
    await directoryPath(artifactStore);
    const currentStore = await lstat(artifactStore);
    const currentStaging = await lstat(staging).catch(error => { if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined; throw error; });
    if (currentStaging) {
      await directoryPath(staging);
      if (currentStore.dev !== storeInfo.dev || currentStore.ino !== storeInfo.ino
          || currentStaging.dev !== stagingInfo.dev || currentStaging.ino !== stagingInfo.ino
          || await realpath(artifactStore) !== storeReal || await realpath(staging) !== join(storeReal, rel)) fail("PATH", "Import directories changed; refusing recursive cleanup");
      await rm(staging, { recursive: true, force: true });
    }
  }
}
