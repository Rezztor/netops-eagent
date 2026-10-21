/**
 * Review: the one inbox for pending changes and conflicts (design 6).
 * Pending changes are grouped by where they came from (a file import, a bulk edit, one
 * device's manual edit); the selected group shows one card per change.
 */

import { useCallback, useMemo, useRef, useState } from "react";

import {
  decideBatch,
  decideProposal,
  listBatches,
  listConflicts,
  listProposals,
  type InventoryBatch,
  type Proposal,
} from "@/api/inventory";
import { listPlaces } from "@/api/inventoryPlaces";
import { Button, CenteredMessage, DateTime, Mono, Row, Tag, TextInput, useConfirm, useToast } from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useSession } from "@/lib/session";
import { useUrlState } from "@/lib/useUrlState";

import { ConflictsPanel } from "../InventoryConflicts";
import { KV, Notice, Segmented, fieldLabel } from "./kit";
import { PlacePath } from "./PlacePicker";
import styles from "./ReviewPanel.module.css";

/** Shown when the signed-in engineer approves their own proposal (ADR 0039). */
export const SELF_APPROVAL_COPY = "You requested this change. Approving makes you accountable for it.";

/** Batch variant of the requester-accountability notice. */
export const SELF_APPROVAL_BATCH_COPY = "You requested these changes. Approving makes you accountable for them.";

const MIN_CONFIDENCE_DEFAULT = 0.8;

