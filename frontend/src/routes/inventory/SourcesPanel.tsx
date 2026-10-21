/** Sources tab: where records come from (files, SolarWinds, device reads). Every source stages for Review. */

import { useCallback, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  downloadTemplate,
  listBatches,
  listSources,
  refreshFacts,
  syncSolarwinds,
  type FactsDeviceResult,
  type InventorySource,
  type NeedsReviewItem,
  type SourceRunCounts,
} from "@/api/inventory";
import { getSchedule } from "@/api/inventoryPorts";
import { createChat, sendMessageWithAttachments } from "@/api/chat";
import { Button, Card, CenteredMessage, DateTime, Mono, Tag, useToast } from "@/components";
import { useAsync } from "@/lib/useAsync";

import { saveExport } from "./download";
import { KV, Notice } from "./kit";
import styles from "./SourcesPanel.module.css";

function sourceStatusLabel(src: InventorySource): { label: string; tone: "ok" | "warn" | "crit" | "muted" } {
  if (!src.enabled) return { label: "Disabled", tone: "muted" };
  if (!src.configured) return { label: "Not configured", tone: "warn" };
  if (src.last_run?.status === "failed") return { label: "Last run failed", tone: "crit" };
  return { label: "Ready", tone: "ok" };
}

function formatRunCounts(counts: SourceRunCounts | undefined | null): string {
  if (!counts) return "—";
  return [
    `fetched ${counts.fetched}`,
    `to add ${counts.create}`,
    `to update ${counts.update}`,
    `unchanged ${counts.unchanged}`,
    `needs review ${counts.needs_review}`,
    `stale ${counts.stale}`,
  ].join(" · ");
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function SourcesPanel({
  canApprove,
  canManage = false,
  onChanged,
}: {
  canApprove: boolean;
  /** Reading devices needs inventory.manage; the screen passes it down. */
  canManage?: boolean;
  /** A run or a read staged something: refresh the counts around this panel. */
  onChanged?: () => void;
}) {
  const toast = useToast();
  const navigate = useNavigate();
  const load = useCallback(async () => {
    const [sources, batches, schedule] = await Promise.all([
      listSources(),
      listBatches({ limit: 20 }),
      getSchedule().catch(() => null), // the card still renders without it
    ]);
    return { sources, batches, schedule };
  }, []);
  const { state, reload } = useAsync(load, [load]);
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const [reads, setReads] = useState<FactsDeviceResult[] | null>(null);
  const [lastResult, setLastResult] = useState<{
    status: string;
    counts: SourceRunCounts;
    batch_id: string | null;
    needs_review: NeedsReviewItem[];
    missing_columns: string[];
    dry_run: boolean;
  } | null>(null);

  async function runSync(dryRun: boolean) {
    setBusy(true);
    try {
      const result = await syncSolarwinds(dryRun);
      if (!result.ok || !result.counts || !result.status || !result.run_id) {
        throw new Error(result.error || "SolarWinds sync failed.");
      }
      setLastResult({
        status: result.status,
        counts: result.counts,
        batch_id: result.batch_id ?? null,
        needs_review: result.needs_review ?? [],
        missing_columns: result.missing_columns ?? [],
        dry_run: dryRun,
      });
      toast.success(dryRun ? "Dry run finished." : "Sync staged proposals.");
      reload();
      if (!dryRun) onChanged?.();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function readDevices() {
    setBusy(true);
    try {
      const result = await refreshFacts({});
      if (!result.ok) throw new Error(result.error || "Could not read the devices.");
      const results = result.results ?? [];
      setReads(results);
      toast.success(`Read ${results.filter((r) => r.status === "ok").length} of ${plural(results.length, "device")}.`);
      onChanged?.();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  /** The same upload the chat composer uses: a new chat, the file attached, and the agent asked to import it. */
  async function importFile(file: File) {
    setBusy(true);
    try {
      const { context } = await createChat();
      await sendMessageWithAttachments(
        `Import the devices in the attached file ${file.name} into the inventory. Map the columns and stage them for review.`,
        context,
        [file],
      );
      navigate(`/chat?ctx=${encodeURIComponent(context)}`);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
      setBusy(false);
    }
  }

  async function template() {
    try {
      toast.success(`Saved ${saveExport(await downloadTemplate(), "text/csv")}`);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  if (state.status === "loading") return <CenteredMessage title="Loading sources…" />;
  const retry = (
    <Button variant="primary" onClick={reload}>
      Retry
    </Button>
  );
  if (state.status === "error") {
    return <CenteredMessage title="Could not load sources" detail={state.error.message} action={retry} />;
  }
  if (!state.data.sources.ok) {
    return <CenteredMessage title="Sources refused" detail={state.data.sources.error} action={retry} />;
  }

  const sources = state.data.sources.sources ?? [];
  const imports = (state.data.batches.batches ?? []).filter((b) => b.source === "extraction").slice(0, 3);
  const schedule = state.data.schedule?.ok ? state.data.schedule : null;
  const unreachable = (reads ?? []).filter((r) => r.status === "error");

  return (
    <div className={styles.stack} data-testid="sources-panel">
      <Notice>
        Every source stages changes for Review. None of them writes to the inventory directly, and values a person
        typed are never overwritten.
      </Notice>

      <div className={styles.grid}>
        <Card title="Files" actions={<Tag tone="ok">ready</Tag>}>
          <div className={styles.stack} data-testid="import-from-file">
            <p className={styles.help}>
              Upload a CSV, Excel, PDF or Word file. The assistant reads it in a new chat, maps the columns and stages
              the devices for your review.
            </p>
            {imports.length > 0 && (
              <KV
                rows={imports.map((b) => ({
                  label: b.file || "import",
                  value: (
                    <>
                      {b.total} staged · <DateTime value={b.requested_at} mode="relative" />
                    </>
                  ),
                }))}
              />
            )}
            <div className={styles.actions}>
              {canManage && (
                <>
                  <input
                    ref={picker}
                    type="file"
                    hidden
                    data-testid="import-file-input"
                    accept=".csv,.xlsx,.xls,.pdf,.doc,.docx,.txt"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      if (file) void importFile(file);
                    }}
                  />
                  <Button variant="primary" disabled={busy} onClick={() => picker.current?.click()}>
                    Upload a file
                  </Button>
                </>
              )}
              <Button onClick={() => void template()}>Download template</Button>
            </div>
          </div>
        </Card>

        {sources.map((src) => {
          const chip = sourceStatusLabel(src);
          const canRun = canApprove && src.enabled && src.configured;
          const counts = src.last_run?.counts;
          const run = src.last_run;
          return (
            <Card
              key={src.source}
              title={src.source === "solarwinds" ? "SolarWinds" : src.source}
              actions={
                <Tag tone={chip.tone}>
                  <span data-testid={`source-status-${src.source}`}>{chip.label}</span>
                </Tag>
              }
            >
              <div className={styles.stack} data-testid={`source-${src.source}`}>
                {!src.enabled && (
                  <Notice>
                    <div data-testid={`source-disabled-help-${src.source}`}>
                      Disabled. An administrator enables it in the _ea_inventory plugin config
                      (`solarwinds.enabled: true`) and configures the SolarWinds plugin.
                    </div>
                  </Notice>
                )}
                {src.enabled && !src.configured && (
                  <Notice tone="warn">Enabled but the SolarWinds plugin is not configured yet.</Notice>
                )}

                <div data-testid={`source-last-run-${src.source}`}>
                  <KV
                    rows={[
                      {
                        label: "Last run",
                        value: run ? (
                          <>
                            <DateTime value={run.finished_at || run.started_at} mode="relative" /> ({run.status})
                          </>
                        ) : (
                          "Never"
                        ),
                      },
                      { label: "Nodes read", value: String(src.nodes_seen) },
                      {
                        label: "Staged for review",
                        value: counts ? `${counts.create} new · ${counts.update} updates` : "—",
                      },
                      {
                        label: "Needs a decision",
                        value: counts?.needs_review
                          ? `${plural(counts.needs_review, "possible duplicate")}`
                          : "None",
                      },
                      { label: "Not seen lately", value: String(src.stale_count) },
                      { label: "Writes back", value: "Never: read only" },
                    ]}
                  />
                  {run?.error && <Notice tone="crit">{run.error}</Notice>}
                </div>

                {canRun && (
                  <div className={styles.actions}>
                    <Button disabled={busy} data-testid="solarwinds-dry-run" onClick={() => void runSync(true)}>
                      Preview a run
                    </Button>
                    <Button
                      variant="primary"
                      disabled={busy}
                      data-testid="solarwinds-run"
                      onClick={() => void runSync(false)}
                    >
                      Run and stage
                    </Button>
                  </div>
                )}
              </div>
            </Card>
          );
        })}

        <Card
          title="Device reads"
          actions={
            reads && (
              <Tag tone={unreachable.length ? "warn" : "ok"}>
                {reads.length - unreachable.length} of {reads.length} reachable
              </Tag>
            )
          }
        >
          <div className={styles.stack}>
            <KV
              rows={[
                { label: "Fills in", value: "model, serial, software, HA" },
                ...(unreachable.length
                  ? [{ label: "Not reachable", value: unreachable.map((r) => r.device).join(", ") }]
                  : []),
                { label: "If a value disagrees", value: "Opens a conflict, never overwrites" },
                ...(schedule
                  ? [
                      {
                        label: "Scheduled",
                        value: schedule.interval_hours ? `Every ${plural(schedule.interval_hours, "hour")}` : "Off",
                      },
                      ...(schedule.interval_hours
                        ? [
                            {
                              label: "Last scheduled run",
                              value: schedule.last_run ? (
                                <>
                                  <DateTime value={schedule.last_run.finished_at || schedule.last_run.started_at} mode="relative" />{" "}
                                  ({schedule.last_run.status})
                                </>
                              ) : (
                                "Never"
                              ),
                            },
                            {
                              label: "Next run",
                              value: schedule.next_run_at ? <DateTime value={schedule.next_run_at} mode="relative" /> : "—",
                            },
                          ]
                        : []),
                    ]
                  : []),
              ]}
            />
            {canManage && (
              <div className={styles.actions}>
                <Button disabled={busy} onClick={() => void readDevices()}>
                  Refresh all active devices
                </Button>
              </div>
            )}
          </div>
        </Card>
      </div>

      {lastResult && (
        <Card title={lastResult.dry_run ? "Dry-run result" : "Sync result"} description={lastResult.status}>
          <div data-testid="sync-result" className={styles.stack}>
            <div>{formatRunCounts(lastResult.counts)}</div>
            {lastResult.missing_columns.length > 0 && (
              <Notice tone="warn">
                <div data-testid="missing-columns">Missing columns: {lastResult.missing_columns.join(", ")}</div>
              </Notice>
            )}
            {lastResult.batch_id && !lastResult.dry_run && (
              <p className={styles.help}>
                <Link
                  to={`/inventory?tab=review&batch=${encodeURIComponent(lastResult.batch_id)}`}
                  data-testid="sync-batch-link"
                >
                  Open Review for this batch
                </Link>
              </p>
            )}
            {lastResult.needs_review.length > 0 && (
              <div data-testid="needs-review-list">
                <strong>Needs review</strong>
                <ul>
                  {lastResult.needs_review.map((item) => (
                    <li key={`${item.node_id}:${item.device_id ?? "none"}`}>
                      <Mono>{item.observed_name}</Mono> ↔ <Mono>{item.device_name}</Mono> — {item.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
