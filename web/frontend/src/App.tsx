import { lazy, useCallback, useEffect, useRef, useState } from "react";
import { Bell, Home, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import DeferredPanel from "./DeferredPanel";
import { DATA_CHANGED_EVENT, SESSION_EXPIRED_EVENT } from "./brand";
import PlatformBrand from "./PlatformBrand";
import { applyDocumentBranding, captureBrandingReadGeneration, publishBranding, useBranding } from "./brandingState";
import { usePollingQuery } from "./usePollingQuery";
import { useRefreshPreference } from "./refreshPreferences";
import { discardNavigationDrafts, hasUnsavedChanges } from "./unsavedChanges";
import Pagination from "./Pagination";
import { isConsolePage, matchesServiceStatus, titles, visibleNavigation } from "./consoleNavigation";
import type { ConsolePage } from "./consoleNavigation";
import { PluginContext, pageEnabled, parsePluginCatalog, pluginEnabled, renderPageContribution,
  runtimeEnabled, runtimeReadOnlyReason, usePluginEnabled, useRuntimeAccess, type ConsolePageViews, type PluginCatalog } from "./consolePlugins";
import { parsePluginPageHash, pluginPageHash, pluginPagePermitted, type PluginAccess } from "./communityPlugins";
import CommunityPluginPage from "./CommunityPluginPage";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  AlertCircle,
  Activity,
  Box,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Download,
  FileJson,
  Layers3,
  LogOut,
  Menu,
  MoreHorizontal,
  Play,
  Plus,
  RefreshCw,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  Square,
  TerminalSquare,
  Trash2,
  UserRound,
  Upload,
  X,
} from "./icons";
import {
  api,
  createMutationIntent,
  download,
  executionReadOnlyReason,
  isUnsubmittedDraft,
  post,
  roleLabel,
  setCsrfToken,
  validateJsonNumbers,
  statusName,
} from "./api";
import type {
  AuthStatus,
  DeploymentApproval,
  Job,
  Log,
  Meta,
  NodeInfo,
  Overview,
  Plan,
  Service,
  Template,
  TemplateInput,
} from "./api";
import {
  Badge,
  Button,
  Confirm,
  Empty,
  ErrorBox,
  InfoBox,
  Modal,
  SearchInput,
  Select,
  Spinner,
} from "./ui";
import ManifestReview from "./ManifestReview";
import ScaleDialog from "./ScaleDialog";
import UndeployDialog from "./UndeployDialog";
import LoginPage from "./LoginPage";
import SystemLogs from "./SystemLogs";
import CapabilitiesPage from "./CapabilitiesPage";
import TemplatesPage, {
  serviceTemplateInput,
  TemplateEditor,
} from "./TemplatesPage";
import SettingsPage from "./SettingsPage";
import { readCount, readNotice, type ReadState } from "./readState";
import { RuntimeLogFilters, RuntimeLogLine, logPath, matchesLog, type LogFilters, type LogResponse } from "./RuntimeLogs";
import GatewaysPage from "./GatewaysPage";
import NodesPage, { NodeModal } from "./NodesPage";
import JobProgress from "./JobProgress";
import "./operations.css";
import "./app-chrome.css";
import { useNpuResources } from "./useNpuResources";
import { ServiceJobIntentStore } from "./serviceJobIntents";
import { knownText } from "./resourcePresentation";
import { isResourceInteger } from "./resourceObservation";
import { documentationUrl } from "./documentation";
import { GRAFANA_CONFIGURATION_SAVED, grafanaDashboardHref } from "./integrationLinks";
import type { GrafanaNavigationConfiguration } from "./integrationLinks";

const AccountsPage = lazy(() => import("./Accounts"));
const PasswordDialog = lazy(() => import("./Accounts").then((module) => ({ default: module.PasswordDialog })));
const SystemMaintenance = lazy(() => import("./SystemMaintenance"));
const AgentRolloutDialog = lazy(() => import("./AgentRolloutDialog"));
const CommandPalette = lazy(() => import("./CommandPalette"));
const OverviewPage = lazy(() => import("./OverviewPage"));
const ImagesPage = lazy(() => import("./ImagesPage"));
const ServiceEditor = lazy(() => import("./ServiceEditor"));
const ServiceDetail = lazy(() => import("./ServiceDetail"));

type Page = ConsolePage;
function Brand({ compact = false }: { compact?: boolean }) {
  return <PlatformBrand compact={compact} />;
}
function RuntimeTag({ value }: { value: string }) {
  return (
    <span className={`runtime-tag ${value === "kubernetes" ? "k8s" : ""}`}>
      {value === "kubernetes" ? <Layers3 size={12} /> : <Box size={12} />}
      {value === "kubernetes" ? "Kubernetes" : "Docker"}
    </span>
  );
}

