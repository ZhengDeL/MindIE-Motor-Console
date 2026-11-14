import { createElement, lazy } from "react";
import manifest from "../public/ui-default-manifest.json";
import type { UiActions, UiComponentProps, UiPlugin, UiPackageManifest, UiButtonProps, UiElements, UiIcons, UiNotificationsProps } from "./uiPluginSdk";
import { UiButton, UiElement } from "./uiPluginSdk";
import { referenceUiComponents, prepareReferenceUiComponents } from "./referenceUiComponents";

/** The reference UI is a regular package. Every one of its slots is explicitly selected. */
function ReferenceComponent<P extends object>({ Default, ...props }: UiComponentProps<P>) {
  const { as: _as, ...native } = props as P & { as?: string };
  return createElement(Default, native as P);
}
const handlers = new Map<string, (action: Parameters<UiActions["invoke"]>[0]) => unknown>();
const actions: UiActions = {
  invoke(action) { if (action.disabled) return false; return (handlers.get(action.id) ?? (value => value.execute()))(action); },
  register(id, handler) {
    if (handlers.has(id)) throw Error(`Duplicate UI action: ${id}`);
    handlers.set(id, handler);
    return () => { if (handlers.get(id) === handler) handlers.delete(id); };
  },
};
export const DEFAULT_UI_MANIFEST: UiPackageManifest = manifest as UiPackageManifest;
export const DEFAULT_UI_SLOTS = manifest.provides.map(item => item.slot);
export const defaultUiPlugin: UiPlugin = {
  id: manifest.id, version: manifest.version, buildDigest: manifest.buildDigest,
  async activate(context) {
    // A cold modal must not hide its focused trigger behind a parent Suspense
    // boundary while a selected concrete implementation becomes ready.
    if (context.selectedSlots.includes("ui.theme")) await import("./DefaultUiTheme");
    await prepareReferenceUiComponents(context.selectedSlots);
  },
  providers: {
    ...Object.fromEntries(DEFAULT_UI_SLOTS.map(slot => [slot, ReferenceComponent])),
    ...referenceUiComponents,
    "ui.theme": lazy(() => import("./DefaultUiTheme")),
    "ui.button": ({ actionId, actionContext: _context, ...props }: UiButtonProps) => createElement("button", { ...props, "data-ui-action": actionId }),
    "ui.element": { render: (tag, props) => createElement(tag, props) } satisfies UiElements,
    "ui.actions": actions,
    "ui.icons": { render: (_id, props, Default) => createElement(Default, { ...props, "data-icon-theme": "lucide" }) } satisfies UiIcons,
    "ui.i18n": { locale: "zh-CN", translate: (_key: string, source: string) => source },
    "ui.notifications": ({ messages, dismiss }: UiNotificationsProps) =>
      createElement(UiElement, { as: "div", className: "ui-plugin-notifications", "aria-live": "polite" },
        messages.map(message => createElement(UiElement, { as: "div", role: "alert", key: message.id, className: "info-box" },
          message.text, createElement(UiButton, { actionId: "notifications.dismiss", type: "button", "aria-label": "关闭提示",
            className: "btn btn-text", onClick: () => dismiss(message.id) }, "关闭")))),
  },
};
