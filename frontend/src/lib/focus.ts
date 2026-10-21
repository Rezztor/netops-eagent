/**
 * Focus mode: one conversation, the whole window.
 *
 * Kept in the URL rather than in component state, which makes "expand here" and
 * "open in a new tab" the same feature -- the button toggles the parameter, the
 * link opens it in a new tab, and both arrive at the same screen. It also
 * survives a reload and can be bookmarked, which component state cannot.
 */

export const FOCUS_PARAM = "focus";

export function isFocused(search: string | URLSearchParams): boolean {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  return params.get(FOCUS_PARAM) === "1";
}

/** The same location with focus turned on or off, preserving everything else. */
export function withFocus(search: string | URLSearchParams, focused: boolean): string {
  const params = new URLSearchParams(
    typeof search === "string" ? search : search.toString(),
  );
  if (focused) params.set(FOCUS_PARAM, "1");
  else params.delete(FOCUS_PARAM);
  const query = params.toString();
  return query ? `?${query}` : "";
}
