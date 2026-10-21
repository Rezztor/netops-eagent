/**
 * Conflicts tab: observed values that disagree with a human-set value.
 * Decisions are the engineer's own — accept applies the observed value through
 * a proposal; keep suppresses it; pin blocks that source for the field.
 */

import { useCallback, useState } from "react";

import {
  decideConflict,
  decideConflicts,
  listConflicts,
  listPins,
  unpinField,
  type ConflictDecision,
  type InventoryConflict,
} from "@/api/inventory";
import {
  Button,
  Card,
  CenteredMessage,
  DateTime,
  EmptyRow,
  Mono,
  Table,
  Tag,
  useConfirm,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";

import { KV, Notice } from "./inventory/kit";
import styles from "./inventory/ReviewPanel.module.css";

const DECISION_LABEL: Record<ConflictDecision, string> = {
  accept: "Accept observed",
  keep: "Keep current",
  pin: "Pin current",
};

function formatVal(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function ConflictsPanel({
  canApprove,
  onChanged,
}: {
  canApprove: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [conflictsRes, pinsRes] = await Promise.all([
      listConflicts({ status: "open", limit: 100 }),
      listPins({}),
    ]);
    return { conflictsRes, pinsRes };
  }, []);
  const { state, reload } = useAsync(load, [load]);

  function changed() {
    setSelected(new Set());
    reload();
    onChanged();
  }

  async function decide(conflict: InventoryConflict, decision: ConflictDecision) {
    setBusy(true);
    try {
      const result = await decideConflict({ id: conflict.id, decision });
      if (!result.ok) throw new Error(result.error || "Decision failed.");
      toast.success(`${DECISION_LABEL[decision]}: ${conflict.device_name} ${conflict.field}.`);
      changed();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function decideSelected(decision: ConflictDecision) {
    const ids = [...selected];
    const ok = await confirm({
      title: `${DECISION_LABEL[decision]} (${ids.length})?`,
      body: "Applies to every selected conflict. Each decision is recorded against you.",
      confirmLabel: DECISION_LABEL[decision],
    });
    if (!ok) return;
    setBusy(true);
    try {
      const result = await decideConflicts({ ids, decision });
      if (!result.ok) throw new Error(result.error || "Bulk decision failed.");
      const failed = result.failed?.length ?? 0;
      toast.success(`Decided ${result.decided ?? 0}${failed ? `, ${failed} failed` : ""}.`);
      changed();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function unpin(deviceId: string, field: string, source: string) {
    setBusy(true);
    try {
      const result = await unpinField({ device_id: deviceId, field, source });
      if (!result.ok) throw new Error(result.error || "Unpin failed.");
      toast.success("Unpinned.");
      reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  if (state.status === "loading") return <CenteredMessage title="Loading conflicts…" />;
  if (state.status === "error" || !state.data.conflictsRes.ok) {
    return (
      <CenteredMessage
        title="Could not load conflicts"
        detail={
          state.status === "error" ? state.error.message : state.data.conflictsRes.error
        }
        action={
          <Button variant="primary" onClick={reload}>
            Retry
          </Button>
        }
      />
    );
  }

  const conflicts = state.data.conflictsRes.conflicts ?? [];
  const pins = state.data.pinsRes.pins ?? [];
  const allSelected = conflicts.length > 0 && conflicts.every((c) => selected.has(c.id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  return (
    <div style={{ display: "grid", gap: "var(--space-3)" }} data-testid="conflicts-panel">
      <Notice>
        An observed value that disagrees with a value an engineer set is never applied
        automatically. Accept it, keep the current value, or pin the current value so that
        source stops proposing it.
      </Notice>

      <div className={styles.head}>
        <span className={styles.sub}>{state.data.conflictsRes.open_count ?? conflicts.length} open</span>
        <div className={styles.actions}>
          {canApprove && conflicts.length > 0 && (
            <label className={styles.filter}>
              <input
                type="checkbox"
                aria-label="Select all conflicts"
                checked={allSelected}
                onChange={() => setSelected(allSelected ? new Set() : new Set(conflicts.map((c) => c.id)))}
              />
              Select all
            </label>
          )}
          {canApprove && selected.size > 0 && (
            <>
              <span>{selected.size} selected</span>
              {(Object.keys(DECISION_LABEL) as ConflictDecision[]).map((d) => (
                <Button key={d} disabled={busy} data-testid={`bulk-${d}`} onClick={() => void decideSelected(d)}>
                  {`${DECISION_LABEL[d]} (${selected.size})`}
                </Button>
              ))}
            </>
          )}
          <Button onClick={reload}>Refresh</Button>
        </div>
      </div>

      {conflicts.length === 0 && (
        <CenteredMessage
          title="No open conflicts"
          detail="Observed values that match or fill empty fields become proposals instead."
        />
      )}
      {conflicts.map((c) => (
        <div key={c.id} className={styles.card} data-testid={`conflict-row-${c.id}`}>
          <div className={styles.cardHead}>
            <div className={styles.who}>
              {canApprove && (
                <input
                  type="checkbox"
                  aria-label={`Select ${c.device_name} ${c.field}`}
                  checked={selected.has(c.id)}
                  onChange={() => toggle(c.id)}
                />
              )}
              <Tag tone="warn">conflict</Tag>
              <Mono>{c.device_name}</Mono>
              <Mono>{c.field}</Mono>
            </div>
            {canApprove && (
              <div className={styles.actions}>
                {(Object.keys(DECISION_LABEL) as ConflictDecision[]).map((d) => (
                  <Button key={d} disabled={busy} onClick={() => void decide(c, d)}>
                    {DECISION_LABEL[d]}
                  </Button>
                ))}
              </div>
            )}
          </div>
          <KV
            rows={[
              { label: "Current", value: `${formatVal(c.current_value)} (${c.current_source})` },
              {
                label: "Observed",
                value: (
                  <>
                    {formatVal(c.proposed_value)} ({c.source}
                    {c.observed_at && (
                      <>
                        , <DateTime value={c.observed_at} mode="relative" />
                      </>
                    )}
                    )
                  </>
                ),
              },
            ]}
          />
        </div>
      ))}

      <Card title="Pinned fields" description={`${pins.length} pinned`}>
        <Table>
          <thead>
            <tr>
              <th>Device</th>
              <th>Field</th>
              <th>Source</th>
              <th>Pinned by</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {pins.length === 0 ? (
              <EmptyRow colSpan={5}>No pinned fields.</EmptyRow>
            ) : (
              pins.map((p) => (
                <tr key={`${p.device_id}:${p.field}:${p.source}`}>
                  <td>
                    <Mono>{p.device_name}</Mono>
                  </td>
                  <td>
                    <Mono>{p.field}</Mono>
                  </td>
                  <td>{p.source}</td>
                  <td>
                    {p.pinned_by} · <DateTime value={p.pinned_at} mode="relative" />
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {canApprove && (
                      <Button
                        disabled={busy}
                        onClick={() => void unpin(p.device_id, p.field, p.source)}
                      >
                        Unpin
                      </Button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
