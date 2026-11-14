# Process plugin SDK 1.0.0

`@mindie-motor/host-sdk` contains public process-plugin contracts and small
configuration validators. It imports Node standard-library types and no Console
application, business implementation or Python code. Its source is the canonical
contract used by the Console host; `platform/src/sdk` is a compatibility export.

Build from this directory using Node 22:

```sh
npm ci --ignore-scripts
npm run build
npm run check
npm pack --ignore-scripts
```

Install the resulting reviewed tarball in an independent plugin project. A
GitHub source release and an npm registry publication are different operations;
this project does not assume that the package is already on the npm registry.

```ts
import type { Plugin, HttpHandler } from "@mindie-motor/host-sdk";

const handler: HttpHandler = {
  async handle(_request, response) {
    response.writeHead(503, { "content-type": "text/plain; charset=utf-8" });
    response.end("Explicit maintenance mode\n");
  },
};

export default {
  activate(context) {
    context.provide("console.http.handler", handler);
  },
} satisfies Plugin;
```

Plugin authors may augment `ContractMap` in this module with their own typed
interfaces. A manifest still declares each interface and version, and the host
composition explicitly selects its provider. TypeScript types are not runtime
validation or a code sandbox. Settings use safe JSON metadata; arbitrary native
Motor documents remain opaque HTTP bytes rather than JavaScript numbers.

Lifecycle, immutable artifact identity, imports and recovery are described in the
[host guide](../../platform/README.md) in the full source checkout. The installed
SDK tarball is self-contained and does not require that checkout at runtime.
