# TypeScript plugin host

This executable Node host loads explicitly selected, local, SHA256-pinned ESM packages. Its public SDK and the reference HTTP transport/Python bridge use the same contracts as replacement packages. The host has no default business factories, package-name fallback, runtime package download, directory-wide module import, or recovery HTTP API.

The existing Python service remains the reference HTTP application. Its internal providers and the browser UI SDK are separate contracts; placing Python behind this bridge does not by itself make every Python module replaceable. Native Motor remains independent.

Use Node 22 or newer. The runtime has no third-party Node dependencies; TypeScript and Node types are locked development dependencies.

```sh
cd platform
npm ci --ignore-scripts
npm run check
npm run build
node dist/cli.js pin --input examples/python-bridge.template.json --output /private/console/composition.json
node dist/cli.js validate --config /private/console/composition.json
node dist/cli.js start --config /private/console/composition.json
```

Create the private directory first. `pin` refuses to overwrite its output, rebases local roots to absolute paths, validates the complete graph, and records exact package versions and full package-tree hashes. The bridge template expects an independently started Python HTTP application at loopback port 19081. It does not launch or claim any model deployment.

`examples/maintenance-provider` is an independent, public-contract-only HTTP handler. Pin `examples/maintenance.template.json` to select it in place of Python. It returns an explicit maintenance 503 for every request. The HTTP transport and caller do not change, and the Python package is not imported.

## Public package contract

Import authoring types from `@mindie/console-platform/sdk`. A manifest has `schemaVersion`, unique `id`, exact `version`, supported `sdk` range, relative ESM `entry`, `provides`, and `requires`. The parser rejects unknown fields and unsupported range grammar. Version ranges support exact `1.2.3`/`=1.2.3`, `~1.2.3`, and `^1.2.3`, including caret's zero-major restrictions. Versions use numeric major.minor.patch without prerelease/build labels.

```ts
import type { Plugin } from '@mindie/console-platform/sdk';
const plugin: Plugin = {
  activate(ctx) {
    const repository = ctx.consume('example.repository');
    ctx.provide('example.service', { repository });
    ctx.onStart(async signal => { signal.throwIfAborted(); });
    ctx.onStop(async () => { /* close resources owned by this package */ });
  },
};
export default plugin;
```

Augment SDK `ContractMap` to add type-safe interfaces for a package ecosystem. Types describe interfaces; every plugin must still validate its own settings and runtime inputs. Manifest declarations and consumption rights are checked at runtime. `consumeOptional` requires an explicitly optional dependency. A `many` slot requires `consumeAll`; results preserve the explicit binding order. Missing, duplicate, undeclared, incompatible and cyclic dependencies fail before any module is imported.

Composition declares contract slots (`one`, `optional`, or `many`), imported package records (`id`, exact `version`, local `root`, tree `sha256`), `selected` package IDs, explicit `bindings`, and per-package `settings`. An imported unselected artifact is read and verified but its entrypoint is never executed. A selected package may declare several contracts; each slot binding determines which provider consumers receive. No implicit last-writer override occurs.

Import a reviewed directory into a digest-named store without executing it:

```sh
node dist/cli.js import --source /reviewed/my-provider --store /private/console/artifacts
```

The command validates the bounded full tree and manifest, copies to a private staging directory, verifies the copy, and atomically publishes under the digest. Existing digest paths are verified and reused without overwriting. It returns a package descriptor to add to a candidate composition; import does not select or start the package. No npm hooks or module imports run. Digest directories are append-only by this API; filesystem permissions must prevent other writers from mutating reviewed artifacts.

An optional manifest `ui` descriptor supplies a versioned catalog and hashed local assets. It is metadata for a UI-serving plugin, not an automatic script loader. The separate browser contract and `/api/ui-plugins/bootstrap` endpoint are owned by the selected application provider. The bridge forwards these bytes unchanged and supplies the outer generation in `x-console-generation`.

## Generation and lifecycle

The generation is SHA256 of a canonical, sorted-key composition document, including settings and artifact pins. Each host instance has one immutable selection for its entire life. `PluginHost` activates in interface dependency order, checks every declared provision, then runs start hooks in that order. It publishes the running generation only after all hooks succeed. The reference HTTP listener rejects traffic until this barrier has passed. Partial failure aborts the generation and runs registered cleanup in reverse order within a total shutdown budget. Diagnostics distinguish imported, selected, starting, running, draining, stopped, and failed packages.

