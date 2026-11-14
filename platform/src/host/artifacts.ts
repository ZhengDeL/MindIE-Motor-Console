import { createHash } from "node:crypto";
import { constants, type Stats } from "node:fs";
import { lstat, open, readdir } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import type { Composition, PackageSelection, PluginManifest } from "../sdk/index.js";
import { canonical, fail, parseComposition, parseManifest } from "./schema.js";

const MAX_JSON = 2 * 1024 * 1024;
const MAX_PACKAGE = 256 * 1024 * 1024;
export interface Artifact { selection: PackageSelection; root: string; entry: string; manifest: PluginManifest }
export interface CompositionDocument { file: string; composition: Composition; generation: string }
export function digest(bytes: string | Uint8Array): string { return createHash("sha256").update(bytes).digest("hex"); }
export function generation(composition: Composition): string { return digest(canonical(composition)); }
/** Check every existing component, including Windows junctions. Never traverse linked parents. */
export async function directoryPath(directory: string, allowMissing = false): Promise<void> {
  let current = resolve(directory);
  for (;;) {
    const info = await lstat(current).catch(error => {
      if (allowMissing && (error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
      throw error;
    });
    if (info && (!info.isDirectory() || info.isSymbolicLink())) fail("PATH", "Directory path contains a link or non-directory component");
    const parent = dirname(current);
    if (parent === current) return;
    current = parent;
  }
}
export function sameFile(left: Stats, right: Stats): boolean {
  return left.dev === right.dev && left.ino === right.ino && left.size === right.size
    && left.mtimeMs === right.mtimeMs && left.ctimeMs === right.ctimeMs && left.nlink === right.nlink;
}
export async function regularFile(file: string): Promise<Stats> {
  await directoryPath(dirname(resolve(file)));
  const info = await lstat(file);
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1) fail("FILE", "Files must be regular files without links");
  return info;
}
/** Reads never allocate according to a mutable file's claimed size. */
async function streamRegular(file: string, maximum: number, begin: (info: Stats) => void, chunk: (bytes: Buffer) => void): Promise<void> {
  const before = await regularFile(file);
  if (before.size > maximum) fail("FILE", "File exceeds its byte limit");
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0));
  try {
    const opened = await handle.stat();
    if (!sameFile(before, opened)) fail("FILE", "File changed while opening");
    await directoryPath(dirname(resolve(file)));
    begin(opened);
    const buffer = Buffer.allocUnsafe(64 * 1024);
    let total = 0;
    for (;;) {
      const { bytesRead } = await handle.read(buffer, 0, Math.min(buffer.length, maximum - total + 1), null);
      if (!bytesRead) break;
      total += bytesRead;
      if (total > maximum || total > opened.size) fail("FILE", "File grew while reading");
      chunk(buffer.subarray(0, bytesRead));
    }
    if (total !== opened.size || !sameFile(opened, await handle.stat()) || !sameFile(opened, await regularFile(file))) fail("FILE", "File changed while reading");
  } finally { await handle.close(); }
}
export async function readDocumentBytes(file: string): Promise<Buffer> {
  const chunks: Buffer[] = [];
  await streamRegular(file, MAX_JSON, () => undefined, bytes => { chunks.push(Buffer.from(bytes)); });
  return Buffer.concat(chunks);
}
export async function readJson(file: string): Promise<unknown> {
  try { return JSON.parse((await readDocumentBytes(file)).toString("utf8")) as unknown; }
  catch (error) { if (error instanceof SyntaxError) fail("JSON", "Invalid JSON document"); throw error; }
}
export async function readComposition(file: string, allowUnpinned = false): Promise<CompositionDocument> {
  const absolute = resolve(file);
  const composition = parseComposition(await readJson(absolute), allowUnpinned);
  return { file: absolute, composition, generation: generation(composition) };
}
export function inside(root: string, subpath: string): string {
  const target = resolve(root, subpath);
  const rel = relative(root, target);
  if (!rel || isAbsolute(rel) || rel === ".." || rel.startsWith(`..${sep}`)) fail("PATH", "Package path escapes its root");
  return target;
}
/** v1 tree digest: sorted UTF-8 path, NUL, decimal byte length, NUL, bytes, NUL. No files are excluded. */
export async function packageDigest(directory: string): Promise<string> {
  const root = resolve(directory);
  await directoryPath(root);
  const hash = createHash("sha256");
  let total = 0;
  let files = 0;
  let entriesSeen = 0;
  async function walk(dir: string, prefix: string, depth = 0): Promise<void> {
    if (depth > 64) fail("ARTIFACT", "Package exceeds directory depth 64");
    await directoryPath(dir);
    const entries = await readdir(dir, { withFileTypes: true });
    entries.sort((a, b) => Buffer.compare(Buffer.from(a.name), Buffer.from(b.name)));
    const portableNames = new Set<string>();
    for (const entry of entries) {
      entriesSeen += 1;
      if (entriesSeen > 10000) fail("ARTIFACT", "Package exceeds 10000 filesystem entries");
      const path = resolve(dir, entry.name);
      const name = `${prefix}${entry.name}`;
      const normalizedName = entry.name.toLowerCase();
      if (/[<>:"\\|?*\x00-\x1f]/.test(entry.name) || /[. ]$/.test(entry.name) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(entry.name) || portableNames.has(normalizedName)) fail("ARTIFACT", "Package filenames must be portable and unique ignoring case");
      portableNames.add(normalizedName);
      const info = await lstat(path);
      if (info.isSymbolicLink()) fail("ARTIFACT", "Package symlinks are unsupported");
      if (info.isDirectory()) await walk(path, `${name}/`, depth + 1);
      else {
        if (!info.isFile()) fail("ARTIFACT", "Package contains a non-regular file");
        files += 1;
        if (files > 10000) fail("ARTIFACT", "Package exceeds 10000 files");
        await streamRegular(path, MAX_PACKAGE - total, opened => {
          if (!sameFile(info, opened)) fail("ARTIFACT", "Package changed while opening");
          total += opened.size;
          hash.update(name, "utf8").update("\0").update(String(opened.size)).update("\0");
        }, bytes => { hash.update(bytes); });
        hash.update("\0");
      }
    }
  }
  await walk(root, "");
  return hash.digest("hex");
}
export async function inspectArtifact(selection: PackageSelection, configFile: string, verify = true): Promise<Artifact> {
  if (/^[a-z]+:\/\//i.test(selection.root)) fail("PATH", "Package roots must be local filesystem paths");
  const root = resolve(dirname(configFile), selection.root);
  const actualDigest = await packageDigest(root);
  if (verify && actualDigest !== selection.sha256) fail("INTEGRITY", `${selection.id}: package tree SHA256 does not match selected artifact`);
  const manifest = parseManifest(await readJson(inside(root, "manifest.json")));
  if (manifest.id !== selection.id || manifest.version !== selection.version) fail("IDENTITY", `${selection.id}: manifest identity/version differs from selection`);
  const entry = inside(root, manifest.entry);
  await regularFile(entry);
  if (manifest.ui) {
    for (const asset of manifest.ui.assets) {
      const hash = createHash("sha256");
      await streamRegular(inside(root, asset.path), MAX_PACKAGE, () => undefined, bytes => { hash.update(bytes); });
      if (hash.digest("hex") !== asset.sha256) fail("INTEGRITY", `${selection.id}: UI asset SHA256 mismatch`);
    }
  }
  return { selection: { ...selection, sha256: actualDigest }, root, entry, manifest };
}
