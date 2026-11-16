# Browser UI SDK 1.0.0

`@mindie-motor/ui-sdk` is an independent npm tarball built from [`../../../packages/ui-sdk`](../../../packages/ui-sdk). Install the tarball into an empty external project and use its public DTOs, props, clients and typed slot references. The Console consumes those same contracts. `index.ts` remains a compatibility re-export. The package has its own 1.0.0 version and has not been published to npm.

React and React DOM stay external; use `getUiSdk().React` from the running host. An imported `.mcui` entry must be a single self-contained ESM file. [`examples/draft-page`](examples/draft-page) builds independently, contributes a new page and its own versioned service contract, and reads/saves real Control API drafts. Extension paths use `extensions/<package-id>/<leaf>` and explicit account roles. This registers navigation and deep links without editing the host page switch.

```ts
import { getUiSdk } from "@mindie-motor/ui-sdk";
import type { UiPlugin, UiComponentProps, ServiceEditorProps } from "@mindie-motor/ui-sdk";
const sdk = getUiSdk();
const Editor = ({ Default, ...props }: UiComponentProps<ServiceEditorProps>) =>
  sdk.createElement("div", { className: "my-editor" }, sdk.createElement(Default, props));
export default sdk.definePlugin({
  id: "example.operations-ui", version: "1.0.0",
  providers: { "component.ServiceEditor": Editor },
} satisfies UiPlugin);
```

`Default` is an explicit, public composition prop supplied by component callers
equally to default and third-party providers. Root shell/theme slots receive a
children-only composition component; the minimal startup loader does not import
the default business implementation to supply a fallback. The registry resolves the selected
provider first; it never renders `Default` after a missing provider or a failed
factory. A replacement can completely implement the component using its original
controlled props and callbacks, without using `Default`.

## Contracts and selection

The complete default contract manifest is
[`../public/ui-default-manifest.json`](../public/ui-default-manifest.json).
Every required slot has exactly one explicit binding. Package IDs identify
artifacts; slot IDs identify contracts. A package can provide many slots.

The browser kernel requires only the seven `UI_KERNEL_SLOTS` exported by the
public SDK: `ui.appearance`, `ui.theme`, `ui.shell`, `ui.i18n`,
`ui.notifications`, `ui.button` and `ui.actions`. Reference page and component
slots are not kernel requirements. Existing selections inherit the reference
bindings by default, so replacing a single component remains compatible.

For an independent shell, turn off **继承参考界面的默认槽位** in the package
selection dialog, or send `inherit_defaults: false` with the normal
`POST /api/ui-plugins/selection` request. Supply explicit bindings for the seven
kernel slots and all dependencies of your selected packages. The request still
requires an expected revision, administrator session, CSRF and trusted-code
confirmation. A successful selection activates only at the next control-process
start. The unselected reference package contributes no pages and loads neither
App nor its global styles. Restoring the default selection enables inheritance
again. Explicit reuse of `console.ui.default` requires its full reference
catalogue to remain bound; the host rejects an incomplete reference selection
instead of silently filling slots.

| Slots | Provider contract |
| --- | --- |
| `ui.shell` | React component replacing the full default application; use `sdk.client.readSession()` for verified metadata, session and CSRF initialization, and implement navigation/draft coordination. |
| `page.login` | React component with `status` and `onAuthenticated`; all requests use the public CSRF-aware client. |
| `page.*` | Full page React component, with `page`, `catalog`, `children`, `Default`. `sdk.useHost()` exposes current services, nodes, auth, metadata, navigation, notifications and the existing create/edit/open/service-action workflows. |
| `component.*` | React component receiving the original component props and `Default`; includes complete editors, dialogs, forms, tables, command palette, notifications and settings. |
| `tab.service.*` | React component receiving a controlled `model`: service, nodes, selected tab/setter, prompt/setter, token limit/setter, inference operation/result, configuration tab/setter, revisions, logs, observations and original edit/action callbacks. Existing permission and runtime conditions gate the slot. |
| `tab.deploy.0` through `tab.deploy.3` | React component with controlled `model`: original service, nodes, step/setter, runtime/setter, name/setter, raw user/env/extra JSON strings and setters, placements/setter, dirty/setter, plan, saved service and busy state. JSON strings preserve the existing validation boundary. |
| `layout.sidebar`, `layout.topbar`, `layout.workspace`, `layout.panel` | React component with the original DOM props, refs, handlers, children, `as` tag and `Default`; preserve focus/ARIA/ref behavior. |
| `ui.button` | React component with native button props/ref and `actionId`; render the passed handler and honor `disabled`, submit semantics and accessible names. All application buttons, including menus/tabs, pass through this slot. |
| `ui.element` | `{ render(tag, props) }` for native controls and structure. Preserve DOM semantics, refs and controlled values. All default application DOM elements use it; the minimal failed-boot diagnostic remains host-owned. |
| `ui.actions` | `{ invoke(invocation), register?(id, handler) }`. An invocation contains `id`, event, principal/page/context, disabled state and the original `execute` callback. Call `execute` at most once to continue; leaving it uncalled suppresses activation/native submission. Policies do not replace backend authorization. |
| `ui.icons` | `{ render(semanticId, svgProps, DefaultIcon) }`. Existing and navigation icons use semantic IDs, even formerly direct Lucide imports. Preserve size, ref and accessible labeling. |
| `ui.appearance`, `ui.theme` | React providers receiving `children` and `Default`. The default appearance provider keeps persisted mode/density/motion/width settings; a replacement theme owns its complete design stylesheet/tokens, loaded from trusted package assets. Default global styles are loaded only by the selected default theme. |
| `ui.notifications` | React component receiving `messages` and `dismiss(id)`, used by `sdk.notify` and error feedback outside the default shell. |
| `ui.i18n` | `{ locale, translate(key, source, values?) }`. Source text is the default key; text nodes, dynamic native element children, labels/placeholders and selected component text props consult it. Raw code/JSON editor content and controlled input values remain data. |

