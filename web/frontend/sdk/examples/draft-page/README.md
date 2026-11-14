# Independent draft workbench

This example is a complete additional React page and a package-owned formatter contract. Its source imports only `@mindie-motor/ui-sdk`; it contains no private Console source imports. The package's 1.0.0 version, SDK 1.0.0 version and formatter 2.3.0 version are separate identities.

Copy this directory to an empty project directory, then install a locally distributed SDK tarball:

```sh
npm install --ignore-scripts /path/to/mindie-motor-ui-sdk-1.0.0.tgz
npm run build
python /path/to/console/web/scripts/ui_plugins.py build dist /path/to/artifacts/draft-page.mcui
```

The build produces a self-contained `entry.mjs` plus the manifest, README and license. React is external and comes from the host's public SDK singleton. There is no registry publication prerequisite.

An authenticated Control administrator can import the `.mcui` in Settings, select both `example.drafts.page` and `example.drafts.formatter`, explicitly trust the code and restart the owning Console API. Refresh the browser after saving drafts. The “独立草稿工作台” navigation item and `#extensions/example.drafts/workbench` deep link then become available to administrators and operators. Viewers cannot render the page; backend API roles and CSRF still independently control requests.

The page calls the real `/api/services` API through `UiClient`, reads saved services and creates a Docker draft with no placements. It retains arbitrary JSON fields, marks unsaved edits, disables concurrent submissions and surfaces an unknown write result without automatically retrying. The page supplies no deployment action. Its successful draft save is evidence of configuration persistence, with no claim about Docker, Kubernetes, NPU execution or a deployed model.

`contributes.pages` adds a page without changing the host's menu or page switch. IDs and component slots belong to `example.drafts`; the path uses its reserved extension namespace. The formatter declares an explicit service method shape and a version dependency, and the page consumes the same typed slot reference used by its provider.
