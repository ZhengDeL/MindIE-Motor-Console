import { build } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";
await mkdir("dist", { recursive: true });
await build({ entryPoints: ["index.ts"], outfile: "dist/entry.mjs", bundle: true,
  format: "esm", platform: "browser", target: "es2022", external: ["react", "react-dom"] });
for (const name of ["manifest.json", "README.md", "LICENSE"]) await copyFile(name, `dist/${name}`);
