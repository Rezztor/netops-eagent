/**
 * Render a timestamp using the signed-in user's timezone and 12/24h preference.
 *
 * Wraps the pure helpers in lib/datetime.ts with the session preferences so a
 * screen writes `<DateTime value={row.created_at} />` instead of reaching for
 * `toLocaleString()`, which ignored those preferences entirely. The machine
 * value is exposed as a `title` (ISO) and a `<time dateTime>` for correctness.
 */

import {
  formatDate,
  formatDateTime,
  formatPrecise,
  formatRelative,
  formatTime,
  toIso,
  type DateInput,
} from "@/lib/datetime";
import { useSession } from "@/lib/session";

export type DateTimeMode = "datetime" | "date" | "time" | "precise" | "relative";

export function DateTime({
  value,
  mode = "datetime",
  fallback = "—",
}: {
  value: DateInput;
  mode?: DateTimeMode;
  fallback?: string;
}) {
  const { preferences } = useSession();

  const iso = toIso(value);
  if (!iso) return <>{fallback}</>;

  let text: string;
  switch (mode) {
    case "date":
      text = formatDate(value, preferences);
      break;
    case "time":
      text = formatTime(value, preferences);
      break;
    case "precise":
      text = formatPrecise(value, preferences);
      break;
    case "relative":
      text = formatRelative(value);
      break;
    default:
      text = formatDateTime(value, preferences);
  }

  return (
    <time dateTime={iso} title={formatPrecise(value, preferences)}>
      {text}
    </time>
  );
}
