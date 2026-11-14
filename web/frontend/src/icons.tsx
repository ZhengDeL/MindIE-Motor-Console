import { forwardRef } from "react";
import { useUiContract } from "./uiPluginSdk";
import type { ForwardRefExoticComponent, RefAttributes, SVGProps } from "react";
import {
  ArrowDown as LucideArrowDown,
  ArrowUp as LucideArrowUp,
  Bell as LucideBell,
  Home as LucideHome,
  ImagePlus as LucideImagePlus,
  MemoryStick as LucideMemoryStick,
  PanelLeftClose as LucidePanelLeftClose,
  PanelLeftOpen as LucidePanelLeftOpen,
  RotateCcw as LucideRotateCcw,
  Activity as LucideActivity,
  AlertCircle as LucideAlertCircle,
  Archive as LucideArchive,
  ArrowLeft as LucideArrowLeft,
  ArrowRight as LucideArrowRight,
  ArrowUpRight as LucideArrowUpRight,
  BookOpen as LucideBookOpen,
  Box as LucideBox,
  Boxes as LucideBoxes,
  Braces as LucideBraces,
  Check as LucideCheck,
  CheckCircle2 as LucideCheckCircle2,
  ChevronDown as LucideChevronDown,
  ChevronLeft as LucideChevronLeft,
  ChevronRight as LucideChevronRight,
  CircleHelp as LucideCircleHelp,
  ClipboardCheck as LucideClipboardCheck,
  ClipboardList as LucideClipboardList,
  Clock3 as LucideClock3,
  Cloud as LucideCloud,
  Copy as LucideCopy,
  Cpu as LucideCpu,
  Download as LucideDownload,
  ExternalLink as LucideExternalLink,
  Eye as LucideEye,
  EyeOff as LucideEyeOff,
  FileCheck2 as LucideFileCheck2,
  FileClock as LucideFileClock,
  FileJson as LucideFileJson,
  FileText as LucideFileText,
  Files as LucideFiles,
  GitBranch as LucideGitBranch,
  KeyRound as LucideKeyRound,
  Layers3 as LucideLayers3,
  LayoutDashboard as LucideLayoutDashboard,
  Loader2 as LucideLoader2,
  LockKeyhole as LucideLockKeyhole,
  LogOut as LucideLogOut,
  Menu as LucideMenu,
  Monitor as LucideMonitor,
  Moon as LucideMoon,
  MoreHorizontal as LucideMoreHorizontal,
  Network as LucideNetwork,
  Play as LucidePlay,
  Plus as LucidePlus,
  RefreshCw as LucideRefreshCw,
  Rocket as LucideRocket,
  Search as LucideSearch,
  Send as LucideSend,
  Server as LucideServer,
  Settings2 as LucideSettings2,
  ShieldCheck as LucideShieldCheck,
  SlidersHorizontal as LucideSlidersHorizontal,
  Square as LucideSquare,
  Sun as LucideSun,
  TerminalSquare as LucideTerminalSquare,
  Trash2 as LucideTrash2,
  Upload as LucideUpload,
  UserRound as LucideUserRound,
  UsersRound as LucideUsersRound,
  Wrench as LucideWrench,
  X as LucideX,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type ConsoleIconProps = Omit<SVGProps<SVGSVGElement>, "children"> & {
  size?: number | string;
  spin?: boolean;
};
export type ConsoleIcon = ForwardRefExoticComponent<
  ConsoleIconProps & RefAttributes<SVGSVGElement>
>;

// Preserve application props and accessible naming while using the same
// Lucide line icons as the fixed Portainer 2.45.3 interface reference.
function createIcon(Definition: LucideIcon, name: string): ConsoleIcon {
  const Icon = forwardRef<SVGSVGElement, ConsoleIconProps>(
    ({ size = 18, width = size, height = size, color, className, style, spin = false, ...props }, ref) => {
      const pack = useUiContract<{ render: (id: string, props: Record<string, any>, Default: LucideIcon) => React.ReactNode }>("ui.icons");
      const labelled = Boolean(props["aria-label"] || props["aria-labelledby"]);
      return pack.render(name, { ...props, ref, size, width, height, focusable: "false",
        "aria-hidden": props["aria-hidden"] ?? !labelled,
        role: props.role ?? (labelled ? "img" : undefined), "data-icon": name,
        className: ["console-icon", "console-icon-line", spin && "spin", className].filter(Boolean).join(" "),
        style: { ...(color != null ? { color } : {}), ...style } }, Definition);
    },
  );
  Icon.displayName = name;
  return Icon;
}

// Existing names remain stable for every action and navigation component.
export const ArrowDown = createIcon(LucideArrowDown, "ArrowDown");
export const ArrowUp = createIcon(LucideArrowUp, "ArrowUp");
export const Bell = createIcon(LucideBell, "Bell");
export const Home = createIcon(LucideHome, "Home");
export const ImagePlus = createIcon(LucideImagePlus, "ImagePlus");
export const MemoryStick = createIcon(LucideMemoryStick, "MemoryStick");
export const PanelLeftClose = createIcon(LucidePanelLeftClose, "PanelLeftClose");
export const PanelLeftOpen = createIcon(LucidePanelLeftOpen, "PanelLeftOpen");
export const RotateCcw = createIcon(LucideRotateCcw, "RotateCcw");
export const Activity = /*#__PURE__*/ createIcon(LucideActivity, "Activity");
export const AlertCircle = /*#__PURE__*/ createIcon(LucideAlertCircle, "AlertCircle");
export const Archive = /*#__PURE__*/ createIcon(LucideArchive, "Archive");
export const ArrowLeft = /*#__PURE__*/ createIcon(LucideArrowLeft, "ArrowLeft");
export const ArrowRight = /*#__PURE__*/ createIcon(LucideArrowRight, "ArrowRight");
export const ArrowUpRight = /*#__PURE__*/ createIcon(LucideArrowUpRight, "ArrowUpRight");
export const BookOpen = /*#__PURE__*/ createIcon(LucideBookOpen, "BookOpen");
export const Box = /*#__PURE__*/ createIcon(LucideBox, "Box");
export const Boxes = /*#__PURE__*/ createIcon(LucideBoxes, "Boxes");
export const Braces = /*#__PURE__*/ createIcon(LucideBraces, "Braces");
export const Check = /*#__PURE__*/ createIcon(LucideCheck, "Check");
export const CheckCircle2 = /*#__PURE__*/ createIcon(LucideCheckCircle2, "CheckCircle2");
export const ChevronDown = /*#__PURE__*/ createIcon(LucideChevronDown, "ChevronDown");
export const ChevronLeft = /*#__PURE__*/ createIcon(LucideChevronLeft, "ChevronLeft");
export const ChevronRight = /*#__PURE__*/ createIcon(LucideChevronRight, "ChevronRight");
export const CircleHelp = /*#__PURE__*/ createIcon(LucideCircleHelp, "CircleHelp");
export const ClipboardList = /*#__PURE__*/ createIcon(LucideClipboardList, "ClipboardList");
export const Clock3 = /*#__PURE__*/ createIcon(LucideClock3, "Clock3");
export const Cloud = /*#__PURE__*/ createIcon(LucideCloud, "Cloud");
export const Copy = /*#__PURE__*/ createIcon(LucideCopy, "Copy");
export const Cpu = /*#__PURE__*/ createIcon(LucideCpu, "Cpu");
export const Download = /*#__PURE__*/ createIcon(LucideDownload, "Download");
export const ExternalLink = /*#__PURE__*/ createIcon(LucideExternalLink, "ExternalLink");
export const Eye = /*#__PURE__*/ createIcon(LucideEye, "Eye");
export const EyeOff = /*#__PURE__*/ createIcon(LucideEyeOff, "EyeOff");
export const FileCheck2 = /*#__PURE__*/ createIcon(LucideFileCheck2, "FileCheck2");
export const FileClock = /*#__PURE__*/ createIcon(LucideFileClock, "FileClock");
export const FileJson = /*#__PURE__*/ createIcon(LucideFileJson, "FileJson");
export const GitBranch = /*#__PURE__*/ createIcon(LucideGitBranch, "GitBranch");
export const KeyRound = /*#__PURE__*/ createIcon(LucideKeyRound, "KeyRound");
export const Layers3 = /*#__PURE__*/ createIcon(LucideLayers3, "Layers3");
export const LayoutDashboard = /*#__PURE__*/ createIcon(LucideLayoutDashboard, "LayoutDashboard");
export const Loader2 = /*#__PURE__*/ createIcon(LucideLoader2, "Loader2");
export const LockKeyhole = /*#__PURE__*/ createIcon(LucideLockKeyhole, "LockKeyhole");
export const LogOut = /*#__PURE__*/ createIcon(LucideLogOut, "LogOut");
export const Menu = /*#__PURE__*/ createIcon(LucideMenu, "Menu");
export const Monitor = /*#__PURE__*/ createIcon(LucideMonitor, "Monitor");
export const Moon = /*#__PURE__*/ createIcon(LucideMoon, "Moon");
export const MoreHorizontal = /*#__PURE__*/ createIcon(LucideMoreHorizontal, "MoreHorizontal");
export const Network = /*#__PURE__*/ createIcon(LucideNetwork, "Network");
export const Play = /*#__PURE__*/ createIcon(LucidePlay, "Play");
export const Plus = /*#__PURE__*/ createIcon(LucidePlus, "Plus");
export const RefreshCw = /*#__PURE__*/ createIcon(LucideRefreshCw, "RefreshCw");
export const Rocket = /*#__PURE__*/ createIcon(LucideRocket, "Rocket");
export const Search = /*#__PURE__*/ createIcon(LucideSearch, "Search");
export const Send = /*#__PURE__*/ createIcon(LucideSend, "Send");
export const Server = /*#__PURE__*/ createIcon(LucideServer, "Server");
export const Settings2 = /*#__PURE__*/ createIcon(LucideSettings2, "Settings2");
export const ShieldCheck = /*#__PURE__*/ createIcon(LucideShieldCheck, "ShieldCheck");
export const SlidersHorizontal = /*#__PURE__*/ createIcon(LucideSlidersHorizontal, "SlidersHorizontal");
export const Square = /*#__PURE__*/ createIcon(LucideSquare, "Square");
export const Sun = /*#__PURE__*/ createIcon(LucideSun, "Sun");
export const TerminalSquare = /*#__PURE__*/ createIcon(LucideTerminalSquare, "TerminalSquare");
export const Trash2 = /*#__PURE__*/ createIcon(LucideTrash2, "Trash2");
export const Upload = /*#__PURE__*/ createIcon(LucideUpload, "Upload");
export const UserRound = /*#__PURE__*/ createIcon(LucideUserRound, "UserRound");
export const X = /*#__PURE__*/ createIcon(LucideX, "X");

export const NavOverview = /*#__PURE__*/ createIcon(LucideLayoutDashboard, "nav-overview");
export const NavServices = /*#__PURE__*/ createIcon(LucideBoxes, "nav-services");
export const NavTemplates = /*#__PURE__*/ createIcon(LucideFiles, "nav-templates");
export const NavCapabilities = /*#__PURE__*/ createIcon(LucideSlidersHorizontal, "nav-capabilities");
export const NavNodes = /*#__PURE__*/ createIcon(LucideServer, "nav-nodes");
export const NavImages = /*#__PURE__*/ createIcon(LucideLayers3, "nav-images");
export const NavGateways = /*#__PURE__*/ createIcon(LucideNetwork, "nav-gateways");
export const NavRuntimeLogs = /*#__PURE__*/ createIcon(LucideTerminalSquare, "nav-runtime-logs");
export const NavSystemLogs = /*#__PURE__*/ createIcon(LucideFileText, "nav-system-logs");
export const NavAudit = /*#__PURE__*/ createIcon(LucideClipboardCheck, "nav-audit");
export const NavAccounts = /*#__PURE__*/ createIcon(LucideUsersRound, "nav-accounts");
export const NavMaintenance = /*#__PURE__*/ createIcon(LucideWrench, "nav-maintenance");
export const NavSettings = /*#__PURE__*/ createIcon(LucideSettings2, "nav-settings");
