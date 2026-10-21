/**
 * Track "busy" per row/action instead of freezing a whole table.
 *
 * Screens used to flip a single global `busy` boolean while any mutation was in
 * flight, which disabled every control on the page even though only one row was
 * acting. This keeps a set of in-flight ids so a spinner and disabled state can
 * be scoped to the row (or the specific action) that is actually working.
 */

import { useCallback, useRef, useState } from "react";

export interface RowBusy {
  /** True while the given id has an action in flight. */
  isBusy: (id: string) => boolean;
  /** True while any id is busy -- handy for a page-level "working" hint. */
  anyBusy: boolean;
  /**
   * Run an async action under a busy id. Re-entrancy on the same id is ignored
   * so a double click cannot fire the same mutation twice. The action's result
   * is returned; errors propagate so callers can surface them via a toast.
   */
  run: <T>(id: string, action: () => Promise<T>) => Promise<T | undefined>;
}

export function useRowBusy(): RowBusy {
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());
  // The re-entrancy guard has to be decided synchronously at the call site: a
  // state updater's body runs during reconciliation, not on the line that
  // schedules it, so a flag set inside `setBusy(...)` is not readable straight
  // afterwards (and under React's StrictMode the updater is invoked twice).
  // A ref gives us an authoritative "is this id already running" answer now,
  // while the Set state below drives what the row renders.
  const inFlight = useRef<Set<string>>(new Set());

  const isBusy = useCallback((id: string) => busy.has(id), [busy]);

  const run = useCallback(
    async <T,>(id: string, action: () => Promise<T>): Promise<T | undefined> => {
      if (inFlight.current.has(id)) return undefined;
      inFlight.current.add(id);
      setBusy((prev) => {
        const next = new Set(prev);
        next.add(id);
        return next;
      });
      try {
        return await action();
      } finally {
        inFlight.current.delete(id);
        setBusy((prev) => {
          if (!prev.has(id)) return prev;
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }
    },
    [],
  );

  return { isBusy, anyBusy: busy.size > 0, run };
}
