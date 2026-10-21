/**
 * Auto Skills: what the agent learned from people's work, and what to do with it.
 *
 * Two cards, both administrator-only, because both decide what other people's
 * agents will read. A skill induced from someone's own task is applied to them
 * privately and needs no approval -- it appears in their own list, marked
 * `Personal`. Promotion is the step that puts it in front of everybody, and
 * that is what these cards are for.
 *
 * They live here rather than under a plugin because there is no plugin UI
 * extension mechanism in this frontend (ADR 0014): a plugin's screens are
 * written in `frontend/src` like every other screen.
 */

import { useState } from "react";

import {
  getAutoSkillsConfig,
  getPromotion,
  listPromotions,
  promoteSkill,
  rejectSkill,
  saveAutoSkillsConfig,
  type AutoSkillsConfig,
  type Promotion,
} from "@/api/autoSkills";
import {
  Button,
  Card,
  EmptyRow,
  Mono,
  Row,
  Select,
  Table,
  Tag,
  TextInput,
  useConfirm,
} from "@/components";
import { useAsync } from "@/lib/useAsync";

function Muted({ children }: { children: React.ReactNode }) {
  return <span style={{ color: "var(--ink-3)" }}>{children}</span>;
}

const STATUS_TONE = {
  pending: "warn",
  promoted: "ok",
  rejected: "muted",
} as const;

interface Feedback {
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}

function messageOf(caught: unknown): string {
  return caught instanceof Error ? caught.message : String(caught);
}

/* ---------- the queue ---------- */

export function SkillPromotions({ onError, onNotice }: Feedback) {
  const { state, reload } = useAsync<Promotion[]>(() => listPromotions(), []);
  const { confirm, prompt } = useConfirm();
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<Promotion | null>(null);
  // Pending is what a reviewer is here for; promoted/rejected are history.
  const [statusFilter, setStatusFilter] = useState<string>("pending");

  async function act(promotion: Promotion, verb: "promote" | "reject") {
    if (verb === "promote") {
      const confirmed = await confirm({
        title: `Promote "${promotion.name}"?`,
        body: "Every user's agent will be able to load it.",
        confirmLabel: "Promote",
      });
      if (!confirmed) return;
    }

    let reason = "";
    if (verb === "reject") {
      const value = await prompt({
        title: `Reject "${promotion.name}"?`,
        body: "It stays available to its author, but will not be offered to everyone.",
        label: "Reason (optional)",
        confirmLabel: "Reject",
        danger: true,
      });
      if (value === null) return;
      reason = value;
    }

    setBusy(promotion.id);
    try {
      const message =
        verb === "promote"
          ? await promoteSkill(promotion.id)
          : await rejectSkill(promotion.id, reason);
      onNotice(message);
      setOpen(null);
      reload();
    } catch (caught) {
      onError(messageOf(caught));
    } finally {
      setBusy(null);
    }
  }

  async function inspect(promotion: Promotion) {
    if (open?.id === promotion.id) {
      setOpen(null);
      return;
    }
    try {
      setOpen(await getPromotion(promotion.id));
    } catch (caught) {
      onError(messageOf(caught));
    }
  }

  const rows =
    state.status === "ready"
      ? state.data.filter((p) => statusFilter === "all" || p.status === statusFilter)
      : [];

  return (
    <Card
      title="Skills waiting for review"
      description="Skills the agent wrote from a user's completed task. Each one is already available to the person whose work produced it. Promoting makes it available to everyone."
      actions={
        <>
          <Select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by state"
          >
            <option value="pending">Pending</option>
            <option value="promoted">Promoted</option>
            <option value="rejected">Rejected</option>
            <option value="all">All</option>
          </Select>{" "}
          <Button onClick={reload}>Refresh</Button>
        </>
      }
    >
      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && <Muted>{state.error.message}</Muted>}
      {state.status === "ready" &&
        (state.data.length === 0 ? (
          <Muted>
            Nothing to review. Skills appear here after someone completes a task the
            agent could generalise from.
          </Muted>
        ) : (
          <>
            <div className="scroll-x">
              <Table>
                <thead>
                  <tr>
                    <th>Skill</th>
                    <th>What it is for</th>
                    <th>State</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <EmptyRow colSpan={4}>No promotions in this state.</EmptyRow>
                  ) : (
                    rows.map((promotion) => (
                    <tr key={promotion.id}>
                      <td style={{ whiteSpace: "nowrap" }}>
                        <Mono>{promotion.name}</Mono>
                      </td>
                      <td style={{ color: "var(--ink-2)", maxWidth: "48ch" }}>
                        {promotion.description || "—"}
                      </td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        <Tag tone={STATUS_TONE[promotion.status] ?? "muted"}>
                          {promotion.status}
                        </Tag>
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <Button onClick={() => void inspect(promotion)}>
                          {open?.id === promotion.id ? "Hide" : "Read"}
                        </Button>{" "}
                        {promotion.status === "pending" && (
                          <>
                            <Button
                              onClick={() => void act(promotion, "promote")}
                              disabled={busy === promotion.id}
                            >
                              {busy === promotion.id ? "Working…" : "Promote"}
                            </Button>{" "}
                            <Button
                              variant="danger"
                              onClick={() => void act(promotion, "reject")}
                              disabled={busy === promotion.id}
                            >
                              Reject
                            </Button>
                          </>
                        )}
                      </td>
                    </tr>
                    ))
                  )}
                </tbody>
              </Table>
            </div>
            {open && <Proposal promotion={open} />}
          </>
        ))}
    </Card>
  );
}