export function formatVal(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const label = fieldLabel;

interface Group {
  key: string;
  title: string;
  kind: string;
  batch: InventoryBatch | null;
  items: Proposal[];
}

/** Batches by `evidence.batch_id`; everything else by device, as a manual edit. */
function groupProposals(list: Proposal[], batches: InventoryBatch[]): Group[] {
  const meta = new Map(batches.map((b) => [b.batch_id, b]));
  const groups = new Map<string, Group>();
  for (const p of list) {
    const bid = p.evidence?.batch_id ? String(p.evidence.batch_id) : "";
    const key = bid ? `batch:${bid}` : `device:${p.device_id ?? p.device_name}`;
    let group = groups.get(key);
    if (!group) {
      const batch = meta.get(bid) ?? null;
      const file = batch?.file || p.evidence?.file || "";
      group = bid
        ? { key, title: file || "Batch", kind: file === "Bulk edit" ? "bulk" : "import", batch, items: [] }
        : { key, title: p.device_name || "Device", kind: "manual", batch: null, items: [] };
      groups.set(key, group);
    }
    group.items.push(p);
  }
  return [...groups.values()];
}

function groupSub(group: Group) {
  const first = group.items[0];
  const who = group.batch?.requested_by || first?.requested_by || "";
  const when = group.batch?.requested_at || first?.requested_at || "";
  const kind =
    group.kind === "bulk"
      ? `${new Set(group.items.map((p) => p.device_id)).size} devices`
      : group.kind === "import"
        ? "Import"
        : "Manual edit";
  return (
    <>
      {kind} · {who} · <DateTime value={when} mode="relative" />
    </>
  );
}

function PendingQueue({ canApprove, onChanged }: { canApprove: boolean; onChanged: () => void }) {
  const session = useSession();
  const toast = useToast();
  const { confirm } = useConfirm();
  const [selectedKey, setSelectedKey] = useState("");
  const [minConfidenceOnly, setMinConfidenceOnly] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // The confirm dialog keeps the body it was given, so its inputs write to a ref, not state.
  const typed = useRef<{ note: string; edits: Record<string, string> }>({ note: "", edits: {} });

  const load = useCallback(async () => {
    const [proposalsRes, batchesRes] = await Promise.all([
      listProposals({ status: "pending", limit: 100 }),
      listBatches({ status: "pending", limit: 100 }),
    ]);
    return { proposalsRes, batchesRes };
  }, []);
  const { state, reload } = useAsync(load, [load]);
  const placesState = useAsync(() => listPlaces(true), []);
  const places = placesState.state.status === "ready" && placesState.state.data.ok ? placesState.state.data.places : [];
  /** A place change shows both paths; every other value is plain text. */
  const shown = (field: string, value: unknown) =>
    field === "place_id" && typeof value === "string" && value ? <PlacePath id={value} places={places} /> : formatVal(value);

  const groups = useMemo(
    () =>
      state.status === "ready"
        ? groupProposals(state.data.proposalsRes.proposals ?? [], state.data.batchesRes.batches ?? [])
        : [],
    [state],
  );

  const isMine = (who?: string | null) => !!who && (who === session.user.username || who === session.user.id);

  function done() {
    reload();
    onChanged();
  }

  async function decide(proposal: Proposal, decision: "approve" | "reject") {
    const approve = decision === "approve";
    typed.current = { note: "", edits: {} };
    const ok = await confirm({
      title: approve ? `Approve ${proposal.kind}?` : `Reject ${proposal.kind}?`,
      body: approve ? (
        <div>
          {isMine(proposal.requested_by) ? (
            <p>{SELF_APPROVAL_COPY}</p>
          ) : (
            <p>
              Approving applies the proposed field values. If the device version no longer matches, the
              proposal is superseded.
            </p>
          )}
          <Row label="Note">
            <TextInput aria-label="Decision note" onChange={(e) => (typed.current.note = e.target.value)} />
          </Row>
          {Object.keys(proposal.changes).map((field) => (
            <Row key={field} label={`Edit ${field}`} help="Optional override on approve">
              <TextInput
                aria-label={`Edit ${field}`}
                placeholder={formatVal(proposal.changes[field]?.new)}
                onChange={(e) => (typed.current.edits[field] = e.target.value)}
              />
            </Row>
          ))}
        </div>
      ) : (
        "The proposal will not be applied."
      ),
      danger: !approve,
      confirmLabel: approve ? "Approve" : "Reject",
    });
    if (!ok) return;
    setBusy(proposal.id);
    try {
      const { note, edits } = typed.current;
      const overrides = Object.fromEntries(Object.entries(edits).filter(([, v]) => v !== ""));
      const result = await decideProposal({
        id: proposal.id,
        decision,
        ...(note.trim() ? { note: note.trim() } : {}),
        ...(approve && Object.keys(overrides).length ? { edits: overrides } : {}),
      });
      if (!result.ok) throw new Error(result.error || "Decision failed.");
      if (result.sync && !result.sync.ok) toast.error(result.sync.error || "Approved, but executor sync failed.");
      else toast.success(approve ? "Proposal approved." : "Proposal rejected.");
      done();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  }

  async function decideWhole(group: Group, decision: "approve" | "reject") {
    const batch = group.batch;
    if (!batch) return;
    const approve = decision === "approve";
    const n = batch.counts.pending || group.items.length;
    const ok = await confirm({
      title: approve ? `Approve all (${n})?` : `Reject all (${n})?`,
      body: approve ? (
        <div>
          {isMine(batch.requested_by) ? (
            <p>{SELF_APPROVAL_BATCH_COPY}</p>
          ) : (
            <p>Approves every pending proposal in this batch. Stale proposals are superseded; the rest continue.</p>
          )}
          {minConfidenceOnly && <p>Only proposals with confidence ≥ {MIN_CONFIDENCE_DEFAULT} will be decided.</p>}
        </div>
      ) : (
        "All pending proposals in this batch will be rejected."
      ),
      danger: !approve,
      confirmLabel: approve ? `Approve all (${n})` : "Reject all",
    });
    if (!ok) return;
    setBusy(group.key);
    try {
      const result = await decideBatch({
        batch_id: batch.batch_id,
        decision,
        ...(minConfidenceOnly ? { min_confidence: MIN_CONFIDENCE_DEFAULT } : {}),
      });
      if (!result.ok) throw new Error(result.error || "Batch decision failed.");
      const skipped = result.skipped_low_confidence ?? 0;
      const failed = result.failed?.length ?? 0;
      if (result.sync && !result.sync.ok) toast.error(result.sync.error || "Batch decided, but executor sync failed.");
      else
        toast.success(
          `${approve ? "Approved" : "Rejected"} ${result.decided ?? 0}` +
            (skipped ? `, skipped ${skipped} low-confidence` : "") +
            (failed ? `, ${failed} failed` : "") +
            ".",
        );
      done();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  }

  if (state.status === "loading") return <CenteredMessage title="Loading proposals…" />;
  if (state.status === "error") {
    return (
      <CenteredMessage
        title="Could not load proposals"
        detail={state.error.message}
        action={
          <Button variant="primary" onClick={reload}>
            Retry
          </Button>
        }
      />
    );
  }
  if (groups.length === 0) {
    return (
      <div className={styles.empty}>
        <CenteredMessage
          title="Nothing waiting"
          detail="Propose a device create, update or decommission from the Devices tab."
        />
      </div>
    );
  }

  const group = groups.find((g) => g.key === selectedKey) ?? groups[0]!;
  const creates = group.items.filter((p) => p.kind === "create").length;
  const updates = group.items.length - creates;
  const mine = isMine(group.batch?.requested_by ?? group.items[0]?.requested_by);
  const requester = group.batch?.requested_by ?? group.items[0]?.requested_by;
  const pendingN = group.batch?.counts.pending || group.items.length;

  return (
    <div className={styles.split}>
      <div className={styles.list}>
        {groups.map((g) => (
          <button
            key={g.key}
            type="button"
            data-testid={`group-${g.key}`}
            aria-pressed={g.key === group.key}
            className={`${styles.group} ${g.key === group.key ? styles.groupOn : ""}`}
            onClick={() => setSelectedKey(g.key)}
          >
            <span className={styles.groupText}>
              <strong>{g.title}</strong>
              <span className={styles.groupSub}>{groupSub(g)}</span>
            </span>
            <Tag tone="warn">{g.items.length} waiting</Tag>
          </button>
        ))}
      </div>

      <div className={styles.detail} data-testid={group.batch ? `batch-${group.batch.batch_id}` : undefined}>
        <div className={styles.head}>
          <div>
            <h2 className={styles.title}>{group.title}</h2>
            <span className={styles.sub}>
              {group.items.length} change{group.items.length === 1 ? "" : "s"}
              {creates > 0 && ` · ${creates} new device${creates === 1 ? "" : "s"}`}
              {updates > 0 && `${creates > 0 ? ", " : " · "}${updates} update${updates === 1 ? "" : "s"}`}
              {" · requested by "}
              {mine ? "you" : requester}
            </span>
          </div>
          {canApprove && group.batch && (
            <div className={styles.actions}>
              <label className={styles.filter}>
                <input
                  type="checkbox"
                  aria-label="Only confidence >= 0.8"
                  checked={minConfidenceOnly}
                  onChange={(e) => setMinConfidenceOnly(e.target.checked)}
                />
                only confidence ≥ {MIN_CONFIDENCE_DEFAULT}
              </label>
              <Button variant="danger" disabled={busy === group.key} onClick={() => void decideWhole(group, "reject")}>
                Reject all
              </Button>
              <Button
                variant="primary"
                disabled={busy === group.key}
                onClick={() => void decideWhole(group, "approve")}
              >
                {`Approve all (${pendingN})`}
              </Button>
            </div>
          )}
        </div>

        {mine && canApprove && <Notice>{group.batch ? SELF_APPROVAL_BATCH_COPY : SELF_APPROVAL_COPY}</Notice>}

        {group.items.map((p) => {
          const ev = p.evidence;
          const conf = ev?.confidence;
          return (
            <div key={p.id} className={styles.card} data-testid={`proposal-row-${p.id}`}>
              <div className={styles.cardHead}>
                <div className={styles.who}>
                  <Tag tone="accent">{p.kind === "create" ? "new" : p.kind}</Tag>
                  <Mono>{p.device_name || "—"}</Mono>
                </div>
                <div className={styles.actions}>
                  {conf != null && (
                    <Tag tone={conf >= MIN_CONFIDENCE_DEFAULT ? "ok" : "warn"}>
                      <span data-testid={`confidence-${p.id}`}>confidence {conf}</span>
                    </Tag>
                  )}
                  {canApprove && (
                    <>
                      <Button variant="danger" disabled={busy === p.id} onClick={() => void decide(p, "reject")}>
                        Reject
                      </Button>
                      <Button disabled={busy === p.id} onClick={() => void decide(p, "approve")}>
                        Approve
                      </Button>
                    </>
                  )}
                </div>
              </div>
              <div data-testid={`diff-${p.id}`}>
                <KV
                  rows={Object.entries(p.changes).map(([field, change]) => ({
                    label: label(field),
                    value:
                      change.old == null || change.old === "" ? (
                        <strong>{shown(field, change.new)}</strong>
                      ) : (
                        <>
                          <span className={styles.was}>{shown(field, change.old)}</span>{" "}
                          <span className={styles.to}>to</span> <strong>{shown(field, change.new)}</strong>
                        </>
                      ),
                  }))}
                />
              </div>
              {(ev?.quote != null || ev?.location != null) && (
                <div className={styles.evidence} data-testid={`evidence-${p.id}`}>
                  <span className={styles.evidenceHead}>
                    Evidence{ev?.location != null && ` · ${ev.location}`}
                  </span>
                  {ev?.quote != null && <span className={styles.quote}>{ev.quote}</span>}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function ReviewPanel({ canApprove, onChanged }: { canApprove: boolean; onChanged: () => void }) {
  const [queue, setQueue] = useUrlState("queue", "pending");
  const active = queue === "conflicts" ? "conflicts" : "pending";

  const loadCounts = useCallback(async () => {
    const [p, c] = await Promise.all([listProposals({ status: "pending", limit: 1 }), listConflicts({ status: "open", limit: 1 })]);
    return { pending: p.pending_count ?? p.total ?? 0, conflicts: c.open_count ?? c.total ?? 0 };
  }, []);
  const { state, reload } = useAsync(loadCounts, [loadCounts]);
  const counts = state.status === "ready" ? state.data : null;

  function changed() {
    reload();
    onChanged();
  }

  return (
    <div className={styles.page}>
      <div className={styles.bar}>
        <Segmented
          label="Review queue"
          value={active}
          onChange={setQueue}
          options={[
            { id: "pending", label: <>Pending changes {counts && <span>{counts.pending}</span>}</> },
            { id: "conflicts", label: <>Conflicts {counts && <span>{counts.conflicts}</span>}</> },
          ]}
        />
        <span className={styles.hint}>Nothing in the inventory changes until a request is approved.</span>
      </div>
      {active === "pending" ? (
        <PendingQueue canApprove={canApprove} onChanged={changed} />
      ) : (
        <ConflictsPanel canApprove={canApprove} onChanged={changed} />
      )}
    </div>
  );
}
