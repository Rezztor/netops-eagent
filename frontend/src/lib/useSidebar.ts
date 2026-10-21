/**
 * Collapsible sidebar: pinned vs icon rail, peek overlay, and shortcuts.
 */

import { useCallback, useEffect, useState } from "react";

export const SIDEBAR_PINNED_KEY = "eagent.sidebar.pinned";

/** True when a keyboard shortcut should leave the focused field alone. */
export function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return el.isContentEditable || el.getAttribute("contenteditable") === "true";
}

function readPinned(): boolean {
  try {
    const value = localStorage.getItem(SIDEBAR_PINNED_KEY);
    if (value === "0") return false;
    if (value === "1") return true;
  } catch {
    /* storage may throw; page must still work */
  }
  return true;
}

function writePinned(pinned: boolean): void {
  try {
    localStorage.setItem(SIDEBAR_PINNED_KEY, pinned ? "1" : "0");
  } catch {
    /* ignore */
  }
}

export function useSidebar() {
  const [pinned, setPinnedState] = useState(readPinned);
  const [peeking, setPeeking] = useState(false);

  const setPinned = useCallback((next: boolean) => {
    setPinnedState(next);
    writePinned(next);
    if (next) setPeeking(false);
  }, []);

  const toggle = useCallback(() => {
    setPinnedState((prev) => {
      const next = !prev;
      writePinned(next);
      return next;
    });
    setPeeking(false);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTypingTarget(event.target)) return;
      const bracket = event.key === "[";
      const modB =
        (event.key === "b" || event.key === "B") && (event.ctrlKey || event.metaKey);
      if (!bracket && !modB) return;
      event.preventDefault();
      toggle();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toggle]);

  return { pinned, setPinned, toggle, peeking, setPeeking };
}