`ctx.onStop` must register cleanup before acquiring resources. Plugin work must cooperate with `ctx.signal`; `ctx.fail(error)` marks an owned runtime fault and drains the entire generation. Startup/cleanup budgets bound host waiting. Same-process plugins are trusted code: the host cannot forcibly cancel synchronous JavaScript, undo arbitrary side effects, or isolate malicious code. A failed external operation may have an unknown outcome and must be reconciled by its owning provider, never automatically replayed.

Package hashes include every regular file under the package root. The v1 digest walks directory entries in UTF-8 byte order and hashes each relative forward-slash path, NUL, decimal byte length, NUL, file bytes, NUL. Symlinks, special files, nonportable or case-colliding path names, trees over 256 MiB/10000 entries/depth 64, and path traversal are rejected. Keep reviewed package roots immutable and protect them with filesystem permissions. Integrity pins detect changes; they do not sandbox code or attest dependencies that code deliberately imports outside its package. The SDK/Node runtime are separately trusted dependencies.

## HTTP and Python reference providers

`console.http.handler@1.0.0` accepts Node request/response streams plus generation, request ID, deadline and abort signal. `console.http.transport@1.0.0` supplies the listener address. An alternative transport can consume the same handler contract; an alternative handler can run with the reference transport.

Clients may bind an operation to their loaded graph with `X-Console-Expected-Generation`. A nonempty mismatching value receives 409 before the selected handler runs, including for writes; the host performs no automatic retry. Omitted headers remain compatible with external API clients. Responses carry the actual `X-Console-Generation` so the UI can coordinate a refresh and preserve drafts.

The reference bridge streams raw request/response bytes with Node stream backpressure. It does not parse/re-serialize Motor JSON, large integers, SSE or cookies. Hop-by-hop headers and connection-nominated headers are stripped; request ID/generation/deadline are assigned by the transport; spoofed forwarded headers are removed. Request byte limits, upstream failures, truncation, caller disconnect and request deadlines abort the upstream HTTP connection. They cannot guarantee that an upstream Docker/Kubernetes write stopped. No retry/replay is performed. WebSocket upgrades and CONNECT tunneling are unsupported.

The bridge accepts `upstream` (HTTP(S) origin without embedded credentials), `maxRequestBytes`, and an optional managed `process`:

```json
{
  "upstream": "http://127.0.0.1:19081",
  "process": {
    "executable": "/usr/local/bin/python3",
    "args": ["-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "19081"],
    "cwd": "/opt/console/web",
    "readyPath": "/healthz",
    "startupTimeoutMs": 20000,
    "shutdownTimeoutMs": 3000
  }
}
```

Executable/cwd must be absolute; argv is fixed strings, `shell:false`, loopback is mandatory, and no package install occurs. Child environment inherits the host process so credentials can stay outside composition. Readiness is HTTP 2xx, not NPU/Kubernetes acceptance. Use a dedicated unused port. Child lifecycle ownership covers the direct child; arbitrary descendant process trees are outside this reference contract. Unexpected owned-child exit fails the host generation closed. HTTPS uses Node's normal certificate validation; do not disable TLS verification in the host environment.

## Offline switch and recovery

```sh
node dist/cli.js switch --config /private/console/composition.json --candidate /private/console/candidate.json --expected-generation CURRENT_SHA256 --backup /private/console/before-switch.json
node dist/cli.js recover --config /private/console/composition.json --candidate /private/console/before-switch.json --expected-generation CURRENT_SHA256 --backup /private/console/before-recovery.json
```

Both commands validate offline without importing package code, take a local exclusive selection lock, compare the expected generation, retain a mandatory new backup, and atomically replace next-start configuration. Existing backup paths are never overwritten. A leftover lock requires an operator to confirm no writer is running before removing it. Recovery validates retained artifacts and needs no database, application plugin or UI. It does not migrate/restore plugin data, reverse external writes or force a running host to mix generations. Stop/drain before switching stateful providers, retain old package roots and data backups, then start and inspect diagnostics. Reverting selection is separate from restoring plugin-owned data.

If the current composition itself contains invalid JSON/schema, `config-hash --config CONFIG` computes its raw file SHA256 without parsing. Use `recover` with `--expected-file-sha256 SHA256` in place of `--expected-generation` to compare those exact damaged bytes, preserve them in a new backup, and restore a fully validated candidate. This mode never executes the broken selection or silently skips candidate validation.

Validation here is local host/contract/byte transport evidence. Real NPU, Docker/Kubernetes operation acceptance, durable task reconciliation, authenticated browser workflows and cross-provider state migrations require their own checks.
