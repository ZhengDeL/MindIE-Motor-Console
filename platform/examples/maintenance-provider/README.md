# Independent Node handler example

This package replaces `console.http.handler` using the public host contract. It returns an explicit HTTP 503 maintenance response; it does not load Python, access devices, deploy a model or report a simulated deployment.

Use Node 22 or newer. `index.mjs` is ready to run without a build and has no runtime dependencies. Its JSDoc references `@mindie-motor/host-sdk` for optional editor/type checking. Install the reviewed SDK tarball in your authoring workspace if you want those types; the SDK is a separate package, and registry publication is not assumed. Do not place an authoring `node_modules` directory inside the artifact you import.

From the Console source checkout, inspect and package a separate clean directory:

```sh
python web/scripts/plugin_dev.py init --kind node --id acme.maintenance --directory /work/acme-maintenance
python web/scripts/plugin_dev.py check /work/acme-maintenance
python web/scripts/plugin_dev.py pack /work/acme-maintenance --output /private/console/artifacts
```

Build the host CLI first as described in `platform/README.md`. These commands check metadata and content integrity without executing `index.mjs`; they do not type-check or select it. Add the returned package descriptor to a reviewed candidate composition, explicitly bind its handler contract and use the host's offline validation/switch workflow. Restart during a maintenance window and inspect the actual running generation. Preserve the previous composition and immutable artifacts for recovery.

For a new implementation, keep input validation, response lifecycle, cancellation, deadlines and error reporting inside the declared handler contract. Register cleanup before acquiring resources. Plugins run as trusted code in the host process; a content digest does not sandbox code or prove its origin. Document additional dependencies and external side effects, and retain a LICENSE with each distribution.
