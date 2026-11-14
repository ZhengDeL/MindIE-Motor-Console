import { getUiSdk, definePlugin, defineSlot, provideSlot } from "@mindie-motor/ui-sdk";
import type { UiChangeEvent, UiPageComponent, UiPageProps } from "@mindie-motor/ui-sdk";

const sdk = getUiSdk(), R = sdk.React, h = sdk.createElement;
type Note = { id: string; text: string; created_at: string };
type Notes = { items: Note[]; total: number; limit: number; offset: number; has_more: boolean };
type NoteDisplay = { format(note: Note): string };
const displaySlot = defineSlot<NoteDisplay>("example.notes-ui.display");
const pageSlot = defineSlot<UiPageComponent>("example.notes-ui.page");
const endpoint = "/plugins/example-notes/notes";
function NotesPage({ page }: UiPageProps) {
  const host = sdk.useHost(), display = sdk.useSlot(displaySlot);
  const [notes, setNotes] = R.useState<Notes | null>(null), [text, setText] = R.useState("");
  const [busy, setBusy] = R.useState(false), [error, setError] = R.useState("");
  const [message, setMessage] = R.useState(""), [unknown, setUnknown] = R.useState(false);
  const inFlight = R.useRef(false);
  const canWrite = host.meta?.mode === "control" && ["admin", "operator"].includes(host.auth?.user?.role ?? "");
  sdk.useUnsavedChanges(!!text || busy || unknown);
  async function load(signal?: AbortSignal) {
    setNotes(await sdk.client.api<Notes>(endpoint, { signal }));
  }
  R.useEffect(() => {
    const abort = new AbortController();
    void load(abort.signal).catch(cause => { if (!abort.signal.aborted) setError(String(cause)); });
    return () => abort.abort();
  }, []);
  async function save() {
    if (!canWrite || inFlight.current || unknown || !text.trim()) return;
    inFlight.current = true; setBusy(true); setError(""); setMessage("");
    try {
      await sdk.client.post<{ item: Note }>(endpoint, { text });
      setText(""); setMessage("便笺已保存"); await load();
    } catch (cause) {
      const failure = cause as Error & { outcomeUnknown?: boolean };
      setUnknown(failure.outcomeUnknown === true);
      setError(failure.message + (failure.outcomeUnknown ? "；结果尚未确认，请先读取已有便笺核对。" : ""));
    } finally { inFlight.current = false; setBusy(false); }
  }
  return h("section", { className: "panel", "data-independent-ui-page": page.id },
    h("div", { className: "panel-head" }, h("h2", null, page.title)),
    h("div", { className: "settings-content" },
      canWrite && h("label", null, "便笺内容", h("textarea", { rows: 5, maxLength: 2000, value: text,
        "aria-label": "便笺内容", disabled: busy || unknown,
        onChange: (event: UiChangeEvent<HTMLTextAreaElement>) => setText(event.target.value) })),
      canWrite && h(sdk.UiButton, { actionId: "example.notes-ui.save", className: "btn btn-primary",
        disabled: busy || unknown || !text.trim(), onClick: () => void save() }, busy ? "正在保存" : "保存便笺"),
      h(sdk.UiButton, { actionId: "example.notes-ui.refresh", className: "btn", disabled: busy,
        onClick: () => { void load().catch(cause => setError(String(cause))); } }, "读取便笺"),
      message && h("p", { role: "status" }, message),
      error && h("p", { role: "alert", className: "error-box" }, error),
      notes && h("p", null, `共 ${notes.total} 条，当前显示 ${notes.items.length} 条`),
      h("ul", { "aria-label": "便笺列表" }, notes?.items.map(note => h("li", { key: note.id }, display.format(note))))));
}
export default definePlugin({ id: "example.notes-ui", version: "1.0.0",
  providers: { ...provideSlot(pageSlot, NotesPage), ...provideSlot(displaySlot, { format: note => note.text }) } });
