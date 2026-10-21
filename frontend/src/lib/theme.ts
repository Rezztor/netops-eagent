/**
 * Theme selection.
 *
 * Three states, matching the token file: "system" stamps nothing on <html> and
 * lets prefers-color-scheme decide, "light" and "dark" stamp data-theme and win
 * over the OS in both directions.
 *
 * Stored per browser in localStorage, which can throw outright in some contexts
 * (private windows, blocked site data), so every access is guarded and the
 * page renders correctly with no stored value.
 */

export type ThemeChoice = "system" | "light" | "dark";

const STORAGE_KEY = "eagent:theme";

export function readThemeChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Storage unavailable; fall through to the system default.
  }
  return "system";
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", choice);
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    // A theme that does not persist is still a theme that applies.
  }
}

/** Apply the stored choice before first paint. */
export function initTheme(): void {
  applyTheme(readThemeChoice());
}
