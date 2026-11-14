import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = fileURLToPath(new URL("..", import.meta.url));
const require = createRequire(import.meta.url);
const locations = [root, path.resolve(root, "../../web/frontend")];
const ts = require(require.resolve("typescript", { paths: locations }));
const options = { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.NodeNext,
  moduleResolution: ts.ModuleResolutionKind.NodeNext, strict: true, declaration: true,
  skipLibCheck: true, outDir: path.join(root, "dist"), rootDir: path.join(root, "src"),
  paths: Object.fromEntries(["react", "react-dom"].map(name => [name,
    [path.join(path.dirname(require.resolve(`@types/${name}/package.json`, { paths: locations })), "index.d.ts")]])) };
const program = ts.createProgram([path.join(root, "src/index.ts")], options);
const result = program.emit();
const diagnostics = ts.getPreEmitDiagnostics(program).concat(result.diagnostics);
if (diagnostics.length) {
  process.stderr.write(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCanonicalFileName: value => value, getCurrentDirectory: () => root, getNewLine: () => "\n" }));
  process.exitCode = 1;
}
