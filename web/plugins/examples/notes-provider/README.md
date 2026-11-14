# Independent notes module

This reference implements a new business contract and a new authenticated HTTP
module. It uses only `motor_console_sdk`, FastAPI, Pydantic and the Python
standard library. It does not import Console backend classes, modify the host
catalogue, use application state, open a database driver or call Motor.

`example.notes` provides `ext.example-notes.notes` and
`ext.example-notes.http`. The HTTP product consumes the selected notes service;
the service reads the selected public `STORAGE` contract and writes through the
selected transactional `AUDIT` contract. Dependencies are
declared per contract, so a multi-product package does not create a self-cycle.

## Data and permissions

- `GET /api/plugins/example-notes/notes`: administrator, operator or viewer,
  authenticated browser session; returns a real page from the selected Store.
- `POST` at that path: administrator or operator browser session, valid Origin
  and CSRF, body exactly `{ "text": "A note" }`, returns `201` and `{ "item": ... }`.
- Text is trimmed and must contain 1 to 2000 characters; other fields fail schema
  validation. UI code renders text, never HTML.

Rows use kind `plugin:example-notes`, isolated from model-service and node rows.
The selected Store transaction writes the note and its domain audit together;
the host independently enforces request authorization, CSRF, generation and
module audit. These rows are included in the selected Store's consistent backup.
The plugin creates no external resources and has no private database to adopt.
Future changes to its data schema still need an explicit migration design.

## Build, review and select

From a complete source checkout, using the same reviewed Python environment as
the target Control:

```sh
python web/scripts/plugin_dev.py check web/plugins/examples/notes-provider
python web/scripts/plugin_dev.py pack web/plugins/examples/notes-provider \
  --output /secure/example-notes.mcprovider
python web/scripts/provider_packages.py import /secure/example-notes.mcprovider \
  --directory /opt/motor-console/providers
```

Import is static and does not execute this module. Inspect the returned metadata,
review code and licensing, then prepare a schemaVersion 2 assembly with its
`provider`, `contract_definitions`, `http_modules` and an explicit binding for
each contract. Set `trust_code` only after review. Use the import tool's
`--runtime-root` when host and container paths differ.

Validate and save with the existing expected file SHA and a fresh backup, then
restart the isolated Control. This package does not install its dependencies or
execute an installation hook. Public SDK wheels can be installed independently;
HTTP development also requires the host-compatible FastAPI/Pydantic versions.
See the [provider guide](../../../docs/PROVIDER_PLUGINS.md).

The matching [UI page](../../../frontend/sdk/examples/extension-page/README.md)
is imported independently. It appears through a page contribution, without
editing the default sidebar or route switch. Missing backend installation must
produce a visible request error, never a simulated successful note.

For an independent project, generate a fresh package instead of editing this
reference in place:

```sh
python web/scripts/plugin_dev.py init --kind provider --id acme.notes \
  --namespace acme-notes --directory /work/acme-notes
```

The generated project owns its namespace and data. No model deployment,
Agent/NPU operation or native Motor configuration is involved.
