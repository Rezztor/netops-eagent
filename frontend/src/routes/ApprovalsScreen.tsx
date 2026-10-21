/**
 * Approvals: everything the signed-in approver may sign, oldest first, with the time it stops being signable.
 *
 * Its own page because the approver is usually not in the chat that raised the change: the bell and the mail
 * (`plugins/_ea_ansible/helpers/approvers.py`) link here as `/approvals?change=<id>`, which opens that change.
 *
 * Nothing about approving changes. The commands are read fresh before an approval and the approval binds to their
 * hash; a change-high card's typed confirmation is asked for here too; the server checks all of it again. An Azure
 * change is decided on its chat card, which takes the CR/SR number and the change document, so its row opens the card.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  decideChange,
  getApprovalsWaiting,
  getChangeForReview,
  type ApprovalWaiting,
  type ChangeDetail,
} from "@/api/networkAutomation";
import { PERMISSIONS } from "@/api/types";
import {
  Button,
  Card,
  CenteredMessage,
  DateTime,
  EmptyRow,
  LoadFailed,
  Mono,
  Table,
  Tag,
  useConfirm,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import { askToApprove, SELF_APPROVAL_COPY } from "./approveChange";
import { useSession } from "@/lib/session";
import { useUrlState } from "@/lib/useUrlState";

export function ApprovalsScreen() {
  const session = useSession();
  const canApprove = session.permissions.includes(PERMISSIONS.networkApprove);
  const { state, reload } = useAsync(() => (canApprove ? getApprovalsWaiting() : Promise.resolve([])), [canApprove]);
  const [focus, setFocus] = useUrlState("change");
  const [detail, setDetail] = useState<ChangeDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const dialogs = useConfirm();
  const toast = useToast();

  // Arriving from the bell or a mail (`?change=<id>`): open that change's commands.
  const ready = state.status === "ready" ? state.data : null;
  useEffect(() => {
    if (!focus || !ready?.some((c) => c.id === focus && !c.azure)) return;
    let live = true;
    getChangeForReview(focus)
      .then((result) => live && result.ok && result.change && setDetail(result.change))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [focus, ready]);

  if (!canApprove) {
    return <CenteredMessage title="You don't have access to this page" detail="Signing network changes is not part of your role." />;
  }
  if (state.status === "loading") return <CenteredMessage title="Loading…" />;
  if (state.status === "error") {
    return <LoadFailed what="approvals" error={state.error} onRetry={reload} />;
  }
  const changes = state.data;
  const missing = focus && !changes.some((c) => c.id === focus);

  async function fresh(id: string): Promise<ChangeDetail> {
    const result = await getChangeForReview(id);
    if (!result.ok || !result.change) throw new Error(result.error || "Could not load that change.");
    return result.change;
  }

  async function review(change: ApprovalWaiting) {
    if (detail?.id === change.id) {
      setDetail(null);
      return;
    }
    try {
      setDetail(await fresh(change.id));
      setFocus(change.id);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function decide(change: ApprovalWaiting, decision: "approve" | "reject") {
    let plan: ChangeDetail | null = null;
    let typed = "";
    try {
      if (decision === "approve") {
        // Read again: the dialog shows the plan the approval binds to, as it stands now.
        plan = await fresh(change.id);
        const answer = await askToApprove(dialogs, {
          title: `Approve "${change.summary || change.playbook_name}"?`,
          plan,
          intro: change.requested_by === session.user.id && (
            <p>{SELF_APPROVAL_COPY}</p>
          ),
        });
        if (answer === null) return;
        typed = answer;
      } else if (!(await dialogs.confirm({ title: "Reject this change?", body: "It will not be applied.", danger: true, confirmLabel: "Reject" }))) {
        return;
      }
      setBusy(change.id);
      await decideChange(change.id, decision, plan?.current_hash, typed);
      toast.success(decision === "approve" ? "Change approved." : "Change rejected.");
      setDetail(null);
      reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title={`${changes.length} waiting for a signature`}
      description="Oldest first. A change you do not sign before it expires has to be asked for again."
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      {missing && (
        <p data-testid="approval-gone">That change is no longer waiting: it was decided, it expired, or it is not yours to sign.</p>
      )}
      <Table>
        <thead>
          <tr>
            <th>Waiting since</th>
            <th>Expires</th>
            <th>Change</th>
            <th>Targets</th>
            <th>Requested by</th>
            <th>Tier</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {changes.length === 0 ? (
            <EmptyRow colSpan={7}>Nothing waits for you.</EmptyRow>
          ) : (
            changes.map((change) => (
              <tr key={change.id} data-testid={`approval-${change.id}`} aria-current={change.id === focus ? "true" : undefined}
                style={change.id === focus ? { background: "var(--accent-soft, rgba(127,127,127,0.12))" } : undefined}>
                <td><DateTime value={change.created_at} mode="relative" /></td>
                <td>{change.expires_at ? <DateTime value={change.expires_at} /> : "—"}</td>
                <td>
                  {change.summary || change.playbook_name}
                  <br />
                  <Mono>{change.playbook_name || change.kind}</Mono>
                </td>
                <td>{change.limit || "—"}</td>
                <td>{change.requested_by_name}</td>
                <td>{change.tier ? <Tag tone={change.tier === "change-high" ? "crit" : "warn"}>{change.tier}</Tag> : "—"}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {change.azure ? (
                    change.context_id ? <Link to={`/chat?ctx=${encodeURIComponent(change.context_id)}`}>Open the card</Link> : "On its chat card"
                  ) : (
                    <>
                      <Button onClick={() => void review(change)}>{detail?.id === change.id ? "Hide" : "Review"}</Button>{" "}
                      <Button disabled={busy === change.id} onClick={() => void decide(change, "approve")}>Approve</Button>{" "}
                      <Button variant="danger" disabled={busy === change.id} onClick={() => void decide(change, "reject")}>Reject</Button>
                    </>
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </Table>
      {detail && <Plan detail={detail} />}
    </Card>
  );
}

function Plan({ detail }: { detail: ChangeDetail }) {
  return (
    <div style={{ marginTop: "var(--space-3)" }} data-testid="approval-plan">
      <h4>Commands for {detail.playbook_name}</h4>
      <pre style={{ whiteSpace: "pre-wrap" }}>{detail.commands.join("\n") || "No commands."}</pre>
      {detail.rollback_commands.length > 0 && (
        <>
          <h4>Rollback</h4>
          <pre style={{ whiteSpace: "pre-wrap" }}>{detail.rollback_commands.join("\n")}</pre>
        </>
      )}
    </div>
  );
}
