/**
 * A signed-in user's own reminders and schedules: what's coming up, what's
 * running, what already happened, and the full list underneath.
 *
 * Personal and read-first. Creating a schedule happens by asking the agent in
 * chat -- this screen is for seeing what already exists and lightly managing
 * it (run now, pause, resume, delete), not authoring one from a form.
 */

import { useState, type ReactNode } from "react";

import {
  deleteMySchedule,
  listMySchedules,
  pauseMySchedule,
  resumeMySchedule,
  runMySchedule,
  taskId,
  type ScheduledTask,
} from "@/api/schedules";
import {
  Button,
  Card,
  DateTime,
  EmptyRow,
  Mono,
  ScopeNote,
  SearchInput,
  Select,
  Table,
  Tag,
  useConfirm,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useRowBusy } from "@/lib/useRowBusy";
import { useTableControls } from "@/lib/useTableControls";
import { useSession } from "@/lib/session";
import shell from "./shell.module.css";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function formatClock(hour: string, minute: string): string | null {
  const h = Number(hour);
  const m = Number(minute);
  if (!Number.isInteger(h) || !Number.isInteger(m) || h < 0 || h > 23 || m < 0 || m > 59) return null;
  const period = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m).padStart(2, "0")} ${period}`;
}

/** Cron -> plain English for the common shapes the agent actually produces.
 * Anything it doesn't recognise falls back to the raw crontab fields rather
 * than risking a description that doesn't match what will actually run. */
function describeSchedule(task: ScheduledTask): string {
  if (task.type === "adhoc") return "Runs when triggered";
  if (task.type === "planned") return "One-time reminder";

  const schedule = task.schedule as
    | { minute?: string; hour?: string; day?: string; month?: string; weekday?: string }
    | undefined;
  if (!schedule) return "Recurring";
  const { minute = "*", hour = "*", day = "*", month = "*", weekday = "*" } = schedule;
  const clock = formatClock(hour, minute);
  const raw = `${minute} ${hour} ${day} ${month} ${weekday}`;
  if (!clock || day !== "*" || month !== "*") return raw;

  if (weekday === "*") return `Every day, ${clock}`;
  if (weekday === "1-5") return `Weekdays, ${clock}`;
  if (weekday === "0,6" || weekday === "6,0") return `Weekends, ${clock}`;
  const names = weekday.split(",").map((w) => WEEKDAYS[Number(w.trim())]);
  if (names.length > 0 && names.every(Boolean)) return `${names.join(", ")}, ${clock}`;
  return raw;
}

function typeLabel(type?: string): string {
  if (type === "scheduled") return "Recurring";
  if (type === "planned") return "One-time";
  if (type === "adhoc") return "On demand";
  return type || "—";
}

function relativeFuture(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const diffMs = new Date(iso).getTime() - Date.now();
  if (diffMs <= 0) return null;
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `in ${hours}h`;
  const days = Math.round(hours / 24);
  if (days < 14) return `in ${days}d`;
  return new Date(iso).toLocaleDateString();
}

function relativePast(iso: string | null | undefined): string {
  if (!iso) return "never";
  const diffMs = Date.now() - new Date(iso).getTime();
  if (diffMs < 0) return new Date(iso).toLocaleString();
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 14) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function stateTone(state?: string): "ok" | "warn" | "crit" | "accent" | "muted" {
  if (state === "running") return "accent";
  if (state === "error") return "crit";
  if (state === "disabled") return "muted";
  return "ok";
}

function stateLabel(state?: string): string {
  if (state === "running") return "Running";
  if (state === "error") return "Error";
  if (state === "disabled") return "Paused";
  return "Idle";
}

function Muted({ children }: { children: ReactNode }) {
  return (
    <p style={{ padding: "var(--space-3) 0", color: "var(--ink-3)", fontSize: "var(--text-sm)", margin: 0 }}>
      {children}
    </p>
  );
}

function Dot({ tone }: { tone: "accent" | "ok" | "crit" }) {
  return (
    <span
      style={{
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: `var(--${tone})`,
        flex: "none",
        display: "inline-block",
        marginTop: 5,
      }}
      aria-hidden="true"
    />
  );
}

function Metric({
  label,
  value,
  tone,
  foot,
}: {
  label: string;
  value: ReactNode;
  tone?: "accent" | "crit" | undefined;
  foot?: ReactNode;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--line)",
        borderRadius: "var(--radius)",
        background: "var(--surface)",
        boxShadow: "var(--shadow)",
        padding: "var(--space-4)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        minWidth: 0,
      }}
    >
      <div style={{ fontSize: "var(--text-sm)", color: "var(--ink-2)", fontWeight: 550 }}>{label}</div>
      <div
        style={{
          fontSize: "25px",
          fontWeight: 700,
          letterSpacing: "-.025em",
          color: tone === "accent" ? "var(--accent)" : tone === "crit" ? "var(--crit)" : undefined,
        }}
      >
        {value}
      </div>
      {foot && (
        <div style={{ fontSize: "var(--text-xs)", color: "var(--ink-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {foot}
        </div>
      )}
    </div>
  );
}

export function SchedulesScreen() {
  const session = useSession();
  const timezone = session.preferences.timezone;
  const { state, reload } = useAsync<ScheduledTask[]>(() => listMySchedules(timezone), [timezone]);
  const rowBusy = useRowBusy();
  const toast = useToast();

  // One acted-on row, not the whole table: only that id is disabled while its
  // action is in flight, so an unrelated schedule stays operable.
  async function act(id: string, action: () => Promise<{ error?: string }>, success: string) {
    await rowBusy.run(id, async () => {
      try {
        const result = await action();
        if (result.error) throw new Error(result.error);
        toast.success(success);
        reload();
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : String(caught));
      }
    });
  }

  return (
    <div className={shell.stack}>
      <ScopeNote>
        <div>
          <strong>Set these up from chat.</strong> Ask the agent to remind you about something, or to check on
          something regularly, and it will appear here. This page only shows schedules tied to your account.
        </div>
      </ScopeNote>

      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && (
        <Card title="Could not load your schedules" danger>
          <p style={{ padding: "var(--space-3) 0" }}>{state.error.message}</p>
          <Button onClick={reload}>Retry</Button>
        </Card>
      )}
      {state.status === "ready" && (
        <SchedulesBody
          tasks={state.data}
          timezone={timezone}
          isBusy={rowBusy.isBusy}
          act={act}
          reload={reload}
        />
      )}
    </div>
  );
}

/** How many upcoming rows to show before the table below is the better view. */
const MAX_COMING_UP = 8;

function SchedulesBody({
  tasks,
  timezone,
  isBusy,
  act,
  reload,
}: {
  tasks: ScheduledTask[];
  timezone: string;
  isBusy: (id: string) => boolean;
  act: (id: string, action: () => Promise<{ error?: string }>, success: string) => Promise<void>;
  reload: () => void;
}) {
  const { confirm } = useConfirm();
  const active = tasks.filter((t) => t.state !== "disabled");
  const paused = tasks.length - active.length;
  const running = tasks.filter((t) => t.state === "running");
  const needsAttention = tasks.filter((t) => t.state === "error");
  const comingUp = tasks
    .filter((t) => t.state === "idle" && t.next_run)
    .sort((a, b) => new Date(a.next_run as string).getTime() - new Date(b.next_run as string).getTime());
  const recent = tasks
    .filter((t) => t.last_run)
    .sort((a, b) => new Date(b.last_run as string).getTime() - new Date(a.last_run as string).getTime())
    .slice(0, 6);
  const nextUp = comingUp[0];

  if (tasks.length === 0) {
    return (
      <Card title="Nothing here yet">
        <Muted>
          No reminders or schedules yet. Ask the agent in chat to remind you about something, or to check on
          something regularly, and it will show up here.
        </Muted>
      </Card>
    );
  }

  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: "var(--space-3)" }}>
        <Metric label="Active" value={active.length} foot={paused ? `${paused} paused` : undefined} />
        <Metric
          label="Running now"
          value={running.length}
          tone={running.length ? "accent" : undefined}
          foot={running[0]?.name}
        />
        <Metric label="Next up" value={nextUp ? relativeFuture(nextUp.next_run as string) : "—"} foot={nextUp?.name} />
        <Metric
          label="Needs attention"
          value={needsAttention.length}
          tone={needsAttention.length ? "crit" : undefined}
          foot={needsAttention[0]?.name}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: "var(--space-4)", alignItems: "start" }}>
        <div className={shell.stack}>
          <Card
            title="Coming up"
            description={
              comingUp.length === 0
                ? "Nothing scheduled right now."
                : comingUp.length > MAX_COMING_UP
                  ? `Next ${MAX_COMING_UP} of ${comingUp.length}.`
                  : undefined
            }
          >
            {comingUp.slice(0, MAX_COMING_UP).map((task) => (
              <div
                key={taskId(task)}
                style={{ display: "flex", gap: "var(--space-3)", padding: "var(--space-2) 0", borderTop: "1px solid var(--line)" }}
              >
                <div style={{ width: 48, flex: "none", fontVariantNumeric: "tabular-nums", fontWeight: 700, fontSize: "var(--text-md)" }}>
                  {relativeFuture(task.next_run as string)}
                </div>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: "var(--text-base)" }}>{task.name || "Untitled"}</div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--ink-3)" }}>
                    {typeLabel(task.type)} · {describeSchedule(task)}
                  </div>
                </div>
              </div>
            ))}
          </Card>

          <Card title="Running now" description={running.length ? undefined : "Nothing running right now."}>
            {running.map((task) => (
              <div
                key={taskId(task)}
                style={{ display: "flex", gap: "var(--space-3)", padding: "var(--space-2) 0", borderTop: "1px solid var(--line)" }}
              >
                <Dot tone="accent" />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: "var(--text-base)" }}>{task.name || "Untitled"}</div>
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--ink-3)" }}>
                    {typeLabel(task.type)} · {describeSchedule(task)}
                  </div>
                </div>
              </div>
            ))}
          </Card>
        </div>

        <Card title="Recent activity" description={recent.length ? undefined : "Nothing has run yet."}>
          {recent.map((task) => (
            <div
              key={taskId(task)}
              style={{ display: "flex", gap: "var(--space-3)", padding: "var(--space-2) 0", borderTop: "1px solid var(--line)" }}
            >
              <Dot tone={task.state === "error" ? "crit" : "ok"} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: "var(--text-sm)" }}>{task.name || "Untitled"}</div>
                {task.last_result && (
                  <div style={{ fontSize: "var(--text-xs)", color: "var(--ink-2)", marginTop: 2 }}>
                    {String(task.last_result).slice(0, 160)}
                  </div>
                )}
                <div style={{ fontSize: "var(--text-xs)", color: "var(--ink-3)", marginTop: 2 }}>
                  {relativePast(task.last_run as string)}
                </div>
              </div>
            </div>
          ))}
        </Card>
      </div>

      <AllSchedules
        tasks={tasks}
        timezone={timezone}
        isBusy={isBusy}
        act={act}
        reload={reload}
        confirm={confirm}
      />
    </>
  );
}

function AllSchedules({
  tasks,
  timezone,
  isBusy,
  act,
  reload,
  confirm,
}: {
  tasks: ScheduledTask[];
  timezone: string;
  isBusy: (id: string) => boolean;
  act: (id: string, action: () => Promise<{ error?: string }>, success: string) => Promise<void>;
  reload: () => void;
  confirm: ReturnType<typeof useConfirm>["confirm"];
}) {
  const [stateFilter, setStateFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");

  const controls = useTableControls(tasks, {
    search: (task) => [task.name, describeSchedule(task), typeLabel(task.type)],
    filter: (task) => {
      if (stateFilter !== "all") {
        const paused = task.state === "disabled";
        if (stateFilter === "paused" && !paused) return false;
        if (stateFilter !== "paused" && task.state !== stateFilter) return false;
      }
      if (typeFilter !== "all" && task.type !== typeFilter) return false;
      return true;
    },
  });

  return (
    <Card
      title="All reminders & schedules"
      description="Everything tied to your account."
      actions={<Button onClick={reload}>Refresh</Button>}
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
          placeholder="Search schedules…"
          label="Search schedules"
        />
        <Select value={stateFilter} onChange={(e) => setStateFilter(e.target.value)} aria-label="Filter by state">
          <option value="all">All states</option>
          <option value="idle">Idle</option>
          <option value="running">Running</option>
          <option value="error">Error</option>
          <option value="paused">Paused</option>
        </Select>
        <Select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} aria-label="Filter by type">
          <option value="all">All types</option>
          <option value="scheduled">Recurring</option>
          <option value="planned">One-time</option>
          <option value="adhoc">On demand</option>
        </Select>
      </div>
      <Table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>State</th>
            <th>Next run</th>
            <th>Last run</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {controls.rows.length === 0 ? (
            <EmptyRow colSpan={6}>No schedules match these filters.</EmptyRow>
          ) : (
            controls.rows.map((task) => {
              const id = taskId(task);
              const busy = isBusy(id);
              const canRun = task.state === "idle" || task.state === "error";
              const canPause = task.type !== "adhoc" && (task.state === "idle" || task.state === "error");
              const canResume = task.state === "disabled";
              return (
                <tr key={id}>
                  <td>
                    <strong>{task.name || "Untitled"}</strong>
                    <div style={{ color: "var(--ink-3)", fontSize: "var(--text-xs)" }}>{describeSchedule(task)}</div>
                  </td>
                  <td>
                    <Tag tone="muted">{typeLabel(task.type)}</Tag>
                  </td>
                  <td>
                    <Tag tone={stateTone(task.state)}>{stateLabel(task.state)}</Tag>
                  </td>
                  <td style={{ color: "var(--ink-3)" }}>
                    {task.state === "disabled" ? (
                      <Mono>paused</Mono>
                    ) : task.state === "error" ? (
                      <Mono>won&rsquo;t retry automatically</Mono>
                    ) : task.next_run ? (
                      <DateTime value={task.next_run as string} />
                    ) : (
                      <Mono>—</Mono>
                    )}
                  </td>
                  <td style={{ color: "var(--ink-3)" }}>
                    {task.last_run ? <DateTime value={task.last_run as string} /> : <Mono>never</Mono>}
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {canRun && (
                      <Button
                        disabled={busy}
                        onClick={() => void act(id, () => runMySchedule(id, timezone), `'${task.name}' started.`)}
                      >
                        Run now
                      </Button>
                    )}{" "}
                    {canPause && (
                      <Button
                        disabled={busy}
                        onClick={() => void act(id, () => pauseMySchedule(id, timezone), `'${task.name}' paused.`)}
                      >
                        Pause
                      </Button>
                    )}
                    {canResume && (
                      <Button
                        disabled={busy}
                        onClick={() => void act(id, () => resumeMySchedule(id, timezone), `'${task.name}' resumed.`)}
                      >
                        Resume
                      </Button>
                    )}{" "}
                    <Button
                      variant="danger"
                      disabled={busy}
                      onClick={async () => {
                        const ok = await confirm({
                          title: `Delete "${task.name}"?`,
                          body: "It will stop running. This cannot be undone.",
                          danger: true,
                          confirmLabel: "Delete",
                        });
                        if (ok) void act(id, () => deleteMySchedule(id, timezone), `'${task.name}' deleted.`);
                      }}
                    >
                      Delete
                    </Button>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </Table>
    </Card>
  );
}
