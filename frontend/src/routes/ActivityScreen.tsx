/**
 * Recent activity.
 *
 * Shows the in-memory tail from plugins/_audit, which is deliberately not the
 * audit record: the retained trail is the stdout stream shipped to the SIEM,
 * and it is trustworthy precisely because the application cannot rewrite it.
 * This page says that plainly rather than letting an operator mistake a capped
 * live window for a complete history.
 */

import { useEffect, useMemo, useState } from "react";

import { apiRequest } from "@/api/client";
import {
  Button,
  Card,
  CenteredMessage,
  DateTime,
  Drawer,
  EmptyRow,
  LoadFailed,
  Mono,
  Row,
  ScopeNote,
  SearchInput,
  Select,
  Table,
  Tag,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useTableControls } from "@/lib/useTableControls";
import styles from "./shell.module.css";

interface AuditActor {
  user_id?: string;
  username?: string;
  role?: string;
}

interface AuditEvent {
  ts?: string;
  event?: string;
  actor?: AuditActor;
  outcome?: string;
  tool?: string;
  duration_ms?: number;
  [key: string]: unknown;
}

interface RecentResponse {
  ok: boolean;
  events: AuditEvent[];
  buffered: number;
  capacity: number;
  is_complete_record: boolean;
  note: string;
}

const LIMITS = [100, 250, 500, 1000];
const REFRESH_MS = 5000;

function toneFor(event: AuditEvent): "ok" | "warn" | "crit" | "muted" {
  const outcome = String(event.outcome ?? "");
  if (outcome === "error" || outcome === "denied") return "crit";
  if (outcome === "success" || outcome === "ok") return "ok";
  if (String(event.event ?? "").startsWith("auth.")) return "warn";
  return "muted";
}

const eventSearch = (event: AuditEvent) => [
  event.event,
  event.actor?.username,
  event.actor?.role,
  event.tool,
  event.outcome,
];

