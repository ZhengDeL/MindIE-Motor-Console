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

export function Badge({
  status,
  children,
}: {
  status: string;
  children?: ReactNode;
}) {
  return (
    <span className={`badge badge-${status}`}>
      <i />
      {children ?? statusName(status)}
    </span>
  );
}
export function Empty({
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
    <div className="empty-state">
      <div className="empty-icon">{icon ?? <Search size={25} />}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Spinner({ label = "正在加载" }: { label?: string }) {
  return (
    <div
      className="loading-state"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <Loader2 className="spin" size={22} />
      <span>{label}</span>
    </div>
  );
}
export function Button({
  children,
  onClick,
  kind = "default",
  disabled = false,
  loading = false,
  className = "",
  type = "button",
  title,
  size = "small",
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
}) {
  return (
    <button
      type={type}
      title={title}
      className={`btn btn-${kind} btn-${size} ${className}`}
      onClick={onClick}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading && <Loader2 className="spin" size={14} />}
      {children}
    </button>
  );
}
export function Field({
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
    <label className={`field field-${layout}`}>
      <span className="field-label">
        {label}
        {required && <em aria-hidden="true">*</em>}
      </span>
      <span className="field-control">
        {children}
        {hint && <small>{hint}</small>}
      </span>
    </label>
  );
}
export function SearchInput({
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
    <div className="search-input">
      <Search size={15} />
      <input
        ref={input}
        type="search"
        aria-label={ariaLabel || placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      <button
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
      </button>
    </div>
  );
}
export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="error-box" role="alert">
      <AlertCircle size={17} />
      <span>{message}</span>
    </div>
  );
}
export function InfoBox({ children }: { children: ReactNode }) {
  return (
    <div className="info-box">
      <span className="info-circle">i</span>
      <div>{children}</div>
    </div>
  );
}
export function Copy({
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
    <button
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
    </button>
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

export function Modal({
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
    <div
      className={`modal-overlay${layout === "drawer" ? " modal-overlay-drawer" : ""}`}
      data-closing={closing || undefined}
      onPointerDown={(event) => {
        if (layout === "drawer" && event.target === event.currentTarget)
          requestClose();
      }}
    >
      <div
        ref={ref}
        tabIndex={-1}
        className={`modal ${wide ? "modal-wide" : `modal-${size}`}${layout === "drawer" ? " modal-drawer" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-header">
          <div>
            {headerIcon && <div className="modal-header-symbol">{headerIcon}</div>}
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button
            type="button"
            className="icon-btn"
            aria-label="关闭窗口"
            onClick={requestClose}
            disabled={closeDisabled || closing}
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>
        <div ref={body} className="modal-body" inert={closing || undefined}>
          {children}
        </div>
        {footer && (
          <div className="modal-footer" inert={closing || undefined}>
            {typeof footer === "function" ? footer(requestClose) : footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer(props: Omit<ModalProps, "layout">) {
  return <Modal {...props} layout="drawer" />;
}
export function Confirm({
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
        <span className={`confirm-symbol ${danger ? "danger" : ""}`}>
          {danger ? <AlertCircle size={40} /> : <CheckCircle2 size={40} />}
        </span>
      }
      footer={
        <>
          <Button size="medium" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
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
      <div className="confirm-content">{description}</div>
    </Modal>
  );
}
export function Segment({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string; icon?: ReactNode }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="segment">
      {options.map((x) => (
        <button
          type="button"
          aria-pressed={value === x.value}
          className={value === x.value ? "active" : ""}
          key={x.value}
          onClick={() => onChange(x.value)}
        >
          {x.icon}
          {x.label}
        </button>
      ))}
    </div>
  );
}
export function Select({
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
    <div className="select-wrap">
      <select
        disabled={disabled}
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {children}
      </select>
      <ChevronDown size={14} />
    </div>
  );
}
export function StatBar({
  value,
  kind = "orange",
}: {
  value: number | null | undefined;
  kind?: string;
}) {
  return (
    <div className="stat-bar-wrap">
      <div className="stat-bar">
        <div
          className={kind}
          style={{ width: `${Math.min(100, Math.max(0, value ?? 0))}%` }}
        />
      </div>
      <span>{value == null ? "—" : `${value.toFixed(0)}%`}</span>
    </div>
  );
}
export function JsonEditor({
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
    <div className="code-editor">
      <div className="code-editor-top">
        <span>
          <i />
          {label}
        </span>
        <span>JSON · UTF-8</span>
      </div>
      <div className="code-editor-content">
        <div className="line-numbers" aria-hidden="true">
          {Array.from({ length: Math.max(nums, rows) }, (_, i) => (
            <span key={i}>{i + 1}</span>
          ))}
        </div>
        <textarea
          aria-label={label}
          disabled={disabled}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={rows}
          spellCheck={false}
        />
      </div>
    </div>
  );
}