/** The exact file that would be written, plus where it came from. */
function Proposal({ promotion }: { promotion: Promotion }) {
  return (
    <div style={{ marginTop: "var(--space-3)" }}>
      <Row label="Why the agent proposed it">
        <Muted>{promotion.rationale || "—"}</Muted>
      </Row>
      <Row label="Induced from" help="Conversation and evidence this was written from.">
        <Mono>{promotion.source_context || "—"}</Mono>
      </Row>
      <Row label="Tool calls in that task">
        <Tag tone="muted">{promotion.tool_calls ?? "—"}</Tag>
      </Row>
      <Row
        label="Used since"
        help="How often its author has loaded it. A skill nobody reaches for is not one to give everybody."
      >
        {promotion.usage?.loads
          ? (
            <Tag tone={promotion.usage.loads >= 3 ? "ok" : "muted"}>
              {promotion.usage.loads} load{promotion.usage.loads === 1 ? "" : "s"}
              {typeof promotion.usage.loads_completed === "number"
                && ` · ${promotion.usage.loads_completed} completed`}
            </Tag>
          )
          : <Muted>not yet used</Muted>}
      </Row>
      <pre
        style={{
          marginTop: "var(--space-2)",
          padding: "var(--space-3)",
          background: "var(--surface-2)",
          border: "1px solid var(--line)",
          borderRadius: 6,
          overflowX: "auto",
          fontSize: "var(--text-sm)",
          whiteSpace: "pre-wrap",
        }}
      >
        {promotion.skill_md || "(empty)"}
      </pre>
    </div>
  );
}

/* ---------- the settings ---------- */

export function AutoSkillsSettings({ onError, onNotice }: Feedback) {
  const { state, reload } = useAsync(() => getAutoSkillsConfig(), []);
  const [draft, setDraft] = useState<Partial<AutoSkillsConfig>>({});
  const [saving, setSaving] = useState(false);

  const stored = state.status === "ready" ? state.data.config : null;
  const editable = state.status === "ready" ? state.data.editable : [];
  const value = <K extends keyof AutoSkillsConfig>(key: K) =>
    (draft[key] ?? stored?.[key]) as AutoSkillsConfig[K];
  // An empty list means the backend did not scope editing, so treat everything
  // as editable (the prior behaviour); otherwise honour exactly what it allows.
  const canEdit = (key: string) => editable.length === 0 || editable.includes(key);

  async function save() {
    setSaving(true);
    try {
      await saveAutoSkillsConfig(draft);
      onNotice("Auto Skills settings saved.");
      setDraft({});
      reload();
    } catch (caught) {
      onError(messageOf(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card
      title="Auto Skills"
      description="Whether the agent writes a skill for itself after someone completes a substantial task."
      actions={
        <Button
          variant="primary"
          onClick={() => void save()}
          disabled={saving || Object.keys(draft).length === 0}
        >
          {saving ? "Saving…" : "Save"}
        </Button>
      }
    >
      {state.status === "loading" && <Muted>Loading…</Muted>}
      {state.status === "error" && <Muted>{state.error.message}</Muted>}
      {state.status === "ready" && (
        <>
          {!state.data.enabled && (
            <Row label="Plugin" help="Enable it on the Plugins screen to take effect.">
              <Tag tone="warn">disabled</Tag>
            </Row>
          )}
          <Row
            label="After a completed task"
            help="Personal writes the skill for the user who did the work and queues it here. Review only queues it. Off captures nothing."
          >
            <Select
              value={String(value("mode") ?? "off")}
              disabled={!canEdit("mode")}
              onChange={(event) => setDraft({ ...draft, mode: event.target.value })}
            >
              <option value="personal">Write it for that user, and queue it</option>
              <option value="review">Queue it only</option>
              <option value="off">Off</option>
            </Select>
          </Row>
          <Row
            label="Minimum tool calls"
            help="Shorter tasks are not worth generalising from."
          >
            <TextInput
              type="number"
              min={1}
              disabled={!canEdit("min_tool_calls")}
              value={String(value("min_tool_calls") ?? 2)}
              onChange={(event) =>
                setDraft({ ...draft, min_tool_calls: Number(event.target.value) })
              }
            />
          </Row>
          <Row
            label="Skills per user"
            help="Every skill costs prompt budget on every turn for the user who owns it."
          >
            <TextInput
              type="number"
              min={0}
              disabled={!canEdit("max_personal_skills")}
              value={String(value("max_personal_skills") ?? 25)}
              onChange={(event) =>
                setDraft({ ...draft, max_personal_skills: Number(event.target.value) })
              }
            />
          </Row>
          <Row label="Proposals waiting at once" help="Instance-wide.">
            <TextInput
              type="number"
              min={0}
              disabled={!canEdit("max_pending_promotions")}
              value={String(value("max_pending_promotions") ?? 50)}
              onChange={(event) =>
                setDraft({ ...draft, max_pending_promotions: Number(event.target.value) })
              }
            />
          </Row>
        </>
      )}
    </Card>
  );
}