/** Client-side file download; the buffer is small and already in the browser. */
function download(filename: string, mime: string, content: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function toCsv(events: AuditEvent[]): string {
  const columns = ["ts", "event", "actor", "role", "tool", "duration_ms", "outcome"];
  // A spreadsheet runs a cell that starts with = + - or @; a leading apostrophe makes it text.
  const escape = (value: unknown) => {
    const text = String(value ?? "");
    return `"${(/^[=+\-@\t\r]/.test(text) ? `'${text}` : text).replace(/"/g, '""')}"`;
  };
  const rows = events.map((event) =>
    [
      event.ts,
      event.event,
      event.actor?.username,
      event.actor?.role,
      event.tool,
      event.duration_ms,
      event.outcome,
    ]
      .map(escape)
      .join(","),
  );
  return [columns.join(","), ...rows].join("\n");
}

export function ActivityScreen() {
  const [limit, setLimit] = useState(100);
  const [outcome, setOutcome] = useState("");
  const [live, setLive] = useState(false);
  const [detail, setDetail] = useState<AuditEvent | null>(null);

  const { state, reload } = useAsync(
    () => apiRequest<RecentResponse>("plugins/_audit/audit_recent", { body: { limit } }),
    [limit],
  );

  // Optional live refresh: the window is capped and cleared on restart, so a
  // periodic poll is cheap and never grows unbounded.
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(reload, REFRESH_MS);
    return () => clearInterval(timer);
  }, [live, reload]);

  const events = useMemo(() => (state.status === "ready" ? state.data.events : []), [state]);
  const outcomes = useMemo(
    () => Array.from(new Set(events.map((event) => String(event.outcome ?? "")).filter(Boolean))).sort(),
    [events],
  );

  const controls = useTableControls(events, {
    search: eventSearch,
    ...(outcome ? { filter: (event: AuditEvent) => String(event.outcome ?? "") === outcome } : {}),
  });

  if (state.status === "loading") return <CenteredMessage title="Loading activity…" />;
  if (state.status === "error") {
    return (
      <LoadFailed what="activity" error={state.error} onRetry={reload} />
    );
  }

  const { buffered, capacity, note } = state.data;

  return (
    <div className={styles.stack}>
      <ScopeNote tone="warn">
        <div>
          <strong>This is not the audit record.</strong> {note}
        </div>
      </ScopeNote>

      <Card
        title="Recent events"
        description={`${buffered} of a ${capacity}-event window · ${controls.matched} shown`}
        actions={
          <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "var(--text-sm)" }}>
              <input type="checkbox" checked={live} onChange={(event) => setLive(event.target.checked)} />
              Live
            </label>
            <Button
              disabled={controls.filtered.length === 0}
              onClick={() => download("activity.json", "application/json", JSON.stringify(controls.filtered, null, 2))}
            >
              Export JSON
            </Button>
            <Button
              disabled={controls.filtered.length === 0}
              onClick={() => download("activity.csv", "text/csv", toCsv(controls.filtered))}
            >
              Export CSV
            </Button>
            <Button onClick={reload}>Refresh</Button>
          </div>
        }
      >
        <div
          style={{
            display: "flex",
            gap: "var(--space-2)",
            flexWrap: "wrap",
            alignItems: "center",
            padding: "var(--space-3) 0",
          }}
        >
          <SearchInput
            value={controls.query}
            onChange={controls.setQuery}
            placeholder="Search events, actors, tools…"
            label="Search activity"
          />
          <Select value={outcome} onChange={(event) => setOutcome(event.target.value)} aria-label="Filter by outcome">
            <option value="">All outcomes</option>
            {outcomes.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
          <Select
            value={String(limit)}
            onChange={(event) => setLimit(Number(event.target.value))}
            aria-label="Number of events"
          >
            {LIMITS.map((value) => (
              <option key={value} value={value}>
                Last {value}
              </option>
            ))}
          </Select>
        </div>

        <Table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Event</th>
              <th>Actor</th>
              <th>Detail</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {controls.rows.length === 0 ? (
              <EmptyRow colSpan={5}>
                {events.length === 0
                  ? "Nothing buffered. The window is cleared on restart, so this is empty until something happens — it does not mean nothing ever did."
                  : "No events match your filters."}
              </EmptyRow>
            ) : (
              controls.rows.map((event, index) => (
                <tr
                  key={`${event.ts ?? index}-${index}`}
                  onClick={() => setDetail(event)}
                  style={{ cursor: "pointer" }}
                >
                  <td style={{ color: "var(--ink-3)", whiteSpace: "nowrap" }}>
                    {event.ts ? <DateTime value={event.ts} mode="precise" /> : "—"}
                  </td>
                  <td>
                    <Mono>{event.event ?? "—"}</Mono>
                  </td>
                  <td>
                    {event.actor?.username ?? <span style={{ color: "var(--ink-3)" }}>system</span>}
                    {event.actor?.role ? (
                      <div style={{ color: "var(--ink-3)" }}>{event.actor.role}</div>
                    ) : null}
                  </td>
                  <td style={{ color: "var(--ink-2)" }}>
                    {event.tool ? <Mono>{event.tool}</Mono> : "—"}
                    {typeof event.duration_ms === "number" ? (
                      <span style={{ color: "var(--ink-3)" }}> · {event.duration_ms}ms</span>
                    ) : null}
                  </td>
                  <td>
                    <Tag tone={toneFor(event)}>{event.outcome ?? "—"}</Tag>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </Table>
      </Card>

      {detail && (
        <Drawer title={detail.event || "Event"} onClose={() => setDetail(null)}>
          <Row label="Time">
            {detail.ts ? <DateTime value={detail.ts} mode="precise" /> : "—"}
          </Row>
          <Row label="Actor">
            {detail.actor?.username ?? "system"}
            {detail.actor?.role ? ` · ${detail.actor.role}` : ""}
          </Row>
          <Row label="Outcome">
            <Tag tone={toneFor(detail)}>{detail.outcome ?? "—"}</Tag>
          </Row>
          <h3 style={{ fontSize: "var(--text-sm)", color: "var(--ink-2)", margin: "var(--space-3) 0 var(--space-1)" }}>
            Full record
          </h3>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              fontSize: "var(--text-xs)",
              background: "var(--surface-alt)",
              padding: "var(--space-2)",
              borderRadius: "var(--radius-sm)",
              overflow: "auto",
              margin: 0,
            }}
          >
            {JSON.stringify(detail, null, 2)}
          </pre>
        </Drawer>
      )}
    </div>
  );
}