function ServiceMenu({
  service,
  canOperate,
  onEdit,
  onAction,
  onExport,
}: {
  service: Service;
  canOperate: boolean;
  onEdit: () => void;
  onAction: (action: string) => void;
  onExport: () => void;
}) {
  const templatesEnabled = usePluginEnabled("templates");
  const runtimeAccess = useRuntimeAccess(service.runtime);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const anchor = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const node = menu.current;
    const items = () => Array.from(node?.querySelectorAll<HTMLButtonElement>('button[role="menuitem"]:not(:disabled)') ?? []);
    const frame = requestAnimationFrame(() => items()[0]?.focus({ preventScroll: true }));
    const close = () => setOpen(false);
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        anchor.current?.focus();
      } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        const available = items();
        if (!available.length) return;
        const index = available.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? available.length - 1 :
          (index + (event.key === "ArrowDown" ? 1 : -1) + available.length) % available.length;
        available[next]?.focus({ preventScroll: true });
      } else if (event.key === "Tab") {
        close(); anchor.current?.focus({ preventScroll: true });
      }
    };
    const outside = (event: FocusEvent) => {
      const target = event.target as Node;
      if (!node?.contains(target) && !anchor.current?.contains(target)) close();
    };
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", key);
    document.addEventListener("focusin", outside);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", key);
      document.removeEventListener("focusin", outside);
      if (node?.contains(document.activeElement)) anchor.current?.focus({ preventScroll: true });
    };
  }, [open]);
  const choose = (action: () => void) => {
    setOpen(false);
    anchor.current?.focus({ preventScroll: true });
    action();
  };
  return (
    <>
      <button
        ref={anchor}
        className="icon-btn"
        aria-label={`${service.name} 更多操作`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          const rect = anchor.current!.getBoundingClientRect();
          setPosition({
            left: Math.max(
              10,
              Math.min(window.innerWidth - 166, rect.right - 152),
            ),
            top:
              window.innerHeight - rect.bottom > 215
                ? rect.bottom + 5
                : Math.max(10, rect.top - 207),
          });
          setOpen(!open);
        }}
      >
        <MoreHorizontal size={17} />
      </button>
      {open &&
        createPortal(
          <>
            <button
              className="menu-dismiss"
              aria-label="关闭更多操作"
              onClick={() => setOpen(false)}
            />
            <div
              ref={menu}
              className="floating-actions"
              role="menu"
              aria-label={`${service.name} 服务操作`}
              style={position}
            >
              <button
                role="menuitem"
                onClick={() => choose(onEdit)}
                disabled={
                  !canOperate || !runtimeAccess.enabled || !["draft", "stopped"].includes(service.status)
                }
              >
                <Settings2 size={13} />
                编辑配置
              </button>
              <button
                role="menuitem"
                disabled={!canOperate}
                onClick={() => choose(onExport)}
              >
                <Download size={13} />
                导出配置
              </button>
              {templatesEnabled && <button
                role="menuitem"
                disabled={!canOperate || service.config_redacted}
                onClick={() => choose(() => onAction("template"))}
              >
                <FileJson size={13} />
                保存为模板
              </button>}
              <button
                role="menuitem"
                disabled={
                  !canOperate || !runtimeAccess.enabled ||
                  service.needs_rebuild ||
                  [
                    "draft",
                    "scaling",
                    "removing",
                    "stopping",
                    "restarting",
                    "deploying",
                  ].includes(service.status)
                }
                title={service.needs_rebuild ? "请先重建并应用配置" : undefined}
                onClick={() => choose(() => onAction("restart"))}
              >
                <RefreshCw size={13} />
                重启服务
              </button>
              <button
                role="menuitem"
                className="danger-text"
                disabled={
                  !canOperate || !runtimeAccess.enabled || !["draft", "stopped"].includes(service.status)
                }
                onClick={() => choose(() => onAction("delete"))}
              >
                <Trash2 size={13} />
                删除服务
              </button>
            </div>
          </>,
          document.body,
        )}
    </>
  );
}
export default function App() {
  const branding = useBranding();
  useEffect(() => { applyDocumentBranding(branding); }, [branding]);
  const [page, setPage] = useState<Page>(() => {
    const p = window.location.hash.slice(1);
    return isConsolePage(p) ? p : parsePluginPageHash(p) ? "settings" : "overview";
  });
  const [externalPage, setExternalPage] = useState<string | null>(() => {
    const hash = window.location.hash.slice(1);
    return parsePluginPageHash(hash) ? hash : null;
  });
  const currentExternalPage = useRef(externalPage);
  const currentPage = useRef(page);
  const mainRef = useRef<HTMLElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const mobileAnchor = useRef<HTMLButtonElement>(null);
  const userAnchor = useRef<HTMLButtonElement>(null);
  const activityAnchor = useRef<HTMLButtonElement>(null);
  const userPopover = useRef<HTMLDivElement>(null);
  const principalKey = useRef<string | null>(null);
  const resourceErrors = useRef<Record<string, string>>({});
  const resourceReadTimes = useRef<Record<string, string>>({});
  const [loadedResources, setLoadedResources] = useState<Record<string, boolean>>({});
  const [refreshInterval] = useRefreshPreference();
  const [commandOpen, setCommandOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return localStorage.getItem("qinghuan-platform-spatial-navigation-collapsed") === "true"; }
    catch { return false; }
  });
  useEffect(() => {
    const update = () => { document.documentElement.dataset.motionPaused = String(document.hidden); };
    update();
    document.addEventListener("visibilitychange", update);
    return () => { document.removeEventListener("visibilitychange", update); delete document.documentElement.dataset.motionPaused; };
  }, []);
  const [syncError, setSyncError] = useState("");
  const [productError, setProductError] = useState("");
  const [sessionRevision, setSessionRevision] = useState(0);
  const [meta, setMeta] = useState<Meta | null>(null);
  const docsUrl = documentationUrl(meta?.documentation_url);
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [grafanaNavigation, setGrafanaNavigation] = useState<{ identity: string | null; revision: number; href: string } | null>(null);
  const [pluginState, setPluginState] = useState<{ identity: string | null; catalog: PluginCatalog } | null>(null);
  const [pluginError, setPluginError] = useState("");
  const pluginCatalog = pluginState?.identity === principalKey.current ? pluginState?.catalog ?? null : null;
  const pluginCatalogRef = useRef(pluginCatalog);
  pluginCatalogRef.current = pluginCatalog;
  const [initialLoading, setInitialLoading] = useState(true);
  const [lastSyncedAt, setLastSyncedAt] = useState("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [toastTone, setToastTone] = useState("info");
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);
  const [serviceListPage, setServiceListPage] = useState(1);
  const [servicePageSize, setServicePageSize] = useState(10);
  const [detailId, setDetailId] = useState<string | null>(null);
  // Bind stop recovery to one view visit, including navigation away and back.
  const stopView = useRef({ page, detailId, revision: 0 });
  if (stopView.current.page !== page || stopView.current.detailId !== detailId)
    stopView.current = { page, detailId, revision: stopView.current.revision + 1 };
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [runtimeFilter, setRuntimeFilter] = useState("all");
  const [editor, setEditorState] = useState<Service | "new" | null>(null);
  const editRequest = useRef(0);
  const setEditor = (value: Service | "new" | null) => {
    editRequest.current++;
    setEditorState(value);
  };
  const [initialTemplate, setInitialTemplate] = useState<Template | null>(null);
  const [templateSeed, setTemplateSeed] = useState<TemplateInput | null>(null);
  const [nodeModal, setNodeModal] = useState(false);
  const [agentRolloutView, setAgentRolloutView] = useState<"new" | "history" | null>(null);
  const [scaleService, setScaleService] = useState<Service | null>(null);
  const [undeployService, setUndeployService] = useState<Service | null>(null);
  const [confirm, setConfirm] = useState<{
    title: string;
    description: ReactNode;
    action: () => Promise<void>;
    text: string;
    isCurrent?: () => boolean;
    runtime?: string;
  } | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const confirmFlight = useRef<object | null>(null);
  const [confirmUncertain, setConfirmUncertain] = useState(false);
  const [stopCheckingId, setStopCheckingId] = useState("");
  const stopJobRequest = useRef<{ controller: AbortController; viewRevision: number } | null>(null);
  useEffect(() => {
    if (stopJobRequest.current && (stopJobRequest.current.viewRevision !== stopView.current.revision || editor !== null)) {
      stopJobRequest.current.controller.abort(); stopJobRequest.current = null; setStopCheckingId("");
    }
    setConfirm((current) => current?.isCurrent && !current.isCurrent() ? null : current);
  }, [page, detailId, editor]);
  const [confirmError, setConfirmError] = useState("");
  const [planError, setPlanError] = useState("");
  const [planDialog, setPlanDialog] = useState<{
    service: Service;
    plan: Plan;
  } | null>(null);
  const [planExtraApproved, setPlanExtraApproved] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [jobOpen, setJobOpen] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [headerPanel, setHeaderPanel] = useState("");
  const npuResources = useNpuResources(nodes, {
    active: !!auth?.authenticated && meta?.mode !== "demo" && !logoutBusy && !detailId && pluginEnabled(pluginCatalog, "nodes") && pageEnabled(pluginCatalog, page) && ["overview", "nodes"].includes(page),
    sessionKey: JSON.stringify([auth?.user?.id, auth?.user?.role, auth?.csrf_token, meta?.mode]),
    contextKey: page,
  });
  const clearSessionViews = useCallback(() => {
    stopJobRequest.current?.controller.abort(); stopJobRequest.current = null; setStopCheckingId("");
    setEditor(null);
    setInitialTemplate(null);
    setTemplateSeed(null);
    setNodeModal(false);
    setAgentRolloutView(null);
    setScaleService(null);
    setUndeployService(null);
    setConfirm(null);
    setPlanDialog(null);
    setPlanExtraApproved(false);
    setJob(null);
    setJobOpen(false);
    setPasswordOpen(false);
    setHeaderPanel("");
    setCommandOpen(false);
    setMobileNav(false);
  }, []);
  const notify = useCallback((m: string, tone = "info") => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToastTone(tone);
    setToast(m);
    toastTimer.current = setTimeout(() => { toastTimer.current = null; setToast(""); }, 4200);
  }, []);
  const clearSessionData = useCallback(() => {
    if (toastTimer.current) { clearTimeout(toastTimer.current); toastTimer.current = null; }
    resourceErrors.current = {}; resourceReadTimes.current = {}; setLoadedResources({});
    clearSessionViews();
    setSessionRevision((revision) => revision + 1);
    setServices([]); setNodes([]); setOverview(null); setDetailId(null);
    setGrafanaNavigation(null);
    setPluginState(null); setPluginError("");
    setSearch(""); setFilter("all"); setRuntimeFilter("all");
    setLastSyncedAt(""); setError(""); setSyncError("");
    confirmFlight.current = null;
    setActionBusy(false); setConfirmUncertain(false); setConfirmError(""); setPlanError(""); setToast("");
  }, [clearSessionViews]);
  const reportQuery = useCallback((key: string, failure = "") => {
    if (failure) resourceErrors.current[key] = failure;
    else delete resourceErrors.current[key];
    const message = Object.values(resourceErrors.current).join("；");
    setSyncError(message);
    if (!failure && key !== "session") {
      setLoadedResources((previous) => ({ ...previous, [key]: true }));
      resourceReadTimes.current[key] = new Date().toISOString();
      const complete = ["overview", "services", "nodes"].map((resource) => resourceReadTimes.current[resource]);
      if (complete.every(Boolean)) setLastSyncedAt(complete.sort()[0]);
    }
  }, []);
  const sessionQuery = usePollingQuery("session", async (signal) => {
    const brandingReadGeneration = captureBrandingReadGeneration();
    const metadata = await api<Meta>("/meta", { signal });
    const session = await api<AuthStatus>("/auth/status", { signal });
    return { metadata, session, brandingReadGeneration };
  }, {
    enabled: !logoutBusy,
    intervalMs: 20_000,
    onSuccess: ({ metadata, session, brandingReadGeneration }) => {
      publishBranding(metadata.branding, brandingReadGeneration);
      const identity = JSON.stringify([metadata.mode, session.authenticated, session.user?.id, session.user?.role, session.csrf_token]);
      if (identity !== principalKey.current) {
        principalKey.current = identity;
        resourceErrors.current = {};
        clearSessionData();
      }
      setProductError("");
      setMeta(metadata); setAuth(session); setCsrfToken(session.csrf_token);
      setInitialLoading(false);
      reportQuery("session");
    },
    onError: (failure) => {
      if ("productMismatch" in failure) {
        principalKey.current = null;
        clearSessionData();
        setAuth(null); setMeta(null); setCsrfToken(null);
        setProductError(`无法连接控制台：${failure.message}`);
      }
      reportQuery("session", `会话检查失败：${failure.message}`);
      setInitialLoading(false);
    },
  });
  const queryIdentity = principalKey.current;
  const readResources = !!auth?.authenticated && !logoutBusy && !productError;
  const pluginQuery = usePollingQuery(`plugins:${queryIdentity}`, async (signal) => ({
    identity: queryIdentity, catalog: parsePluginCatalog(await api<unknown>("/plugins", { signal })),
  }), {
    enabled: readResources, intervalMs: 60_000,
    onSuccess: ({ identity, catalog }) => {
      if (identity !== principalKey.current) return;
      setPluginState({ identity, catalog }); setPluginError("");
    },
    onError: (failure) => {
      if (queryIdentity !== principalKey.current) return;
      setPluginState(null);
      setPluginError((failure as Error & { status?: number }).status === 404
        ? "当前后端未提供插件清单接口。请部署与此前端配套的插件宿主版本，再重新读取。"
        : failure.message);
    },
  });
  const acceptGrafanaNavigation = useCallback((value: GrafanaNavigationConfiguration) => {
    const identity = principalKey.current;
    if (!identity || !Number.isSafeInteger(value?.revision) || value.revision < 0) return;
    setGrafanaNavigation((previous) => previous?.identity === identity && previous.revision > value.revision
      ? previous : { identity, revision: value.revision, href: grafanaDashboardHref(value) });
  }, []);
  const grafanaNavigationQuery = usePollingQuery(`grafana-navigation:${queryIdentity}`, async (signal) => ({
    identity: queryIdentity, value: await api<GrafanaNavigationConfiguration>("/extensions/grafana", { signal }),
  }), {
    enabled: readResources && pluginEnabled(pluginCatalog, "grafana"), intervalMs: 60_000, invalidateOn: ["/extensions/grafana"],
    onSuccess: ({ identity, value }) => { if (identity === principalKey.current) acceptGrafanaNavigation(value); },
    onError: () => { /* A failed read cannot create an unconfigured destination. */ },
  });
  useEffect(() => {
    const saved = (event: Event) => acceptGrafanaNavigation((event as CustomEvent<GrafanaNavigationConfiguration>).detail);
    window.addEventListener(GRAFANA_CONFIGURATION_SAVED, saved);
    return () => window.removeEventListener(GRAFANA_CONFIGURATION_SAVED, saved);
  }, [acceptGrafanaNavigation]);
  const grafanaHref = readResources && pluginEnabled(pluginCatalog, "grafana") && grafanaNavigation?.identity === queryIdentity ? grafanaNavigation.href : "";
  const overviewQuery = usePollingQuery(`overview:${queryIdentity}`, async (signal) => ({
    identity: queryIdentity, value: await api<Overview>("/overview", { signal }),
  }), {
    enabled: readResources && pluginEnabled(pluginCatalog, "overview"), intervalMs: refreshInterval,
    onSuccess: ({ identity, value }) => {
      if (identity !== principalKey.current) return;
      setOverview(value); reportQuery("overview");
    },
    onError: (failure) => { if (queryIdentity === principalKey.current) reportQuery("overview", `概览：${failure.message}`); },
  });
  const servicesQuery = usePollingQuery(`services:${queryIdentity}`, async (signal) => ({
    identity: queryIdentity, value: await api<{ items: Service[] }>("/services", { signal }),
  }), {
    enabled: readResources && pluginEnabled(pluginCatalog, "services"), intervalMs: refreshInterval,
    onSuccess: ({ identity, value }) => {
      if (identity !== principalKey.current) return;
      setServices(value.items); reportQuery("services");
    },
    onError: (failure) => { if (queryIdentity === principalKey.current) reportQuery("services", `服务列表：${failure.message}`); },
  });
  const nodesQuery = usePollingQuery(`nodes:${queryIdentity}`, async (signal) => ({
    identity: queryIdentity, value: await api<{ items: NodeInfo[] }>("/nodes", { signal }),
  }), {
    enabled: readResources && pluginEnabled(pluginCatalog, "nodes"), intervalMs: refreshInterval,
    onSuccess: ({ identity, value }) => {
      if (identity !== principalKey.current) return;
      setNodes(value.items); reportQuery("nodes");
    },
    onError: (failure) => { if (queryIdentity === principalKey.current) reportQuery("nodes", `节点列表：${failure.message}`); },
  });
  const refreshing = sessionQuery.refreshing || overviewQuery.refreshing || servicesQuery.refreshing || nodesQuery.refreshing;
  const refresh = useCallback(async (_silent = false) => {
    await sessionQuery.refresh();
    await pluginQuery.refresh();
    await Promise.all([overviewQuery.refresh(), servicesQuery.refresh(), nodesQuery.refresh(), grafanaNavigationQuery.refresh()]);
  }, [sessionQuery.refresh, pluginQuery.refresh, overviewQuery.refresh, servicesQuery.refresh, nodesQuery.refresh, grafanaNavigationQuery.refresh]);
  useEffect(() => { clearSessionViews(); }, [auth?.user?.id, auth?.user?.role, auth?.authenticated, clearSessionViews]);
  useEffect(() => {
    // Browser state from the separate HX edition is deliberately not imported.
    sessionStorage.removeItem("qinghuan-platform-token");
  }, []);
  useEffect(() => {
    const expired = () => {
      overviewQuery.cancel(); servicesQuery.cancel(); nodesQuery.cancel();
      principalKey.current = null; resourceErrors.current = {};
      clearSessionData(); setCsrfToken("");
      setAuth((previous) => previous ? { ...previous, authenticated: false, user: null, csrf_token: null } : previous);
      sessionQuery.invalidate();
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
  }, [clearSessionData, sessionQuery.invalidate, overviewQuery.cancel, servicesQuery.cancel, nodesQuery.cancel]);
  useEffect(() => {
    const changed = (event: Event) => {
      const path = (event as CustomEvent<{ path?: string }>).detail?.path ?? "";
      if (path.startsWith("/auth/")) sessionQuery.invalidate();
      if (path.startsWith("/services") || path.startsWith("/jobs")) {
        servicesQuery.invalidate(); overviewQuery.invalidate();
      }
      if (path.startsWith("/nodes") || path.startsWith("/agent-rollouts")) {
        nodesQuery.invalidate(); overviewQuery.invalidate();
      }
    };
    window.addEventListener(DATA_CHANGED_EVENT, changed);
    return () => window.removeEventListener(DATA_CHANGED_EVENT, changed);
  }, [sessionQuery.invalidate, servicesQuery.invalidate, overviewQuery.invalidate, nodesQuery.invalidate]);
  useEffect(() => { setConfirmError(""); setConfirmUncertain(false); }, [confirm]);
  const canOperate =
    !logoutBusy && principalKey.current !== null && (meta?.mode === "demo" ||
    ["admin", "operator"].includes(auth?.user?.role ?? ""));
  const isAdmin = principalKey.current !== null && auth?.user?.role === "admin";
  const canAdminister = !logoutBusy && principalKey.current !== null && (isAdmin || meta?.mode === "demo");
  const canCreateServices = canOperate && (runtimeEnabled(pluginCatalog, "docker") || runtimeEnabled(pluginCatalog, "kubernetes"));
  const navigation = visibleNavigation({ isAdmin, canAdminister }, pluginCatalog);
  const pluginRole: PluginAccess | "demo" | null = principalKey.current !== null
    ? meta?.mode === "demo" ? "demo" : auth?.user?.role ?? null : null;
  const externalNavigation = (pluginCatalog?.navigation ?? []).filter((item) => pluginPagePermitted(item, pluginRole));
  const externalContribution = externalNavigation.find((item) => pluginPageHash(item) === externalPage);
  const currentTitle = externalPage ? externalContribution?.title ?? "插件页面" : titles[page];
  // Event callbacks retain their render's principal, so an older modal or
  // response cannot reopen account-specific views after a session change.
  const viewIdentity = principalKey.current;
  const sessionCurrent = () => viewIdentity !== null && viewIdentity === principalKey.current;
  const openService = (service: Service) => navigate("services", () => setDetailId(service.id));
  const locateNode = (node: NodeInfo) => navigate("nodes", () => setSearch(node.address));
  const logout = async () => {
    setLogoutBusy(true);
    sessionQuery.cancel(); overviewQuery.cancel(); servicesQuery.cancel(); nodesQuery.cancel();
    principalKey.current = null;
    clearSessionData();
    try {
      await post("/auth/logout");
      setCsrfToken("");
      setHeaderPanel("");
      await refresh();
    } catch (e) {
      await refresh();
      setError((e as Error).message);
    } finally {
      setLogoutBusy(false);
    }
  };
  useEffect(() => {
    const hash = () => {
      const p = window.location.hash.slice(1);
      if (parsePluginPageHash(p) && p !== currentExternalPage.current) {
        if (hasUnsavedChanges()) window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${currentExternalPage.current ?? currentPage.current}`);
        navigatePlugin(p);
      } else if (isConsolePage(p) && (p !== currentPage.current || currentExternalPage.current)) {
        if (hasUnsavedChanges()) window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${currentExternalPage.current ?? currentPage.current}`);
        navigate(p);
      }
    };
    window.addEventListener("hashchange", hash);
    return () => {
      editRequest.current++;
      window.removeEventListener("hashchange", hash);
    };
  }, []);
  usePollingQuery(`job:${queryIdentity}:${job?.id ?? ""}`, async (signal) => ({
    identity: queryIdentity, value: await api<Job>(`/jobs/${job!.id}`, { signal }),
  }), {
    enabled: jobOpen && !!job?.id && !!auth?.authenticated && !logoutBusy && pluginEnabled(pluginCatalog, "services") &&
      !["completed", "success", "succeeded", "failed", "error", "cancelled", "interrupted"].includes(job?.status ?? ""),
    intervalMs: 1400,
    onSuccess: ({ identity, value }) => {
      if (identity !== principalKey.current) return;
      setJob(value);
      if (["completed", "success", "succeeded", "failed", "error", "cancelled", "interrupted"].includes(value.status)) {
        servicesQuery.invalidate(); overviewQuery.invalidate(); nodesQuery.invalidate();
      }
    },
    onError: (failure) => { if (queryIdentity === principalKey.current) setError(failure.message); },
  });
  const navigate = (p: Page, afterNavigate?: () => void) => {
    const changesView = p !== currentPage.current || !!currentExternalPage.current || !!detailId || !!editor || !!initialTemplate || !!templateSeed;
    if (!changesView) {
      setMobileNav(false); setHeaderPanel(""); setCommandOpen(false);
      afterNavigate?.();
      return;
    }
    if (hasUnsavedChanges()) {
      setConfirm({
        title: "离开当前页面？",
        description: "当前页面有未保存的编辑或尚未结束的操作。取消可继续处理，离开将关闭当前页面。",
        text: "放弃并离开",
        action: async () => { if (!sessionCurrent()) return; discardNavigationDrafts(); navigate(p, afterNavigate); },
      });
      return;
    }
    currentPage.current = p;
    currentExternalPage.current = null;
    setExternalPage(null);
    setEditor(null); setInitialTemplate(null); setTemplateSeed(null);
    editRequest.current++;
    setPage(p);
    window.location.hash = p;
    setDetailId(null);
    setSearch("");
    setFilter("all");
    setRuntimeFilter("all");
    setMobileNav(false);
    setHeaderPanel(""); setCommandOpen(false);
    afterNavigate?.();
  };
  const navigatePlugin = (hash: string) => {
    if (!parsePluginPageHash(hash)) return;
    navigate("settings", () => {
      currentExternalPage.current = hash;
      setExternalPage(hash);
      window.location.hash = hash;
    });
  };
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (!auth?.authenticated || logoutBusy || event.isComposing || event.repeat || event.altKey) return;
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "k") return;
      if (!commandOpen && document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      event.preventDefault(); setHeaderPanel(""); setMobileNav(false); setCommandOpen((value) => !value);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [auth?.authenticated, logoutBusy, commandOpen]);
  useEffect(() => {
    if (!["user", "activity"].includes(headerPanel)) return;
    const anchor = headerPanel === "user" ? userAnchor : activityAnchor;
    const outside = (event: Event) => {
      const target = event.target as Node;
      if (!anchor.current?.contains(target) && !userPopover.current?.contains(target)) setHeaderPanel("");
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setHeaderPanel(""); anchor.current?.focus(); }
    };
    document.addEventListener("pointerdown", outside); document.addEventListener("focusin", outside); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("focusin", outside); document.removeEventListener("keydown", key); };
  }, [headerPanel]);
  useEffect(() => {
    if (!mobileNav || !sidebarRef.current) return;
    const previous = document.activeElement as HTMLElement | null;
    const node = sidebarRef.current; const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const stops = () => Array.from(node.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],[tabindex="0"]')).filter((item) => item.getClientRects().length > 0);
    const focus = requestAnimationFrame(() => stops()[0]?.focus());
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setMobileNav(false); }
      if (event.key === "Tab") {
        const items = stops(); const first = items[0], last = items[items.length - 1];
        if (!items.length) return;
        if (event.shiftKey && (document.activeElement === first || !node.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !node.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
      }
    };
    const resize = () => { if (window.matchMedia("(min-width: 801px)").matches) setMobileNav(false); };
    document.addEventListener("keydown", key); window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(focus); document.body.style.overflow = overflow;
      document.removeEventListener("keydown", key); window.removeEventListener("resize", resize);
      if (previous?.isConnected) previous.focus({ preventScroll: true }); else mobileAnchor.current?.focus();
    };
  }, [mobileNav]);
  const didRouteFocus = useRef(false);
  useEffect(() => {
    if (!didRouteFocus.current) { didRouteFocus.current = true; return; }
    const frame = requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [page, detailId, externalPage]);
  const submitServiceJob = async (path: string, body?: unknown): Promise<Job> => {
    if (!sessionCurrent()) throw Error("登录身份已变化，未提交任务");
    const intents = new ServiceJobIntentStore();
    const pending = intents.get({ userId: auth?.user?.id || (meta?.mode === "demo" ? "demo" : ""), mode: meta?.mode || "" }, path, body);
    let jobId = pending.jobId;
    try {
      if (!jobId) {
        const accepted = await createMutationIntent("service", pending.operationKey).submit<{ job_id: string }>(path, body);
        jobId = intents.accepted(pending, accepted.job_id);
      }
      if (!sessionCurrent()) throw Object.assign(Error("登录身份已变化，已保留原任务供再次登录后核对"), { outcomeUnknown: true });
      const value = await api<Job>(`/jobs/${encodeURIComponent(jobId)}`);
      if (!value || value.id !== jobId || !["queued", "pending", "running", "deploying", "completed", "success", "succeeded", "failed", "error", "cancelled", "interrupted"].includes(value.status) || !Array.isArray(value.steps))
        throw Object.assign(Error("原任务观测结果无效，已保留待确认操作，请重新查询原任务"), { outcomeUnknown: true });
      if (sessionCurrent()) intents.resolved(pending);
      return value;
    } catch (error) {
      const result = error as { outcomeUnknown?: boolean; status?: number };
      if (pending.created && !jobId && !result.outcomeUnknown && result.status && result.status >= 400 && result.status < 500)
        intents.resolved(pending);
      throw error;
    }
  };
  const doJob = async (
    service: Service,
    action: string,
    approval?: DeploymentApproval,
  ) => {
    if (!sessionCurrent()) return;
    const runtimeReason = runtimeReadOnlyReason(pluginCatalogRef.current, service.runtime);
    if (runtimeReason) throw Error(runtimeReason);
    if (action === "deploy" && service.needs_rebuild) action = "rebuild";
    const value = await submitServiceJob(
      `/services/${service.id}/${action}`,
      approval,
    );
    if (!sessionCurrent()) return;
    setJob(value);
    setJobOpen(true);
    void refresh(true);
  };
  const checkStopJobs = async (service: Service): Promise<Job | null> => {
    if (stopJobRequest.current) throw Error("正在核对停止任务，请稍候。");
    const controller = new AbortController();
    const request = { controller, viewRevision: stopView.current.revision };
    stopJobRequest.current = request; setStopCheckingId(service.id);
    const timeout = window.setTimeout(() => controller.abort(), 30_000);
    try {
      const result = await api<{ items: (Job & { service_id: string })[] }>(`/jobs?service_id=${encodeURIComponent(service.id)}`, { signal: controller.signal });
      const statuses = ["queued", "pending", "running", "deploying", "completed", "success", "succeeded", "failed", "error", "cancelled", "interrupted"];
      if (!result || !Array.isArray(result.items) || result.items.some((item) => !item || item.service_id !== service.id || typeof item.id !== "string" || !item.id || !statuses.includes(item.status) || !Array.isArray(item.steps)))
        throw Error("任务状态响应不符合约定，尚未确认可以继续停止。请刷新后重试。");
      return result.items.find((item) => ["queued", "running", "pending", "deploying"].includes(item.status)) ?? null;
    } catch (error) {
      if (controller.signal.aborted) throw Error("任务状态核对已取消或超时，尚未继续停止。请重试。");
      throw error;
    } finally {
      window.clearTimeout(timeout);
      if (stopJobRequest.current === request) {
        stopJobRequest.current = null;
        if (sessionCurrent()) setStopCheckingId("");
      }
    }
  };
  const performAction = (service: Service, action: string) => {
    const readOnly = runtimeReadOnlyReason(pluginCatalogRef.current, service.runtime) || executionReadOnlyReason(service, nodes);
    if (readOnly && ["deploy", "rebuild", "restart", "stop", "undeploy", "scale", "delete"].includes(action)) {
      setError(readOnly);
      return;
    }
    if (!sessionCurrent()) return;
    if (action !== "job" && !canOperate) {
      setError("当前账号为只读角色，无法执行服务变更。");
      return;
    }
    if (action === "template") {
      if (service.config_redacted) {
        setError("当前账号无权读取完整服务配置。");
        return;
      }
      try {
        setTemplateSeed(serviceTemplateInput(service));
      } catch (e) {
        setError((e as Error).message);
      }
      return;
    }
    if (action === "undeploy") {
      setUndeployService(service);
      return;
    }
    if (action === "scale") {
      setScaleService(service);
      return;
    }
    if (action === "job") {
      if (!service.last_job_id) return;
      api<Job>(`/jobs/${service.last_job_id}`)
        .then((value) => {
          if (!sessionCurrent()) return;
          setJob(value);
          setJobOpen(true);
        })
        .catch((error) => { if (sessionCurrent()) setError(error.message); });
      return;
    }
    if (action === "deploy") {
      setActionBusy(true);
      setPlanError("");
      post<Plan>(`/services/${service.id}/plan`)
        .then((plan) => {
          if (!sessionCurrent()) return;
          setPlanExtraApproved(false);
          setPlanDialog({ service, plan });
        })
        .catch((e) => { if (sessionCurrent()) setError(e.message); })
        .finally(() => { if (sessionCurrent()) setActionBusy(false); });
      return;
    }
    const continueStop = action === "stop" && service.runtime === "kubernetes" && service.status === "stopping";
    const stopViewRevision = stopView.current.revision;
    const stopEditRequest = editRequest.current;
    const stopTargetCurrent = () => sessionCurrent() && currentPage.current === "services" && stopView.current.revision === stopViewRevision && editRequest.current === stopEditRequest;
    const text =
      action === "stop"
        ? continueStop ? "继续停止" : "停止服务"
        : action === "restart"
          ? "重启服务"
          : "删除服务";
    const openConfirmation = () => setConfirm({
      runtime: service.runtime,
      title: `${text} ${service.name}？`,
      description: (
        <>
          <p>
            {continueStop
              ? "按已保存停机基线继续核对/停止受管工作负载。提交前将再次检查该服务的排队与运行任务。"
              : action === "delete"
              ? "将删除控制台中的服务记录及受控部署资源，模型权重不会被删除。"
              : "操作将影响该服务正在运行的实例和请求。"}
          </p>
          <div className="confirm-resource">
            <Box size={17} />
            <b>{service.name}</b>
            <RuntimeTag value={service.runtime} />
          </div>
        </>
      ),
      text,
      isCurrent: continueStop ? stopTargetCurrent : undefined,
      action: async () => {
        if (continueStop) {
          if (!stopTargetCurrent()) return;
          const active = await checkStopJobs(service);
          if (!stopTargetCurrent()) return;
          if (active) {
            setConfirm(null); setJob(active); setJobOpen(true);
            notify("该服务原有任务仍在排队或执行，请先查看任务。", "warning");
            return;
          }
        }
        if (action === "delete") {
          const result = await api<{ ok?: boolean; status?: string; resources_may_exist?: boolean }>(`/services/${service.id}`, { method: "DELETE" });
          if (!sessionCurrent()) return;
          if (result?.ok === true && (result.resources_may_exist === undefined || result.resources_may_exist === false) &&
              (result.status === undefined || result.status === "removed")) {
            if (detailId === service.id) setDetailId(null);
            notify("服务已删除");
          } else if (result?.ok !== true && result?.status === "removing" && result.resources_may_exist === true) {
            notify("删除已受理，正在等待资源清理；服务记录继续保留。", "warning");
          } else {
            throw Object.assign(Error("删除结果尚未确认，请刷新服务状态并核对原操作。"), { outcomeUnknown: true });
          }
          void refresh(true);
        } else await doJob(service, action);
      },
    });
    if (continueStop) {
      if (stopJobRequest.current) return;
      void checkStopJobs(service).then((active) => {
        if (!stopTargetCurrent()) return;
        if (active) {
          setJob(active); setJobOpen(true);
          notify("该服务原有任务仍在排队或执行，请先查看任务。", "warning");
        } else openConfirmation();
      }).catch((failure) => { if (stopTargetCurrent()) setError((failure as Error).message); });
    } else openConfirmation();
  };
  const editService = async (service: Service) => {
    if (!sessionCurrent()) return;
    const runtimeReason = runtimeReadOnlyReason(pluginCatalogRef.current, service.runtime);
    if (runtimeReason) { setError(runtimeReason); return; }
    const request = ++editRequest.current;
    try {
      const latest = await api<Service>(`/services/${service.id}`);
      if (request !== editRequest.current) return;
      setServices((current) =>
        current.map((item) => (item.id === latest.id ? latest : item)),
      );
      if (!["draft", "stopped"].includes(latest.status)) {
        throw Error("服务状态已变化，请先停止服务并刷新后再编辑配置");
      }
      validateJsonNumbers(latest);
      setEditor(latest);
    } catch (e) {
      if (request === editRequest.current) setError((e as Error).message);
    }
  };
  const confirmRun = async () => {
    if (!confirm || confirmFlight.current || confirmUncertain || !sessionCurrent()) return;
    const runtimeReason = confirm.runtime && runtimeReadOnlyReason(pluginCatalogRef.current, confirm.runtime);
    if (runtimeReason) { setConfirmError(runtimeReason); return; }
    const flight = {};
    confirmFlight.current = flight;
    setActionBusy(true);
    try {
      await confirm.action();
      if (!sessionCurrent() || confirm.isCurrent?.() === false) return;
      setConfirm(null);
    } catch (e) {
      if (sessionCurrent() && confirm.isCurrent?.() !== false) {
        const unknown = (e as { outcomeUnknown?: boolean }).outcomeUnknown === true;
        setConfirmUncertain(unknown);
        setConfirmError((e as Error).message + (unknown ? " 操作结果待核对，已阻止重复提交；请关闭窗口后刷新状态并查看原操作。" : ""));
      }
    } finally {
      if (confirmFlight.current === flight) {
        confirmFlight.current = null;
        if (sessionCurrent()) setActionBusy(false);
      }
    }
  };
  useEffect(() => setServiceListPage(1), [search, filter, runtimeFilter]);
  const detail = services.find((s) => s.id === detailId);
  const filteredServices = services.filter(
    (s) =>
      matchesServiceStatus(s.status, filter) &&
      (runtimeFilter === "all" || s.runtime === runtimeFilter) &&
      `${s.name} ${s.model} ${s.node_names?.join(" ")} ${s.engine}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  useEffect(() => setServiceListPage((current) => Math.min(current, Math.max(1, Math.ceil(filteredServices.length / servicePageSize)))), [filteredServices.length, servicePageSize]);
  const table = (items: Service[]) => (
    <div className="table-wrap">
      <table className="service-table" aria-label="推理服务列表">
        <thead>
          <tr>
            <th scope="col">服务名称</th>
            <th scope="col">状态</th>
            <th scope="col">快捷操作</th>
            <th scope="col">模型</th>
            <th scope="col">运行环境</th>
            <th scope="col">实例布局</th>
            <th scope="col">部署节点</th>
            <th scope="col">NPU 逻辑设备</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id}>
              <td>
                <button className="text-link service-name" title={s.name}
                  onClick={() => openService(s)}>
                  {s.name}
                </button>
              </td>
              <td><Badge status={s.status} /></td>
              <td>
                <div className="row-actions service-quick-actions">
                  <button type="button" title={`管理 ${s.name}`} aria-label={`管理 ${s.name}`}
                    onClick={() => openService(s)}>
                    <Settings2 size={15} />
                  </button>
                  {["draft", "stopped", "error", "failed"].includes(s.status) ? (
                    <button type="button" title={`${s.needs_rebuild ? "重建" : "部署"} ${s.name}`}
                      aria-label={`${s.needs_rebuild ? "重建" : "部署"} ${s.name}`}
                      onClick={() => performAction(s, "deploy")} disabled={actionBusy || !canOperate || !runtimeEnabled(pluginCatalog, s.runtime)}>
                      <Play size={15} />
                    </button>
                  ) : (
                    <button type="button" title={`${s.status === "stopping" ? s.runtime === "kubernetes" ? "继续停止" : "停止中" : "停止"} ${s.name}`}
                      aria-label={`${s.status === "stopping" ? s.runtime === "kubernetes" ? "继续停止" : "停止中" : "停止"} ${s.name}`}
                      aria-busy={s.runtime === "kubernetes" && s.status === "stopping" && !!stopCheckingId}
                      disabled={!canOperate || !runtimeEnabled(pluginCatalog, s.runtime) || (s.status === "stopping" ? s.runtime !== "kubernetes" || !!stopCheckingId || !!executionReadOnlyReason(s, nodes) : ["deploying", "restarting", "scaling", "removing"].includes(s.status))}
                      onClick={() => performAction(s, "stop")}>
                      <Square size={14} />
                    </button>
                  )}
                  <ServiceMenu service={s} canOperate={canOperate}
                    onEdit={() => editService(s)} onAction={(action) => performAction(s, action)}
                    onExport={() => void api(`/services/${s.id}/export`)
                      .then((result) => { if (sessionCurrent()) download(result, `${s.name}.json`); })
                      .catch((error) => { if (sessionCurrent()) setError(error.message); })} />
                </div>
              </td>
              <td>{knownText(s.model) && <span className="service-model" title={s.model}>{s.model}</span>}</td>
              <td>
                <div className="service-runtime-cell">
                  <RuntimeTag value={s.runtime} />
                  {!runtimeEnabled(pluginCatalog, s.runtime) && <span className="small-caption" title={runtimeReadOnlyReason(pluginCatalog, s.runtime)}>运行时未启用 · 只读</span>}
                  {knownText(s.engine) && <span className="service-engine" title={s.engine}>{s.engine}</span>}
                </div>
              </td>
              <td>
                {isResourceInteger(s.replicas?.union) && s.replicas.union > 0 ? (
                  <span className="layout-label">{s.replicas.union} 混部实例</span>
                ) : isResourceInteger(s.replicas?.prefill) && isResourceInteger(s.replicas?.decode) ? (
                  <span className="layout-label">{s.replicas.prefill} P<span className="layout-separator">/</span>{s.replicas.decode} D</span>
                ) : <button className="text-link" onClick={() => openService(s)}>查看配置</button>}
              </td>
              <td>
                <span className="small-node-list" title={s.node_names?.join("、")}>
                  {s.node_names?.slice(0, 2).join("、") || <button className="text-link" onClick={() => openService(s)}>查看部署配置</button>}
                </span>
                {s.node_names?.length > 2 && <span className="tag small">+{s.node_names.length - 2}</span>}
              </td>
              <td>
                {isResourceInteger(s.device_count) && <><span className="npu-value">{s.device_count}</span><span className="muted-text"> 个逻辑设备</span></>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  const pageViews: ConsolePageViews = {
    overview: () => (<DeferredPanel label="集群概览"><OverviewPage
                    overview={overview} services={services} nodes={nodes} npuResources={npuResources}
                    servicesRead={{ loaded: !!loadedResources.services, refreshing: servicesQuery.refreshing, error: resourceErrors.current.services || "" }}
                    nodesRead={{ loaded: !!loadedResources.nodes, refreshing: nodesQuery.refreshing, error: resourceErrors.current.nodes || "" }}
                    refreshing={overviewQuery.refreshing || servicesQuery.refreshing || nodesQuery.refreshing}
                    onRefresh={() => { npuResources.refresh(); void overviewQuery.refresh(); void servicesQuery.refresh(); void nodesQuery.refresh(); }}
                    demo={meta?.mode === "demo"} canOperate={canCreateServices} canAdminister={canAdminister}
                    onNavigate={navigate} onCreateService={() => setEditor("new")} onOpenService={openService} /></DeferredPanel>),
    services: () => ((
                    <section className="panel service-list-panel" aria-labelledby="service-list-title">
                      <div className="service-list-header">
                        <div className="service-list-heading">
                          <span className="service-list-symbol" aria-hidden="true"><Box size={18} /></span>
                          <h2 id="service-list-title">服务列表</h2>
                          <span className="service-list-count" title="全部服务数量">{services.length}</span>
                        </div>
                        <div className="service-list-tools">
                          <SearchInput value={search} onChange={setSearch} placeholder="搜索服务、模型或节点" ariaLabel="搜索服务、模型、节点或引擎" />
                          <Select value={runtimeFilter} onChange={setRuntimeFilter} ariaLabel="筛选运行环境">
                            <option value="all">全部运行环境</option>
                            <option value="docker">Docker</option>
                            <option value="kubernetes">Kubernetes</option>
                          </Select>
                          <Select value={filter} onChange={setFilter} ariaLabel="筛选服务状态">
                            <option value="all">全部状态</option>
                            {["running", "draft", "starting", "deploying", "stopped", "partial", "error"].map((s) => (
                              <option key={s} value={s}>{statusName(s)}</option>
                            ))}
                          </Select>
                          <Button kind="primary" disabled={!canCreateServices} onClick={() => setEditor("new")}>
                            <Plus size={15} />创建推理服务
                          </Button>
                        </div>
                      </div>
                      {filteredServices.length ? table(filteredServices.slice((serviceListPage - 1) * servicePageSize, serviceListPage * servicePageSize)) : (
                        <Empty title={services.length ? "未找到匹配的服务" : "暂无推理服务"}
                          description={services.length ? "试试其他搜索关键词或筛选条件。" : "点击上方“创建推理服务”，从模板或已有配置开始。"} />
                      )}
                      <div className="table-footer service-list-footer">
                        <span>共 {filteredServices.length} 条记录</span>
                        <div className="service-pagination">
                          <label htmlFor="service-page-size">每页显示</label>
                          <select id="service-page-size" value={servicePageSize} onChange={(event) => { setServicePageSize(Number(event.target.value)); setServiceListPage(1); }}>
                            {[10, 25, 50, 100].map(count => <option key={count} value={count}>{count}</option>)}
                          </select>
                          {filteredServices.length > servicePageSize && <Pagination showSizeLabel={false} page={serviceListPage} totalPages={Math.ceil(filteredServices.length / servicePageSize)} onChange={setServiceListPage} />}
                        </div>
                      </div>
                    </section>
                  )),
    nodes: () => ((
                    <NodesPage
                      demo={meta?.mode === "demo"}
                      nodes={nodes}
                      resources={npuResources}
                      search={search}
                      setSearch={setSearch}
                      refresh={() => { npuResources.refresh(); void refresh(true); }}
                      notify={notify}
                      canAdminister={canAdminister}
                      onAddNode={() => setNodeModal(true)}
                      onRolloutAgent={isAdmin && meta?.mode !== "demo" ? () => setAgentRolloutView("new") : undefined}
                      onRolloutHistory={isAdmin && meta?.mode !== "demo" ? () => setAgentRolloutView("history") : undefined}
                      onDelete={(n) =>
                        setConfirm({
                          title: `移除节点 ${n.name}？`,
                          description: (
                            <p>
                              从控制台移除节点连接。若有服务仍引用该节点，操作会被阻止。
                            </p>
                          ),
                          text: "移除节点",
                          action: async () => {
                            await api(`/nodes/${n.id}`, { method: "DELETE" });
                            if (!sessionCurrent()) return;
                            notify("节点连接已移除");
                            void refresh(true);
                          },
                        })
                      }
                    />
                  )),
    images: () => ((
                    <DeferredPanel label="节点镜像"><ImagesPage search={search} setSearch={setSearch} nodes={nodes} canOperate={canOperate} principal={auth?.user?.id ?? "anonymous"} /></DeferredPanel>
                  )),
    gateways: () => ((
                    <GatewaysPage key={JSON.stringify([auth?.user?.id, auth?.user?.role, auth?.csrf_token])}
                      services={services} servicesRead={{ loaded: !!loadedResources.services, refreshing: servicesQuery.refreshing, error: resourceErrors.current.services || "" }} role={meta?.mode === "demo" ? "admin" : auth?.user?.role ?? "viewer"}
                      notify={notify} />
                  )),
    logs: () => (<LogsPage services={services} nodes={nodes} servicesRead={{ loaded: !!loadedResources.services, refreshing: servicesQuery.refreshing, error: resourceErrors.current.services || "" }} />),
    templates: () => ((
                    <TemplatesPage
                      canOperate={canOperate}
                      notify={notify}
                      onUse={(template) => {
                        const targetRuntime = template.runtime ?? (runtimeEnabled(pluginCatalogRef.current, "docker") ? "docker" : "kubernetes");
                        const runtimeReason = runtimeReadOnlyReason(pluginCatalogRef.current, targetRuntime);
                        if (runtimeReason) { setError(runtimeReason); return; }
                        navigate("services");
                        setInitialTemplate(template);
                        setEditor("new");
                      }}
                    />
                  )),
    features: () => ((
                    <CapabilitiesPage
                      capabilities={meta?.capabilities ?? []}
                      documentationUrl={meta?.documentation_url}
                      onCreate={canCreateServices ? () => setEditor("new") : undefined}
                    />
                  )),
    audit: () => ((canAdminister ? (
                      <SystemLogs audit />
                    ) : (
                      <InfoBox>操作审计仅限系统管理员查看。</InfoBox>
                    ))),
    systemlogs: () => ((isAdmin ? (
                      <SystemLogs />
                    ) : (
                      <InfoBox>系统日志仅限系统管理员查看。</InfoBox>
                    ))),
    accounts: () => ((isAdmin ? (
                      <DeferredPanel label="账号管理"><AccountsPage
                        currentUser={auth?.user ?? null}
                        onSessionChanged={() => refresh(true)}
                      /></DeferredPanel>
                    ) : (
                      <InfoBox>当前会话没有账号管理权限。</InfoBox>
                    ))),
    maintenance: () => ((isAdmin ? (
                      <DeferredPanel label="系统维护"><SystemMaintenance /></DeferredPanel>
                    ) : (
                      <InfoBox>系统维护仅限系统管理员操作。</InfoBox>
                    ))),
    settings: () => ((
                    <SettingsPage
                      pluginCatalog={pluginCatalog}
                      pluginError={pluginError}
                      pluginsRefreshing={pluginQuery.refreshing}
                      onPluginRefresh={() => void pluginQuery.refresh()}
                      meta={meta}
                      nodes={nodes}
                      auth={auth}
                      nodesRead={{ loaded: !!loadedResources.nodes, refreshing: nodesQuery.refreshing, error: resourceErrors.current.nodes || "" }}
                      onPassword={() => setPasswordOpen(true)}
                      onAccounts={
                        isAdmin && pageEnabled(pluginCatalog, "accounts") ? () => navigate("accounts") : undefined
                      }
                      onNodes={pageEnabled(pluginCatalog, "nodes") ? () => navigate("nodes") : undefined}
                    />
                  )),
  };
  const connectionError = productError || (!auth && !initialLoading ? syncError || error : "");
  const currentResource = ["overview", "services", "nodes"].includes(page) ? page : "";
  const firstReadFailed = !!currentResource && !loadedResources[currentResource] && !!resourceErrors.current[currentResource];
  const pageLoading = initialLoading || (!!currentResource && !loadedResources[currentResource] && !firstReadFailed);
  if (connectionError)
    return <main className="console-connection-state">
      <Brand />
      <section className="console-connection-card" aria-labelledby="console-connection-title">
        <AlertCircle size={30} aria-hidden="true" />
        <h1 id="console-connection-title">后端连接需要检查</h1>
        <p role="alert">{connectionError}</p>
        <Button kind="primary" loading={refreshing} onClick={() => void refresh()}>重新连接</Button>
      </section>
    </main>;
  if (auth && !auth.authenticated)
    return <LoginPage status={auth} onAuthenticated={() => refresh()} />;
  return (
    <PluginContext.Provider value={pluginCatalog}>
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`} data-page={externalPage ? "plugin" : page}>
      <a className="skip-link" href="#main-content" onClick={(event) => { event.preventDefault(); mainRef.current?.focus(); }}>跳到主要内容</a>
      <aside ref={sidebarRef} className={`sidebar ${mobileNav ? "open" : ""}`} role={mobileNav ? "dialog" : undefined} aria-modal={mobileNav || undefined} aria-label={mobileNav ? "主导航" : undefined}>
        <button className="brand-home" aria-label={pageEnabled(pluginCatalog, "overview") ? "返回集群概览" : "打开系统设置"} onClick={() => navigate(pageEnabled(pluginCatalog, "overview") ? "overview" : "settings")}><Brand compact={sidebarCollapsed} /></button>
        <button type="button" className="icon-btn mobile-navigation-close" aria-label="关闭导航" onClick={() => setMobileNav(false)}><X size={19} /></button>
        <nav id="primary-navigation" aria-label="主导航">
          {navigation.map((g) => (
            <div className="nav-group" key={g.label}>
              {g.label !== "服务管理" && <span className="nav-group-label">{g.label}</span>}
              {g.items
                .map((item) => (
                  <button
                    key={item.id}
                    className={`nav-item ${!externalPage && page === item.id ? "active" : ""}`}
                    data-nav={item.id}
                    title={sidebarCollapsed ? item.label : undefined}
                    aria-label={item.label}
                    aria-current={!externalPage && page === item.id ? "page" : undefined}
                    onClick={() => navigate(item.id as Page)}
                  >
                    <span className="nav-symbol" aria-hidden="true"><item.icon size={20} /></span>
                    <span className="nav-label">{item.label}</span>
                    {item.id === "services" && services.length > 0 && (
                      <em>{services.length}</em>
                    )}
                  </button>
                ))}
              {g.label === "运维与治理" && grafanaHref && <a className="nav-item nav-integration-link" href={grafanaHref}
                target="_blank" rel="noopener noreferrer" data-integration="grafana"
                aria-label="Grafana 监控（新窗口打开）" title="Grafana 监控（新窗口打开）" onClick={() => setMobileNav(false)}>
                <span className="nav-symbol" aria-hidden="true"><Activity size={20} /></span>
                <span className="nav-label">Grafana 监控</span><ArrowUpRight size={13} className="nav-external-mark" aria-hidden="true" />
              </a>}
            </div>
          ))}
          {!!externalNavigation.length && <div className="nav-group"><span className="nav-group-label">社区插件</span>
            {externalNavigation.map((item) => <button key={pluginPageHash(item)} className={`nav-item ${externalPage === pluginPageHash(item) ? "active" : ""}`}
              data-plugin-page={pluginPageHash(item)} aria-label={item.title} title={sidebarCollapsed ? item.title : undefined}
              aria-current={externalPage === pluginPageHash(item) ? "page" : undefined} onClick={() => navigatePlugin(pluginPageHash(item))}>
              <span className="nav-symbol" aria-hidden="true"><Box size={20} /></span><span className="nav-label">{item.title}</span></button>)}
          </div>}
        </nav>
        <div className="sidebar-bottom">
          <button aria-label="版本与帮助" onClick={() => navigate("settings")}>
            <CircleHelp size={15} />
            <span>版本与帮助</span>
            <ArrowUpRight size={13} />
          </button>
          <div className="sidebar-version">
            <span className="sidebar-brand-name" title={branding.name}>{branding.name}</span>{" "}
            {meta?.version && <span>{`v${meta.version}`}</span>}
          </div>
        </div>
      </aside>
      {mobileNav && (
        <button
          className="mobile-backdrop"
          tabIndex={-1}
          aria-label="关闭导航遮罩"
          onClick={() => setMobileNav(false)}
        />
      )}
      <div key={sessionRevision} className="workspace-main" inert={mobileNav || undefined}>
        <header className="topbar">
          <div className="topbar-left">
            <button type="button" className="icon-btn navigation-toggle" aria-label={sidebarCollapsed ? "展开导航" : "收起导航"} aria-controls="primary-navigation" aria-expanded={!sidebarCollapsed}
              onClick={() => setSidebarCollapsed((value) => {
                try { localStorage.setItem("qinghuan-platform-spatial-navigation-collapsed", String(!value)); } catch { /* Browser preference remains optional. */ }
                return !value;
              })}>{sidebarCollapsed ? <PanelLeftOpen size={24} /> : <PanelLeftClose size={24} />}</button>
            <button
              ref={mobileAnchor}
              className="icon-btn mobile-menu"
              onClick={() => setMobileNav(true)}
              aria-label="打开导航"
            >
              <Menu size={20} />
            </button>
            <span className="topbar-divider" aria-hidden="true" />
            <nav className="breadcrumb" aria-label="面包屑"><ol>
              <li><button className="breadcrumb-home" type="button" aria-label="返回集群概览" onClick={() => navigate("overview")}><Home size={16} /></button></li>
              <li><ChevronRight size={12} aria-hidden="true" />{detail ? <button type="button" onClick={() => setDetailId(null)}>{currentTitle}</button> : <span aria-current="page">{currentTitle}</span>}</li>
              {detail && <li><ChevronRight size={12} aria-hidden="true" /><span aria-current="page">{detail.name}</span></li>}
            </ol></nav>
          </div>
          <div className="topbar-right">
            {meta?.mode === "demo" && <span className="demo-label">演示数据 · 模拟操作</span>}
            <button ref={activityAnchor} type="button" className="header-icon" aria-label="查看控制台活动" title="控制台活动" aria-expanded={headerPanel === "activity"} aria-controls="session-popover" onClick={() => setHeaderPanel(headerPanel === "activity" ? "" : "activity")}><Bell size={18} /></button>
            {docsUrl && <a className="header-icon" href={docsUrl} target="_blank" rel="noreferrer" aria-label="使用文档（新窗口打开）" title="使用文档"><CircleHelp size={18} /></a>}
            <button
              ref={userAnchor}
              className="user-menu"
              aria-label="查看当前账号与会话"
              aria-controls="session-popover"
              aria-expanded={headerPanel === "user"}
              onClick={() =>
                setHeaderPanel(headerPanel === "user" ? "" : "user")
              }
            >
              <span className="avatar">
                <UserRound size={16} />
              </span>
              <span>
                {auth?.user?.display_name ||
                  auth?.user?.username ||
                  (meta?.mode === "demo" ? "演示会话" : "会话")}
              </span>
              <ChevronDown size={12} />
            </button>
          </div>
          {["user", "activity"].includes(headerPanel) && (
            <div ref={userPopover} id="session-popover" className="header-popover" role="region" aria-label={headerPanel === "user" ? "当前会话" : "控制台活动"}>
              {headerPanel === "user" ? <>
                {auth?.user && <p className="session-identity">{auth.user.username} · {roleLabel(auth.user.role)}</p>}
                {meta?.mode === "demo" && <p>演示模式，无需登录</p>}
                {auth?.user && auth.user.auth_source !== "oidc" && <button className="session-menu-link" onClick={() => { setPasswordOpen(true); setHeaderPanel(""); }}>修改个人密码</button>}
                {auth?.user && <button className="session-menu-link" disabled={logoutBusy} onClick={() => void logout()}><LogOut size={14} />{logoutBusy ? "正在退出…" : "退出登录"}</button>}
                <div className="session-menu-separator" />
                <button className="session-menu-link" onClick={() => navigate("settings")}><Settings2 size={14} />界面外观与设置</button>
                <button className="session-menu-link" onClick={() => { setHeaderPanel(""); setCommandOpen(true); }} aria-keyshortcuts="Control+K Meta+K"><Search size={14} />搜索资源或跳转页面<kbd>Ctrl K</kbd></button>
              </> : <>
                <h3>控制台活动</h3>
                <p className={`shell-sync-status ${syncError ? "sync-error" : ""}`} role="status">{syncError ? `同步异常：${syncError}` : refreshing ? "正在同步控制台数据" : lastSyncedAt ? `最近同步 ${new Date(lastSyncedAt).toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}` : "尚未完成数据同步"}</p>
                {job ? <button className="session-menu-link" onClick={() => { setJobOpen(true); setHeaderPanel(""); }}><Clock3 size={14} />查看当前部署任务</button> : <p>本次会话没有正在跟踪的部署任务。</p>}
                {canAdminister && pageEnabled(pluginCatalog, "audit") && <button className="session-menu-link" onClick={() => navigate("audit")}>查看操作审计<ArrowRight size={14} /></button>}
              </>}
            </div>
          )}
        </header>
        <main ref={mainRef} id="main-content" className="main-content" tabIndex={-1}>
          <DeferredPanel key={`${sessionRevision}:${externalPage ?? page}:${detail?.id ?? ""}`} label={currentTitle}>
          {externalPage ? externalContribution && queryIdentity ? <CommunityPluginPage key={`${queryIdentity}:${pluginCatalog?.digest}:${externalPage}`} contribution={externalContribution} identity={queryIdentity} /> :
            <section className="panel"><Empty title={!pluginCatalog ? "正在确认插件页面" : "插件页面不可用"}
              description={pluginError || "该页面尚未加载、已被停用或当前账号没有访问权限。请在系统设置中核对实际插件清单。"}
              action={<><Button onClick={() => navigate("settings")}>打开系统设置</Button><Button loading={pluginQuery.refreshing} onClick={() => void pluginQuery.refresh()}>重新读取清单</Button></>} /></section> : <>
          <div className="workspace-scene" key={detail?.id ?? page}>
          {!pageEnabled(pluginCatalog, page) && (
            <section className="panel"><Empty title={pluginCatalog ? "此功能插件未启用" : pluginError ? "无法读取插件清单" : "正在确认可用功能"}
              description={pluginCatalog ? `“${titles[page]}”未在当前启动配置中启用，可在系统设置中查看已安装插件。` : pluginError || "读取服务端插件清单后显示可用页面。"}
              action={<><Button onClick={() => navigate("settings")}>打开系统设置</Button>{!pluginCatalog && <Button loading={pluginQuery.refreshing} onClick={() => void pluginQuery.refresh()}>重新读取</Button>}</>} /></section>
          )}
          {detail && page === "services" ? (
            <div hidden={!pageEnabled(pluginCatalog, page)}>
              {error && <ErrorBox message={error} />}
              <ServiceDetail
                service={detail}
                nodes={nodes}
                onBack={() => setDetailId(null)}
                onEdit={editService}
                onAction={performAction}
                notify={notify}
                canOperate={canOperate}
                stopChecking={!!stopCheckingId}
              />
            </div>
          ) : pageEnabled(pluginCatalog, page) ? (
            <>
              {page !== "gateways" && page !== "overview" && (
              <div className="page-heading">
                <div>
                  <h1>
                    {titles[page]}
                    {page === "features" && (
                      <span className="title-count">
                        {meta?.capabilities?.length}
                      </span>
                    )}
                  </h1>
                </div>
                <div className="heading-actions">
                  {page === "nodes" && <button type="button" className="page-reload" aria-label="刷新节点列表" title="刷新节点列表" disabled={nodesQuery.refreshing} onClick={() => { npuResources.refresh(); void nodesQuery.refresh(); }}><RefreshCw size={18} className={nodesQuery.refreshing ? "spin" : undefined} /></button>}
                  {page === "services" && <button type="button" className="page-reload" aria-label="刷新服务列表" title="刷新服务列表" disabled={servicesQuery.refreshing} onClick={() => void servicesQuery.refresh()}><RefreshCw size={18} className={servicesQuery.refreshing ? "spin" : undefined} /></button>}
                </div>
              </div>
              )}
              {meta?.mode === "demo" && page === "overview" && (
                <div className="demo-banner">
                  <span>
                    <AlertCircle size={15} />
                    <b>演示工作空间</b>
                    <span>示例服务与模拟指标；操作不会发送到真实节点。</span>
                  </span>
                  <button onClick={() => navigate("settings")}>
                    了解真实环境接入
                    <ArrowRight size={13} />
                  </button>
                </div>
              )}
              {(error || syncError) && <ErrorBox message={error || syncError} />}
              {pageLoading ? (
                <Spinner label="正在连接工作空间" />
              ) : firstReadFailed ? (
                <Empty title="尚未读取此页面的数据" description="请检查上方读取原因后重试；未取得的列表不会作为空列表或零值展示。" />
              ) : (
                <>
                  {renderPageContribution(page, pluginCatalog, pageViews)}
                </>
              )}
            </>
          ) : null}
          </div>
          </>}
          </DeferredPanel>
        </main>
      </div>
      {editor && canOperate && (
        <DeferredPanel label="推理服务配置" onClose={() => setEditor(null)}>
        <ServiceEditor
          original={editor === "new" ? undefined : editor}
          initialTemplate={
            editor === "new" ? (initialTemplate ?? undefined) : undefined
          }
          nodes={nodes}
          nodesRead={{ loaded: !!loadedResources.nodes, refreshing: nodesQuery.refreshing, error: resourceErrors.current.nodes || "" }}
          onClose={() => {
            setEditor(null);
            setInitialTemplate(null);
          }}
          onSaved={() => void refresh(true)}
          onDeploy={async (s, approval) => {
            await doJob(s, "deploy", approval);
          }}
        />
        </DeferredPanel>
      )}
      {templateSeed && canOperate && pluginEnabled(pluginCatalog, "templates") && (
        <TemplateEditor
          initial={templateSeed}
          title="保存为模板"
          onClose={() => setTemplateSeed(null)}
          onSaved={(template) => {
            if (!sessionCurrent()) return;
            setTemplateSeed(null);
            notify(`模板“${template.name}”已保存`);
          }}
        />
      )}
      {commandOpen && auth?.authenticated && !logoutBusy && <DeferredPanel label="快速查找" onClose={() => setCommandOpen(false)}>
        <CommandPalette groups={navigation} services={pluginEnabled(pluginCatalog, "services") ? services : []} nodes={pluginEnabled(pluginCatalog, "nodes") ? nodes : []} onNavigate={navigate}
          externalPages={externalNavigation.map((item) => ({ id: pluginPageHash(item), label: item.title, description: item.plugin_id, icon: Box, onSelect: () => navigatePlugin(pluginPageHash(item)) }))}
          onOpenService={openService} onLocateNode={locateNode} onClose={() => setCommandOpen(false)} actions={[
            ...(canCreateServices ? [{ id: "create-service", label: "创建推理服务", description: "从模板或已有配置开始", icon: Plus, onSelect: () => setEditor("new") }] : []),
            ...(canAdminister && pluginEnabled(pluginCatalog, "nodes") ? [{ id: "connect-node", label: "接入节点", description: "登记已有执行 Agent", icon: Server, onSelect: () => setNodeModal(true) }] : []),
            ...(isAdmin && meta?.mode !== "demo" && pluginEnabled(pluginCatalog, "nodes") ? [
              { id: "push-agent", label: "推送 Agent", description: "在新节点上安装执行 Agent", icon: Upload, onSelect: () => setAgentRolloutView("new") },
              { id: "agent-history", label: "Agent 推送记录", description: "查看安装进度与连接结果", icon: Clock3, onSelect: () => setAgentRolloutView("history") },
            ] : []),
          ]} />
      </DeferredPanel>}
      {passwordOpen && (
        <DeferredPanel label="密码设置" onClose={() => setPasswordOpen(false)}><PasswordDialog
          onClose={() => setPasswordOpen(false)}
          onChanged={() => refresh()}
        /></DeferredPanel>
      )}
      {agentRolloutView && isAdmin && meta?.mode !== "demo" && pluginEnabled(pluginCatalog, "nodes") && (
        <DeferredPanel label="Agent 推送" onClose={() => setAgentRolloutView(null)}>
          <AgentRolloutDialog
            initialView={agentRolloutView}
            canAdminister={isAdmin}
            onClose={() => setAgentRolloutView(null)}
            onConnected={() => { npuResources.refresh(); void refresh(true); }}
          />
        </DeferredPanel>
      )}
      {nodeModal && canAdminister && pluginEnabled(pluginCatalog, "nodes") && (
        <NodeModal
          onClose={() => setNodeModal(false)}
          onSaved={() => {
            setNodeModal(false);
            notify("节点已接入，可执行连接探测");
            void refresh(true);
          }}
        />
      )}
      {scaleService && canOperate && pluginEnabled(pluginCatalog, "services") && (
        <ScaleDialog
          service={scaleService}
          onClose={() => setScaleService(null)}
          onScale={async (service, counts) => {
            if (!sessionCurrent()) return;
            const value = await submitServiceJob(
              `/services/${service.id}/scale`,
              { counts },
            );
            if (!sessionCurrent()) return;
            setJob(value);
            setJobOpen(true);
            void refresh(true);
          }}
        />
      )}
      {undeployService && canOperate && pluginEnabled(pluginCatalog, "services") && (
        <UndeployDialog
          service={undeployService}
          onClose={() => setUndeployService(null)}
          onUndeploy={async (service, confirmationName) => {
            if (!sessionCurrent()) return;
            const value = await submitServiceJob(
              `/services/${service.id}/undeploy`,
              { confirmation_name: confirmationName },
            );
            if (!sessionCurrent()) return;
            setJob(value);
            setJobOpen(true);
            void refresh(true);
          }}
        />
      )}
      {confirm && canOperate && (
        <Confirm
          title={confirm.title}
          description={confirm.description}
          confirmText={confirm.text}
          onClose={() => setConfirm(null)}
          onConfirm={() => void confirmRun()}
          busy={actionBusy}
          confirmDisabled={confirmUncertain}
          error={confirmError}
        />
      )}
      {planDialog && canOperate && pluginEnabled(pluginCatalog, "services") && (
        <Modal
          title={`${planDialog.service.needs_rebuild ? "重建预检" : "部署预检"} · ${planDialog.service.name}`}
          onClose={() => setPlanDialog(null)}
          closeDisabled={actionBusy}
          footer={
            <>
              <Button disabled={actionBusy} onClick={() => setPlanDialog(null)}>返回</Button>
              <Button
                kind="primary"
                disabled={
                  !runtimeEnabled(pluginCatalog, planDialog.service.runtime) ||
                  !planDialog.plan.valid ||
                  Boolean(
                    planDialog.service.extra_manifests?.length &&
                      (!planExtraApproved ||
                        !planDialog.plan.extra_manifest_sha256 ||
                        !planDialog.plan.configuration_digest ||
                        !planDialog.plan.manifests?.length),
                  )
                }
                loading={actionBusy}
                onClick={async () => {
                  if (!sessionCurrent()) return;
                  setActionBusy(true);
                  try {
                    await doJob(
                      planDialog.service,
                      "deploy",
                      planDialog.service.extra_manifests?.length &&
                        planDialog.plan.extra_manifest_sha256
                        ? {
                            extra_manifests_approved_sha256:
                              planDialog.plan.extra_manifest_sha256,
                            reviewed_configuration_digest:
                              planDialog.plan.configuration_digest ?? undefined,
                          }
                        : undefined,
                    );
                    if (!sessionCurrent()) return;
                    setPlanDialog(null);
                  } catch (e) {
                    if (sessionCurrent()) setPlanError((e as Error).message);
                  } finally {
                    if (sessionCurrent()) setActionBusy(false);
                  }
                }}
              >
                <Play size={14} />
                {planDialog.service.needs_rebuild
                  ? "确认重建并应用配置"
                  : "确认部署"}
              </Button>
            </>
          }
        >
          {planError && <ErrorBox message={planError} />}
          <div className="plan-heading">
            <ShieldCheck size={27} />
            <div>
              <h3>
                {planDialog.plan.valid
                  ? "预检通过，可以部署"
                  : "请先处理预检未通过项"}
              </h3>
              <p>
                {planDialog.service.needs_rebuild
                  ? "按当前配置重建本服务已停止的受管容器，保留模型权重。"
                  : "实际检查结果与当前配置执行计划"}
              </p>
            </div>
          </div>
          <div className="check-list">
            {planDialog.plan.checks.map((c, i) => (
              <div key={i}>
                <Badge status={c.status} />
                <div>
                  <b>{c.name}</b>
                  <p>{c.detail}</p>
                </div>
              </div>
            ))}
          </div>
          {planDialog.plan.warnings?.length > 0 && (
            <InfoBox>{planDialog.plan.warnings.join("；")}</InfoBox>
          )}
          <ManifestReview
            plan={planDialog.plan}
            nodes={nodes}
            extraCount={planDialog.service.extra_manifests?.length ?? 0}
            extraManifests={planDialog.service.extra_manifests ?? []}
            approved={planExtraApproved}
            onApprovalChange={setPlanExtraApproved}
          />
          <details className="command-preview">
            <summary>执行计划 · {planDialog.plan.commands.length} 条</summary>
            {planDialog.plan.commands.map((c, i) => (
              <pre key={i}>{c.command}</pre>
            ))}
          </details>
        </Modal>
      )}
      {jobOpen && job && pluginEnabled(pluginCatalog, "services") && (
        <JobProgress
          key={job.id}
          job={job}
          nodes={nodes}
          canOperate={canOperate}
          canAdminister={canAdminister}
          demo={meta?.mode === "demo"}
          onUpdate={(value) => { if (sessionCurrent()) setJob(value); }}
          onClose={() => setJobOpen(false)}
          onAudit={() => { setJobOpen(false); navigate("audit"); }}
        />
      )}
      {toast && (
        <div className={`toast ${toastTone}`} role="status">
          {toastTone === "error" ? (
            <AlertCircle size={17} />
          ) : toastTone === "warning" ? (
            <Clock3 size={17} style={{ color: "var(--warning)" }} />
          ) : (
            <CheckCircle2 size={17} />
          )}
          <span>{toast}</span>
          <button onClick={() => setToast("")} aria-label="关闭提示">
            <X size={14} />
          </button>
        </div>
      )}
    </div>
    </PluginContext.Provider>
  );
}

