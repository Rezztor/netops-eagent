/**
 * The approval dialog for a device change, shared by the Approvals page and the Network screen's Changes tab.
 *
 * It shows the commands of the plan as read just now, and for a change-high plan makes the approver type what it
 * applies to (`confirm`: the device name, or `apply to N devices`), as the chat card does. The server checks the
 * confirmation and the hash again; this only collects them.
 */

import type { ReactNode } from "react";

import type { ChangeDetail } from "@/api/networkAutomation";
import type { useConfirm } from "@/components";

export const SELF_APPROVAL_COPY =
  "You requested this change. Approving makes you accountable for what runs.";

/**
 * Ask to approve `plan`. Null when the person cancels; otherwise the confirmation to send with the approval
 * ("" when the plan asks for none).
 */
export async function askToApprove(
  dialogs: ReturnType<typeof useConfirm>,
  { title, plan, intro }: { title: string; plan: ChangeDetail; intro?: ReactNode },
): Promise<string | null> {
  const body = (
    <div>
      {intro}
      <h4>Commands</h4>
      <pre style={{ whiteSpace: "pre-wrap" }}>{plan.commands.join("\n") || "No commands."}</pre>
    </div>
  );
  const confirm = plan.confirm ?? "";
  if (!confirm) return (await dialogs.confirm({ title, body, confirmLabel: "Approve" })) ? "" : null;
  const typed = await dialogs.prompt({
    title,
    body,
    label: `Type ${confirm} to approve`,
    confirmLabel: "Approve",
    validate: (value) => (value.trim() === confirm ? null : `Type ${confirm} exactly.`),
  });
  return typed === null ? null : typed.trim();
}