Semantic public action IDs and context fields are exported as `sdk.UI_ACTIONS`;
[`../public/ui-action-catalog.json`](../public/ui-action-catalog.json) lists all
call sites and descriptions. IDs such as `services.deploy`, `services.plan`,
`nodes.probe`, `auth.login`, `plugins.ui.import` and `plugins.ui.select` describe
the operation; component-local IDs identify presentation controls. Multiple
call sites may intentionally share an action ID. Resource actions carry
`service`/`node` when available, plus principal/page. Do not infer permissions
from the label or DOM text. `sdk.useAction(id)` exposes the same selected action
provider to custom workflow components.

## Lifecycle, assets and API generation

Optional `activate(ctx)` is awaited in dependency order. `ctx` exposes
`generation`, `signal`, readonly `selectedSlots`, `consume(slot)`, `onStop(cleanup)`, `assetUrl(member)` and
`loadAsset(member)`. Register owned subscriptions/timers and stylesheet removal
with `onStop`; cleanups run in reverse order with a time bound. Activation has a
15-second deadline. Browser code shares the host's trust and execution context;
these controls do not isolate arbitrary code.

`ctx.consume` accepts only dependencies listed in the active package's
`requires`, including a self-provided service consumed during activation.
Optional dependencies may remain unbound at startup; consuming an unbound slot
throws an explicit error. Available dependencies must match their declared
versions. Render-time `sdk.useSlot` resolves the
selected graph and is a separate API. Register cleanup before acquiring owned
resources; registration closes when stopping begins. Repeated `stop()` calls
share the same completion promise. Late activation cannot reopen cleanup
registration after a timeout, and arbitrary JavaScript is not forcibly canceled.

`assetUrl("images/brand.png")` returns a same-origin path under this package's
immutable archive digest. It rejects absolute/traversal paths. `loadAsset`
also checks the per-file SHA-256 from the bootstrap asset inventory with
WebCrypto. Use HTTPS or localhost; browsers without `crypto.subtle` cannot load
independent UI code. Do not use `import.meta.url` relative to the verified Blob
entry, bare imports, remote scripts, HTML injection or arbitrary module URLs.

`sdk.client` exposes the existing authenticated client, `readSession`, strict
JSON parsing, download and mutation-intent helpers. It retains CSRF, backend
product identity, unsafe-number checks, idempotency keys and unknown-result
semantics. JSON configuration retains arbitrary nested Motor fields; unsafe
integers are explicitly rejected by the browser editor rather than rounded.
Use controlled callbacks supplied to editors for existing deployment/job
recovery behavior. A UI plugin must never report deployment success from a
render, mock, queued job or HTTP status alone.

The graph is immutable for a page lifetime. Requests pin the selected host and
provider generations, and writes carry the UI generation. A mismatch freezes
new writes, keeps current drafts and displays a refresh notice. No factory,
dependency, version, digest or required-slot error silently selects the default
UI. `sdk.useUnsavedChanges(dirty)` lets replacement workflows participate in
the existing unload/navigation protection.

## Import and rollback

A `.mcui` ZIP contains `manifest.json`, nonempty `README.md`, `LICENSE`, and one
root `entrypoint` `.mjs`/`.js`; optional images, CSS and fonts stay under safe
relative paths. See the source reference pack below. No package is downloaded
or installed from the Internet. The UI package center imports bytes, shows
the archive digest and contracts, then separately records trusted slot
selection using an expected revision. Importing an unselected package executes
no code. Selection takes effect after the control process restarts; save drafts
and reload browsers to use the new generation. Restoring default bindings or a
prior digest is also a next-start selection and does not undo external effects.

`startUiRuntime` also accepts compiled `UiPlugin` packages or lazy descriptors
`{ manifest, load: () => import(...).then(module => module.plugin) }`
for build-time integrations; use the same manifest, identity, contract and
binding checks. Descriptors import code only when selected. Default components
are concrete reference providers whose selected implementations are prepared
during activation, so first-open dialogs preserve their focused trigger. The
package itself is loaded lazily; an
entirely unselected default package loads neither App nor its global styles.
Runtime import is the path that avoids rebuilding the host.

[`examples/operations-ui`](examples/operations-ui) contains source for a real
service page and an independent raw-configuration deployment editor. Its API
calls are actual operations in the selected environment; validate on an isolated
control plane before selecting it for production. It does not replace the
authentication, executor or authorization providers.
