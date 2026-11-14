# @mindie-motor/ui-sdk

Independent public TypeScript contracts for trusted Console browser packages. Version 1.0.0 is independent of the Console release. This package is distributed as an npm tarball; it has not been published to the npm registry.

```sh
cd packages/ui-sdk
npm ci --ignore-scripts
npm run build
npm pack --pack-destination /path/to/artifacts
```

The emitted JavaScript contains small accessors and contract helpers. Domain DTOs, component props, `UiClient`, `UiHostServices`, `BuiltinUiContracts`, lifecycle contracts and page declarations are defined in this package. They do not import application source or derive their public types from an implementation. The reference frontend imports these same definitions; its old `sdk/index.ts` is a compatibility re-export.

Install the tarball in an independent project. Use `getUiSdk().React`, `createElement`, UI controls, hooks and `client` from the running host. React and React DOM are peer dependencies and must stay external to browser plugin bundles. A second React instance is unsupported. Read `web/frontend/sdk/examples/draft-page` in the source distribution for a complete independent project.

```ts
import { defineSlot, provideSlot, definePlugin, getUiSdk } from "@mindie-motor/ui-sdk";
const formatter = defineSlot<{ format(value: string): string }>("acme.ui.formatter", "2.0.0");
export default definePlugin({ id: "acme.ui", version: "1.0.0",
  providers: provideSlot(formatter, { format: value => value.toUpperCase() }) });
// In a component: getUiSdk().useSlot(formatter).format("example");
```

Declare the matching slot version, service kind and method names in `manifest.json`. Typed references check developer code and verify the selected contract version at runtime. Registry module values cross a dynamic boundary as `unknown`; selected declarations are checked before activation. `BuiltinUiContracts` supplies typed references for standard services and key reference component props. The legacy string generic overload supports old host code; new custom slots should use `defineSlot<T>`.

To add a complete React page, contribute a component slot and `contributes.pages` with a package-prefixed ID, explicit account roles and a route `extensions/<package-id>/<leaf>`. `UiPageProps` contains its declaration. The host registers navigation, command search and deep links; it checks role visibility before rendering. API authorization remains enforced by the backend. UI declarations grant no API permissions.

The running host pins artifact digests and a boot generation, loads only selected providers, validates dependencies and runs lifecycle cleanup. Selection takes effect on the next owning API start; browsers preserve their generation and unsaved drafts until an explicit reload. Components should call `useUnsavedChanges` and preserve native disabled, keyboard, focus and ref behavior. Plugins share the authenticated browser session and are trusted code, without a browser security sandbox.
