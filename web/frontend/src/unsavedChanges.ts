import { useLayoutEffect, useRef } from "react";

const drafts = new Map<symbol, boolean>();
export const hasUnsavedChanges = () => [...drafts.values()].some(Boolean);
export const discardNavigationDrafts = () => drafts.clear();

/** A page draft participates in SPA navigation and the browser's unload protection. */
export function useUnsavedChanges(dirty: boolean) {
  const identity = useRef(Symbol("page-draft"));
  useLayoutEffect(() => {
    const key = identity.current;
    drafts.set(key, dirty);
    const unload = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges()) return;
      event.preventDefault(); event.returnValue = "";
    };
    window.addEventListener("beforeunload", unload);
    return () => { drafts.delete(key); window.removeEventListener("beforeunload", unload); };
  }, [dirty]);
}
