import { UiElement, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronDown,
  Copy as CopyIcon,
  Loader2,
  Search,
  X,
} from "./icons";
import { statusName } from "./api";

function DefaultBadge({
  status,
  children,
}: {
  status: string;
  children?: ReactNode;
}) {
  return (
    <UiElement as="span" className={`badge badge-${status}`}>
      <UiElement as="i" />
      {children ?? statusName(status)}
    </UiElement>
  );
}
function DefaultEmpty({
  title = "暂无数据",
  description = "数据接入后将在这里展示。",
  icon,
  action,
}: {
  title?: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <UiElement as="div" className="empty-state">
      <UiElement as="div" className="empty-icon">{icon ?? <Search size={25} />}</UiElement>
      <UiElement as="h3">{title}</UiElement>
      <UiElement as="p">{description}</UiElement>
      {action}
    </UiElement>
  );
}
function DefaultSpinner({ label = "正在加载" }: { label?: string }) {
  return (
    <UiElement as="div"
      className="loading-state"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <Loader2 className="spin" size={22} />
      <UiElement as="span">{label}</UiElement>
    </UiElement>
  );
}
function DefaultButton({
  children,
  onClick,
  kind = "default",
  disabled = false,
  loading = false,
  className = "",
  type = "button",
  title,
  size = "small",
  actionId,
  actionContext,
}: {
  children: ReactNode;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  kind?: "primary" | "default" | "danger" | "text";
  disabled?: boolean;
  loading?: boolean;
  className?: string;
  type?: "button" | "submit";
  title?: string;
  size?: "small" | "medium" | "large";
  actionId?: string;
  actionContext?: Record<string, unknown>;
}) {
  return (
    <UiButton
      actionId={actionId}
      actionContext={actionContext}
      type={type}
      title={title}
      className={`btn btn-${kind} btn-${size} ${className}`}
      onClick={onClick}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading && <Loader2 className="spin" size={14} />}
      {children}
    </UiButton>
  );
}
function DefaultField({
  label,
  children,
  hint,
  required,
  layout = "vertical",
}: {
  label: string;
  children: ReactNode;
  hint?: string;
  required?: boolean;
  layout?: "vertical" | "horizontal";
}) {
  return (
    <UiElement as="label" className={`field field-${layout}`}>
      <UiElement as="span" className="field-label">
        {label}
        {required && <UiElement as="em" aria-hidden="true"><UiText>{"*"}</UiText></UiElement>}
      </UiElement>
      <UiElement as="span" className="field-control">
        {children}
        {hint && <UiElement as="small">{hint}</UiElement>}
      </UiElement>
    </UiElement>
  );
}
function DefaultSearchInput({
  value,
  onChange,
  placeholder = "搜索名称、模型或节点",
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  ariaLabel?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <UiElement as="div" className="search-input">
      <Search size={15} />
      <UiElement as="input"
        ref={input}
        type="search"
        aria-label={ariaLabel || placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      <UiButton actionId="action.ui.001"
        type="button"
        className="icon-btn search-clear"
        aria-label="清除搜索"
        disabled={!value}
        onClick={() => {
          onChange("");
          input.current?.focus();
        }}
      >
        <X size={14} />
      </UiButton>
    </UiElement>
  );
}
function DefaultErrorBox({ message }: { message: string }) {
  return (
    <UiElement as="div" className="error-box" role="alert">
      <AlertCircle size={17} />
      <UiElement as="span">{message}</UiElement>
    </UiElement>
  );
}
function DefaultInfoBox({ children }: { children: ReactNode }) {
  return (
    <UiElement as="div" className="info-box">
      <UiElement as="span" className="info-circle"><UiText>{"i"}</UiText></UiElement>
      <UiElement as="div">{children}</UiElement>
    </UiElement>
  );
}
function DefaultCopy({
  value,
  label = "复制",
}: {
  value: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);
  return (
    <UiButton actionId="action.ui.002"
      type="button"
      className="copy-button"
      title={
        copyFailed ? "复制失败，请手动选择文本" : copied ? "已复制" : label
      }
      aria-label={label}
      aria-description={
        copyFailed ? "复制失败，请手动选择文本" : copied ? "已复制" : undefined
      }
      data-feedback={copyFailed ? "error" : copied ? "success" : undefined}
      onClick={async () => {
        try {
          setCopyFailed(false);
          await navigator.clipboard.writeText(value);
          if (!mounted.current) return;
          setCopied(true);
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(() => setCopied(false), 1600);
        } catch {
          if (mounted.current) {
            setCopied(false);
            setCopyFailed(true);
          }
        }
      }}
    >
      {copyFailed ? (
        <AlertCircle size={14} />
      ) : copied ? (
        <Check size={14} />
      ) : (
        <CopyIcon size={14} />
      )}
    </UiButton>
  );
}
type ModalEntry = { id: symbol; node: HTMLDivElement };
const modalStack: ModalEntry[] = [];
let modalPreviousOverflow = "";
const modalBackgroundInert = new Map<HTMLElement, boolean>();

function modalTabStops(node: HTMLElement) {
  return [
    ...node.querySelectorAll<HTMLElement>(
      "button, input, select, textarea, summary, a[href], [tabindex]",
    ),
  ].filter(
    (item) =>
      item.tabIndex >= 0 &&
      !item.matches(":disabled") &&
      item.getAttribute("aria-disabled") !== "true" &&
      !item.closest('[hidden], [inert], [aria-hidden="true"]') &&
      item.getClientRects().length > 0 &&
      getComputedStyle(item).visibility !== "hidden",
  );
}
function synchronizeModalStack() {
  modalStack.forEach((entry, index) => {
    const top = index === modalStack.length - 1;
    entry.node.inert = !top;
    entry.node.setAttribute("aria-hidden", top ? "false" : "true");
    entry.node.setAttribute("aria-modal", top ? "true" : "false");
  });
}

export type ModalProps = {
  title: string;
  subtitle?: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode | ((dismiss: () => void) => ReactNode);
  wide?: boolean;
  size?: "small" | "standard";
  closeDisabled?: boolean;
  contentKey?: string;
  layout?: "dialog" | "drawer";
  headerIcon?: ReactNode;
};

function overlayExitMilliseconds() {
  if (
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
    document.documentElement.dataset.motion === "reduced"
  )
    return 0;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue("--motion-exit")
    .trim();
  const duration = Number.parseFloat(value);
  if (!Number.isFinite(duration)) return 160;
  return Math.min(
    180,
    Math.max(0, value.endsWith("ms") ? duration : duration * 1000),
  );
}

function DefaultModal({
  title,
  subtitle,
  children,
  onClose,
  footer,
  wide = false,
  size = "standard",
  closeDisabled = false,
  contentKey,
  layout = "dialog",
  headerIcon,
}: ModalProps) {
  const ref = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const [closing, setClosing] = useState(false);
  const closingRef = useRef(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestClose = () => {
    if (closeDisabled || closingRef.current) return;
    const duration = overlayExitMilliseconds();
    if (!duration) {
      onCloseRef.current();
      return;
    }
    closingRef.current = true;
    setClosing(true);
    closeTimer.current = setTimeout(() => {
      closeTimer.current = null;
      // The parent can defer or reject closing (for example, an unsaved-change
      // confirmation). Restore this surface before handing control back; a
      // successful close unmounts it in the same React update batch.
      closingRef.current = false;
      setClosing(false);
      onCloseRef.current();
    }, duration);
  };
  const close = useRef(requestClose);
  close.current = requestClose;
  const identifier = useRef(Symbol("modal"));
  useEffect(() => {
    body.current?.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }, [contentKey, title]);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const previous = document.activeElement as HTMLElement | null;
    if (!modalStack.length) {
      modalPreviousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      for (const sibling of document.body.children) {
        if (
          sibling instanceof HTMLElement &&
          !sibling.contains(node) &&
          !sibling.matches("script, style, link, .modal-overlay")
        ) {
          modalBackgroundInert.set(sibling, sibling.inert);
          sibling.inert = true;
        }
      }
    }
    const entry = { id: identifier.current, node };
    modalStack.push(entry);
    synchronizeModalStack();
    const isTop = () => modalStack.at(-1)?.id === entry.id;
    const fn = (e: KeyboardEvent) => {
      if (!isTop() || e.defaultPrevented) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        close.current();
      }
      if (e.key === "Tab") {
        const items = modalTabStops(node);
        const first = items[0],
          last = items[items.length - 1];
        const active = document.activeElement;
        if (!items.length) {
          e.preventDefault();
          node.focus();
        } else if (
          e.shiftKey &&
          (active === first || !items.includes(active as HTMLElement))
        ) {
          e.preventDefault();
          last.focus();
        } else if (
          !e.shiftKey &&
          (active === last || !items.includes(active as HTMLElement))
        ) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    const focus = (event: FocusEvent) => {
      if (isTop() && !node.contains(event.target as Node))
        node.focus({ preventScroll: true });
    };
    document.addEventListener("keydown", fn);
    document.addEventListener("focusin", focus);
    node.focus({ preventScroll: true });
    return () => {
      if (closeTimer.current) clearTimeout(closeTimer.current);
      document.removeEventListener("keydown", fn);
      document.removeEventListener("focusin", focus);
      const wasTop = isTop();
      const index = modalStack.findIndex((item) => item.id === entry.id);
      if (index >= 0) modalStack.splice(index, 1);
      synchronizeModalStack();
      if (!modalStack.length) {
        document.body.style.overflow = modalPreviousOverflow;
        for (const [sibling, inert] of modalBackgroundInert)
          sibling.inert = inert;
        modalBackgroundInert.clear();
      }
      if (wasTop) {
        const top = modalStack.at(-1)?.node;
        if (previous?.isConnected && (!top || top.contains(previous)))
          previous.focus({ preventScroll: true });
        else top?.focus({ preventScroll: true });
      }
    };
  }, []);
  return createPortal(
    <UiElement as="div"
      className={`modal-overlay${layout === "drawer" ? " modal-overlay-drawer" : ""}`}
      data-closing={closing || undefined}
      onPointerDown={(event) => {
        if (layout === "drawer" && event.target === event.currentTarget)
          requestClose();
      }}
    >
      <UiElement as="div"
        ref={ref}
        tabIndex={-1}
        className={`modal ${wide ? "modal-wide" : `modal-${size}`}${layout === "drawer" ? " modal-drawer" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <UiElement as="div" className="modal-header">
          <UiElement as="div">
            {headerIcon && <UiElement as="div" className="modal-header-symbol">{headerIcon}</UiElement>}
            <UiElement as="h2">{title}</UiElement>
            {subtitle && <UiElement as="p">{subtitle}</UiElement>}
          </UiElement>
          <UiButton actionId="action.ui.003"
            type="button"
            className="icon-btn"
            aria-label="关闭窗口"
            onClick={requestClose}
            disabled={closeDisabled || closing}
          >
            <UiElement as="span" aria-hidden="true"><UiText>{"×"}</UiText></UiElement>
          </UiButton>
        </UiElement>
        <UiElement as="div" ref={body} className="modal-body" inert={closing || undefined}>
          {children}
        </UiElement>
        {footer && (
          <UiElement as="div" className="modal-footer" inert={closing || undefined}>
            {typeof footer === "function" ? footer(requestClose) : footer}
          </UiElement>
        )}
      </UiElement>
    </UiElement>,
    document.body,
  );
}

function DefaultDrawer(props: Omit<ModalProps, "layout">) {
  return <Modal {...props} layout="drawer" />;
}
function DefaultConfirm({
  title,
  description,
  confirmText = "确定",
  onConfirm,
  onClose,
  danger = true,
  busy = false,
  confirmDisabled = false,
  error,
}: {
  title: string;
  description: ReactNode;
  confirmText?: string;
  onConfirm: () => void;
  onClose: () => void;
  danger?: boolean;
  busy?: boolean;
  confirmDisabled?: boolean;
  error?: string;
}) {
  return (
    <Modal
      title={title}
      size="small"
      onClose={onClose}
      closeDisabled={busy}
      headerIcon={
        <UiElement as="span" className={`confirm-symbol ${danger ? "danger" : ""}`}>
          {danger ? <AlertCircle size={40} /> : <CheckCircle2 size={40} />}
        </UiElement>
      }
      footer={
        <>
          <Button actionId="action.ui.004" size="medium" onClick={onClose} disabled={busy}>
            <UiText>{"取消"}</UiText>
          </Button>
          <Button actionId="action.ui.005"
            kind={danger ? "danger" : "primary"}
            size="medium"
            loading={busy}
            disabled={confirmDisabled}
            onClick={onConfirm}
          >
            {confirmText}
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      <UiElement as="div" className="confirm-content">{description}</UiElement>
    </Modal>
  );
}
function DefaultSegment({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string; icon?: ReactNode }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <UiElement as="div" className="segment">
      {options.map((x) => (
        <UiButton actionId="action.ui.006"
          type="button"
          aria-pressed={value === x.value}
          className={value === x.value ? "active" : ""}
          key={x.value}
          onClick={() => onChange(x.value)}
        >
          {x.icon}
          {x.label}
        </UiButton>
      ))}
    </UiElement>
  );
}
function DefaultSelect({
  value,
  onChange,
  children,
  ariaLabel,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  ariaLabel?: string;
  disabled?: boolean;
}) {
  return (
    <UiElement as="div" className="select-wrap">
      <UiElement as="select"
        disabled={disabled}
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {children}
      </UiElement>
      <ChevronDown size={14} />
    </UiElement>
  );
}
function DefaultStatBar({
  value,
  kind = "orange",
}: {
  value: number | null | undefined;
  kind?: string;
}) {
  return (
    <UiElement as="div" className="stat-bar-wrap">
      <UiElement as="div" className="stat-bar">
        <UiElement as="div"
          className={kind}
          style={{ width: `${Math.min(100, Math.max(0, value ?? 0))}%` }}
        />
      </UiElement>
      <UiElement as="span">{value == null ? "—" : `${value.toFixed(0)}%`}</UiElement>
    </UiElement>
  );
}
function DefaultJsonEditor({
  value,
  onChange,
  label = "配置 JSON",
  rows = 17,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  rows?: number;
  disabled?: boolean;
}) {
  const nums = value.split("\n").length;
  return (
    <UiElement as="div" className="code-editor">
      <UiElement as="div" className="code-editor-top">
        <UiElement as="span">
          <UiElement as="i" />
          {label}
        </UiElement>
        <UiElement as="span"><UiText>{"JSON · UTF-8"}</UiText></UiElement>
      </UiElement>
      <UiElement as="div" className="code-editor-content">
        <UiElement as="div" className="line-numbers" aria-hidden="true">
          {Array.from({ length: Math.max(nums, rows) }, (_, i) => (
            <UiElement as="span" key={i}>{i + 1}</UiElement>
          ))}
        </UiElement>
        <UiElement as="textarea"
          aria-label={label}
          disabled={disabled}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={rows}
          spellCheck={false}
        />
      </UiElement>
    </UiElement>
  );
}

export const Badge = withUiSlot("component.Badge", DefaultBadge);
export const Empty = withUiSlot("component.Empty", DefaultEmpty);
export const Spinner = withUiSlot("component.Spinner", DefaultSpinner);
export const Button = withUiSlot("component.Button", DefaultButton);
export const Field = withUiSlot("component.Field", DefaultField);
export const SearchInput = withUiSlot("component.SearchInput", DefaultSearchInput);
export const ErrorBox = withUiSlot("component.ErrorBox", DefaultErrorBox);
export const InfoBox = withUiSlot("component.InfoBox", DefaultInfoBox);
export const Copy = withUiSlot("component.Copy", DefaultCopy);
export const Modal = withUiSlot("component.Modal", DefaultModal);
export const Drawer = withUiSlot("component.Drawer", DefaultDrawer);
export const Confirm = withUiSlot("component.Confirm", DefaultConfirm);
export const Segment = withUiSlot("component.Segment", DefaultSegment);
export const Select = withUiSlot("component.Select", DefaultSelect);
export const StatBar = withUiSlot("component.StatBar", DefaultStatBar);
export const JsonEditor = withUiSlot("component.JsonEditor", DefaultJsonEditor);

// Reference package implementation exports.
export { DefaultBadge, DefaultEmpty, DefaultSpinner, DefaultButton, DefaultField, DefaultSearchInput, DefaultErrorBox, DefaultInfoBox, DefaultCopy, DefaultModal, DefaultDrawer, DefaultConfirm, DefaultSegment, DefaultSelect, DefaultStatBar, DefaultJsonEditor };
