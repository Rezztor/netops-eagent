/**
 * Load data from the API into the three states a screen actually has.
 *
 * Deliberately small. A data-fetching library would be more capable, but every
 * screen here does the same thing -- load once, show an error, allow a retry --
 * and a dependency that has to be understood before anyone can read a screen is
 * a poor trade for that.
 */

import { useCallback, useEffect, useState } from "react";

export type AsyncState<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: Error };

export function useAsync<T>(
  load: () => Promise<T>,
  deps: unknown[] = [],
): { state: AsyncState<T>; reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  const [nonce, setNonce] = useState(0);

  // Callers pass an inline closure, so the identity changes every render. The
  // dependency key is the caller's own list plus the reload nonce, joined so it
  // is a stable primitive rather than a new array each time.
  const key = JSON.stringify(deps);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;

    // Not reset to "loading" here: doing that synchronously inside the effect
    // causes an extra render pass, and a reload that briefly blanks a screen
    // already showing correct data reads as a flicker rather than progress.
    load()
      .then((data) => {
        if (!cancelled) setState({ status: "ready", data });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState({
          status: "error",
          error: error instanceof Error ? error : new Error(String(error)),
        });
      });

    return () => {
      cancelled = true;
    };
    // `load` is intentionally excluded: it is a new closure on every render and
    // including it would refetch in a loop. `key` captures what it depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  return { state, reload };
}
