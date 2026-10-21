/**
 * Sync a single value to a URL query parameter.
 *
 * The existing `withFocus` helper (lib/focus.ts) proved the pattern: state that
 * lives in the URL survives a reload, can be bookmarked, and makes "open in a
 * new tab" free. This generalises it to any string-ish value so screens can put
 * the selected conversation, the current path, the active tab or a filter in
 * the address bar instead of in component state that a refresh throws away.
 *
 * A value equal to `defaultValue` is written as an absent parameter, so the
 * common case keeps a clean URL.
 */

import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

export function useUrlState(
  key: string,
  defaultValue = "",
): [string, (next: string) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(key) ?? defaultValue;

  const setValue = useCallback(
    (next: string) => {
      setParams(
        (prev) => {
          const updated = new URLSearchParams(prev);
          if (!next || next === defaultValue) updated.delete(key);
          else updated.set(key, next);
          return updated;
        },
        { replace: true },
      );
    },
    [key, defaultValue, setParams],
  );

  return [value, setValue];
}

/** Like {@link useUrlState} but for an enumerated set of allowed values. */
export function useUrlEnum<T extends string>(
  key: string,
  allowed: readonly T[],
  defaultValue: T,
): [T, (next: T) => void] {
  const [raw, setRaw] = useUrlState(key, defaultValue);
  const value = (allowed as readonly string[]).includes(raw) ? (raw as T) : defaultValue;
  const setValue = useCallback((next: T) => setRaw(next), [setRaw]);
  return [value, setValue];
}
