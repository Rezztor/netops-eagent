/**
 * Timestamp formatting that honours the signed-in user's preferences.
 *
 * Every screen used to reach for `new Date(x).toLocaleString()`, which silently
 * ignores the timezone and 12/24h choice the user actually set (delivered in
 * the bootstrap payload as `preferences.timezone` / `preferences.time_format`).
 * These helpers are the single place that maps those preferences onto
 * `Intl.DateTimeFormat`, so a timestamp reads the same wherever it appears.
 *
 * `timezone` is either an IANA zone (e.g. "UTC", "Europe/London") or the
 * sentinel "auto", which means "use the browser's zone" -- matching
 * TIMEZONE_AUTO in helpers/settings.py. `time_format` is "12h" or "24h".
 */

export interface TimePreferences {
  timezone: string;
  time_format: string;
}

const AUTO_TIMEZONE = "auto";

/** Milliseconds/seconds epoch numbers, ISO strings and Dates all land here. */
export type DateInput = string | number | Date | null | undefined;

export function toDate(value: DateInput): Date | null {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  // Bare epoch seconds are common in the backend payloads; anything below this
  // threshold (roughly the year 2001 in ms) is far more likely seconds.
  if (typeof value === "number") {
    const ms = value < 1e11 ? value * 1000 : value;
    const date = new Date(ms);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function baseOptions(prefs: TimePreferences): Intl.DateTimeFormatOptions {
  const options: Intl.DateTimeFormatOptions = { hour12: prefs.time_format !== "24h" };
  if (prefs.timezone && prefs.timezone !== AUTO_TIMEZONE) {
    options.timeZone = prefs.timezone;
  }
  return options;
}

function format(value: DateInput, prefs: TimePreferences, extra: Intl.DateTimeFormatOptions): string {
  const date = toDate(value);
  if (!date) return "";
  try {
    return new Intl.DateTimeFormat(undefined, { ...baseOptions(prefs), ...extra }).format(date);
  } catch {
    // An invalid saved timezone should degrade to the browser default rather
    // than throw and blank the whole screen.
    const { timeZone: _dropped, ...withoutZone } = { ...baseOptions(prefs), ...extra };
    void _dropped;
    return new Intl.DateTimeFormat(undefined, withoutZone).format(date);
  }
}

/** Date and time, e.g. "13 Sep 2026, 14:30". The default for most tables. */
export function formatDateTime(value: DateInput, prefs: TimePreferences): string {
  return format(value, prefs, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Date only, e.g. "13 Sep 2026". */
export function formatDate(value: DateInput, prefs: TimePreferences): string {
  return format(value, prefs, { year: "numeric", month: "short", day: "2-digit" });
}

/** Time only, e.g. "14:30". */
export function formatTime(value: DateInput, prefs: TimePreferences): string {
  return format(value, prefs, { hour: "2-digit", minute: "2-digit" });
}

/** Full precision including seconds, for detail views and audit rows. */
export function formatPrecise(value: DateInput, prefs: TimePreferences): string {
  return format(value, prefs, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** A machine-precise ISO string for tooltips, or "" when the input is empty. */
export function toIso(value: DateInput): string {
  const date = toDate(value);
  return date ? date.toISOString() : "";
}

/**
 * A compact relative phrase ("just now", "3 min ago", "in 2 h").
 *
 * Falls back to nothing for inputs more than a day out, where an absolute date
 * is clearer than "yesterday"-style phrasing.
 */
export function formatRelative(value: DateInput, now: number = Date.now()): string {
  const date = toDate(value);
  if (!date) return "";
  const deltaMs = date.getTime() - now;
  const abs = Math.abs(deltaMs);
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (abs < 45_000) return "just now";
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "short" });
  if (abs < hour) return rtf.format(Math.round(deltaMs / minute), "minute");
  if (abs < day) return rtf.format(Math.round(deltaMs / hour), "hour");
  return rtf.format(Math.round(deltaMs / day), "day");
}
