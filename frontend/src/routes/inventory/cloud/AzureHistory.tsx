/** History tab: every pull, and for a chosen pull what it added, changed and removed with old and new values. */

import { useCallback, useEffect, useState } from "react";

import { listAzureRunChanges, listAzureRuns, type AzureRun, type CloudChange } from "@/api/azure";
import { Button, CenteredMessage, DateTime, Mono, Table, Tag, type TagTone } from "@/components";
import { useAsync } from "@/lib/useAsync";

import { Notice } from "../kit";
import styles from "./AzureHistory.module.css";

export const RUN_TONE: Record<AzureRun["status"], TagTone> = {
  succeeded: "ok",
  partial: "warn",
  failed: "crit",
  running: "accent",
  dry_run: "muted",
};

/** "the schedule", "engineer1, chat": who or what started a pull. */
export function startedBy(run: AzureRun): string {
  const trigger = run.counts?.trigger;
  if (run.requested_by === "system:schedule" || trigger === "schedule") return "the schedule";
  return [run.requested_by, trigger].filter(Boolean).join(", ") || "—";
}

const PAGE = 50;

const show = (value: unknown): string =>
  value === undefined || value === null || value === "" ? "none" : typeof value === "string" ? value : JSON.stringify(value);

function ChangeRow({ change, onOpenResource }: { change: CloudChange; onOpenResource: (id: string) => void }) {
  const entries = Object.entries(change.fields ?? {});
  const tone: TagTone = change.change === "removed" ? "crit" : change.change === "changed" ? "warn" : "ok";
  return (
    <tr data-testid="azure-change">
      <td>
        <Tag tone={tone}>{change.change}</Tag>
      </td>
      <td>
        {change.resource ? (
          <>
            <Button variant="ghost" onClick={() => onOpenResource(change.resource!.id)}>
              {change.resource.name}
            </Button>{" "}
            <span className={styles.muted}>
              {change.resource.label} · {change.resource.resource_group}
            </span>
          </>
        ) : (
          "—"
        )}
      </td>
      <td>
        {entries.length === 0
          ? "—"
          : entries.map(([path, v]) => (
              <div key={path}>
                <Mono>{path}</Mono>: {show(v.old)} → {show(v.new)}
              </div>
            ))}
      </td>
    </tr>
  );
}

function RunChanges({ run, onOpenResource }: { run: AzureRun; onOpenResource: (id: string) => void }) {
  const [rows, setRows] = useState<CloudChange[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const more = useCallback(
    async (offset: number) => {
      setBusy(true);
      setError(null);
      try {
        const page = await listAzureRunChanges(run.id, { limit: PAGE, offset });
        setRows((current) => (offset ? [...current, ...page.changes] : page.changes));
        setTotal(page.total);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setBusy(false);
      }
    },
    [run.id],
  );
  useEffect(() => {
    void more(0);
  }, [more]);

  return (
    <div className={styles.changes} data-testid="azure-run-changes">
      {error && <Notice tone="crit">{error}</Notice>}
      {total === 0 && <p className={styles.muted}>This pull found nothing added, changed or removed.</p>}
      {rows.length > 0 && (
        <Table>
          <thead>
            <tr>
              <th>Change</th>
              <th>Resource</th>
              <th>Old → new</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((change) => (
              <ChangeRow key={change.id} change={change} onOpenResource={onOpenResource} />
            ))}
          </tbody>
        </Table>
      )}
      {total !== null && rows.length < total && (
        <Button disabled={busy} onClick={() => void more(rows.length)}>
          Show more ({total - rows.length} left)
        </Button>
      )}
    </div>
  );
}

export function AzureHistory({ onOpenResource }: { onOpenResource: (id: string) => void }) {
  const { state, reload } = useAsync(listAzureRuns, []);
  const [open, setOpen] = useState<string | null>(null);

  if (state.status === "loading") return <CenteredMessage title="Loading history…" />;
  if (state.status === "error") {
    return (
      <CenteredMessage
        title="Could not load history"
        detail={state.error.message}
        action={
          <Button variant="primary" onClick={reload}>
            Retry
          </Button>
        }
      />
    );
  }
  const runs = state.data.runs;
  if (runs.length === 0) return <CenteredMessage title="No Azure pull has run yet" detail="Pull now on the Sources tab." />;

  return (
    <div className={styles.stack} data-testid="azure-history">
      <Notice>Open a pull to see every resource it added, changed or removed, with the old and new values.</Notice>
      <Table>
        <thead>
          <tr>
            <th>When</th>
            <th>Started by</th>
            <th>Result</th>
            <th>Added</th>
            <th>Changed</th>
            <th>Removed</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <HistoryRow key={run.id} run={run} open={open === run.id} onToggle={() => setOpen(open === run.id ? null : run.id)}>
              {open === run.id && <RunChanges key={run.id} run={run} onOpenResource={onOpenResource} />}
            </HistoryRow>
          ))}
        </tbody>
      </Table>
    </div>
  );
}

function HistoryRow({
  run,
  open,
  onToggle,
  children,
}: {
  run: AzureRun;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <tr data-testid="azure-run">
        <td>
          <Button variant="ghost" aria-expanded={open} onClick={onToggle}>
            <DateTime value={run.started_at} />
          </Button>
        </td>
        <td>{startedBy(run)}</td>
        <td>
          <Tag tone={RUN_TONE[run.status]}>{run.status}</Tag>
        </td>
        <td>{run.counts?.added ?? "—"}</td>
        <td>{run.counts?.changed ?? "—"}</td>
        <td>{run.counts?.removed ?? "—"}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6}>
            {run.error && <Notice tone="crit">{run.error}</Notice>}
            {children}
          </td>
        </tr>
      )}
    </>
  );
}