function LogsPage({ services, nodes, servicesRead }: { services: Service[]; nodes: NodeInfo[]; servicesRead: ReadState }) {
  const [serviceId, setServiceId] = useState(services[0]?.id ?? "");
  const [logs, setLogs] = useState<Log[]>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [auto, setAuto] = useState(false);
  const [filters, setFilters] = useState<LogFilters>({ serviceId: "", nodeId: "", role: "" });
  const [partial, setPartial] = useState(false);
  const nodeId = filters.serviceId === serviceId ? filters.nodeId : "";
  const role = filters.serviceId === serviceId ? filters.role : "";
  const selectedService = services.find((service) => service.id === serviceId);
  const runtimeAccess = useRuntimeAccess(selectedService?.runtime ?? "");
  const unsubmitted = isUnsubmittedDraft(services.find((service) => service.id === serviceId));
  const { refresh: load, refreshing: loading, hasSuccess } = usePollingQuery(
    `runtime-logs:${serviceId}:${unsubmitted}:${nodeId}:${role}`,
    async (signal) => api<LogResponse>(logPath(serviceId, 500, nodeId, role), { signal }), {
      enabled: runtimeAccess.enabled && !!serviceId && !unsubmitted,
      intervalMs: auto ? 4000 : 0,
      onSuccess: (response) => { setLogs(response.items); setPartial(response.partial === true); setError(""); },
      onError: (failure) => setError(failure.message),
    },
  );
  useEffect(() => {
    if (!services.some((service) => service.id === serviceId)) setServiceId(services[0]?.id ?? "");
  }, [services, serviceId]);
  useEffect(() => { setLogs([]); setError(""); setPartial(false); }, [serviceId, unsubmitted, nodeId, role]);
  const filteredLogs = runtimeAccess.enabled ? logs.filter((line) => matchesLog(line, query)) : [];
  return (
    <section className="panel log-panel">
      {runtimeAccess.enabled && error && <ErrorBox message={error} />}
      {selectedService && !runtimeAccess.enabled && <InfoBox>{runtimeAccess.reason}</InfoBox>}
      <div className="table-toolbar">
        <Select
          value={serviceId}
          onChange={setServiceId}
          ariaLabel="选择日志服务"
        >
          <option value="">选择推理服务</option>
          {services.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </Select>
        <RuntimeLogFilters service={services.find((service) => service.id === serviceId)} nodes={nodes} nodeId={nodeId} role={role} onChange={(nextNode, nextRole) => setFilters({ serviceId, nodeId: nextNode, role: nextRole })} />
        <SearchInput
          value={query}
          onChange={setQuery}
          placeholder="搜索当前日志窗口"
        />
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={auto}
            disabled={unsubmitted}
            onChange={(e) => setAuto(e.target.checked)}
          />
          自动刷新
        </label>
        <Button
          onClick={() => void load()}
          loading={loading}
          disabled={!runtimeAccess.enabled || !serviceId || unsubmitted}
        >
          <RefreshCw size={14} />
          刷新
        </Button>
        <Button
          onClick={() => download(logs, "mindie-motor-console-runtime-logs.json")}
          disabled={!runtimeAccess.enabled || !hasSuccess || !logs.length || unsubmitted}
        >
          <Download size={14} />
          导出
        </Button>
      </div>
      {runtimeAccess.enabled && hasSuccess && partial && <InfoBox>部分日志来源未完整返回，当前窗口可能缺少记录；请按执行节点、角色筛选后重试，并查看 Agent 诊断。</InfoBox>}
      <div className="log-view tall">
        {hasSuccess && filteredLogs.length ? (
          filteredLogs.map((l, i) => (
              <RuntimeLogLine key={i} line={l} />
            ))
        ) : (
          <div className="log-empty">
            <TerminalSquare size={36} />
            <h3>
              {selectedService && !runtimeAccess.enabled ? "运行时未启用，已暂停日志读取" : !servicesRead.loaded ? readNotice(servicesRead) : serviceId && !unsubmitted && !hasSuccess ? error ? "日志读取失败，请重试" : "正在读取日志…" : logs.length
                ? "没有匹配的日志"
                : unsubmitted
                ? "服务尚未提交部署，暂无容器/Pod运行日志"
                : serviceId
                  ? "尚无运行日志"
                  : "选择一个服务查看日志"}
            </h3>
            <span>
              {logs.length
                ? "调整筛选条件或清空搜索后查看。"
                : unsubmitted
                ? "提交部署后可查看运行日志。"
                : loading
                  ? "正在获取日志…"
                  : "执行节点与角色在服务端筛选后读取近期尾窗；关键词只搜索当前返回窗口"}
            </span>
          </div>
        )}
      </div>
      <div className="log-footer">
        <span
          className={`status-dot ${runtimeAccess.enabled && auto && !unsubmitted ? "green" : "muted"}`}
        />
        {!runtimeAccess.enabled && selectedService ? "运行时未启用" : unsubmitted ? "尚未提交部署" : auto ? "每 4 秒刷新" : "按需刷新"}
        <span>{runtimeAccess.enabled && serviceId && !unsubmitted ? readCount({ loaded: hasSuccess, refreshing: loading, error }, logs.length, "行已返回 · 近期窗口上限 500 行，非完整归档") : selectedService && !runtimeAccess.enabled ? "已暂停读取，不展示旧日志数量" : servicesRead.loaded ? "选择已部署服务后读取日志" : readNotice(servicesRead)}</span>
      </div>
    </section>
  );
}
