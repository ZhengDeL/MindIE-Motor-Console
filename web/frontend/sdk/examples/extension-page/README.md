# Independent Notes page

This is an independent full React page with a package-owned typed display contract. The source imports only the public `@mindie-motor/ui-sdk`. Copy this directory to an empty external project, install the distributed SDK tarball, and build:

```sh
npm install --ignore-scripts /path/to/mindie-motor-ui-sdk-1.0.0.tgz
npm run build
python /path/to/console/web/scripts/ui_plugins.py build dist /path/to/artifacts/notes-ui.mcui
```

The SDK has not been published to npm. Installation from the explicit tarball replaces the placeholder registry version in this example's package metadata. React and React DOM remain external; all runtime React hooks and UI controls come from the host singleton.

The matching backend Notes package must be separately built, checked, imported, trusted and selected. It contributes `GET` and `POST /api/plugins/example-notes/notes` through the protected HTTP module contract. Importing this UI package alone does not create a working backend route. Failed/missing routes appear as errors and no successful write is simulated.

GET returns `{items:[{id,text,created_at}],total,limit,offset,has_more}`. POST accepts only `{text}` and returns `{item:{id,text,created_at}}` after storing it through the selected backend storage. Text is trimmed and bounded by the backend. Administrators and operators may write; viewers can read and see no write controls. Backend session, role, origin, CSRF, generation and audit checks remain mandatory.

Import `notes-ui.mcui` through Settings, select both declared slots, explicitly trust the browser code and restart the owning API. After preserving drafts and refreshing, `#extensions/example.notes-ui/notes` and its navigation item are registered without changing the Console's page switch. The page registers unsaved input, preserves native disabled behavior, guards duplicate submissions, reports unknown outcomes without automatic retry, and renders note text as React text nodes.

The package ID `example.notes-ui`, component/service slot IDs, page ID and route share one namespace. The independent backend namespace `example-notes` appears only in the API endpoint. Developer scaffolding can replace these identifiers explicitly. The custom display contract, page contract and package each declare exact versions and dependencies in the manifest. This example makes no deployment or NPU claims.
