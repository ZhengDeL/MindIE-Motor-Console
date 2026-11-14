import { copyFile, readdir } from 'node:fs/promises';
for (const entry of await readdir(new URL('../src/plugins/', import.meta.url), { withFileTypes: true })) {
  if (entry.isDirectory()) for (const name of ['manifest.json', 'package.json']) {
    await copyFile(new URL(`../src/plugins/${entry.name}/${name}`, import.meta.url), new URL(`../dist/plugins/${entry.name}/${name}`, import.meta.url));
  }
}
