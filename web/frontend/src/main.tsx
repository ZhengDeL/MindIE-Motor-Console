import { StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import defaultManifest from "../public/ui-default-manifest.json";
import { startUiRuntime, validateUiBootstrap, UiRuntimeProvider, UiFailureBoundary, UiBootDiagnostic, UiGenerationGuard, UiNotificationOutlet, UiSlot } from "./uiPluginSdk";
import { configureUiGeneration } from "./api";
import { PRODUCT_NAME } from "./brand";
import "./ui-kernel.css";
const DEFAULT_UI_SLOTS = defaultManifest.provides.map(contribution => contribution.slot);
const compiledPackages = [{ manifest: defaultManifest, load: () => import("./defaultUiPlugin").then(module => module.defaultUiPlugin) }];
document.title = PRODUCT_NAME;
const root = createRoot(document.getElementById("root")!);
const starting = <main className="ui-boot-status" role="status">正在校验 UI 插件装配…</main>;
root.render(starting);
async function boot() {
  try {
    const response = await fetch("/api/ui-plugins/bootstrap", { credentials: "same-origin", cache: "no-store", redirect: "error" });
    if (!response.ok) throw Error(`无法读取 UI 插件启动配置 (${response.status})`);
    const graph = validateUiBootstrap(await response.json(), DEFAULT_UI_SLOTS);
    configureUiGeneration(graph);
    const runtime = await startUiRuntime(graph, compiledPackages, DEFAULT_UI_SLOTS);
    document.documentElement.lang = Intl.getCanonicalLocales(runtime.resolve<{ locale: string }>("ui.i18n").locale)[0];
    window.addEventListener("pagehide", event => { if (!event.persisted) void runtime.stop(); });
    root.render(<StrictMode><UiFailureBoundary onFailure={() => runtime.stop()}><UiRuntimeProvider value={runtime}><Suspense fallback={starting}>
      <UiSlot name="ui.appearance"><UiSlot name="ui.theme"><UiSlot name="ui.shell" /></UiSlot><UiNotificationOutlet /><UiGenerationGuard generation={runtime.generation} /></UiSlot>
    </Suspense></UiRuntimeProvider></UiFailureBoundary></StrictMode>);
  } catch (error) { root.render(<UiBootDiagnostic message={error instanceof Error ? error.message : String(error)} />); }
}
void boot();
