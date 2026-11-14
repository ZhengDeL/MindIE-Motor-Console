/** Public facade. Bundle this accessor; React and the running host stay external. */
import type { browserUiSdk } from "../src/uiPluginSdk";
import type { ComponentProps } from "react";
export type { UiBootstrap, UiPackageManifest, UiPlugin, UiBundledPlugin, UiRuntime, UiProvider, UiComponentProps,
  UiHostServices, UiActionInvocation, UiActions, UiButtonProps, UiI18n, UiElements } from "../src/uiPluginSdk";
export type { AuthStatus, Service, NodeInfo, Plan, DeploymentApproval } from "../src/api";
export type MotorConsoleUiSdk = typeof browserUiSdk;
export type ServiceEditorProps = ComponentProps<typeof import("../src/ServiceEditor").default>;
export type ServiceDetailProps = ComponentProps<typeof import("../src/ServiceDetail").default>;
export type LoginPageProps = ComponentProps<typeof import("../src/LoginPage").default>;
export type NodesPageProps = ComponentProps<typeof import("../src/NodesPage").default>;
export type OverviewPageProps = ComponentProps<typeof import("../src/OverviewPage").default>;
export type ModalProps = ComponentProps<typeof import("../src/ui").Modal>;
export type ButtonProps = ComponentProps<typeof import("../src/ui").Button>;
declare global {
  var MotorConsoleUiSdk: MotorConsoleUiSdk;
}
export function getUiSdk(): MotorConsoleUiSdk {
  const sdk = globalThis.MotorConsoleUiSdk;
  if (!sdk || sdk.sdkVersion !== "1.0.0") throw Error("Compatible Motor Console UI SDK is not available");
  return sdk;
}
