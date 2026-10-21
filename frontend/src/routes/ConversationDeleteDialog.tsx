/**
 * Confirm bulk conversation deletion: names, cascade checkbox, running/schedule warnings.
 */

import { useMemo, useState } from "react";

import { Button, Modal } from "@/components";
import type { ChatContext } from "@/api/chat";
import { descendantsOf, type ConversationRow } from "./conversationGroups";
import styles from "./chat.module.css";

function isScheduled(context: ChatContext): boolean {
  return (
    typeof context.task_name === "string" ||
    typeof context.task_uuid === "string" ||
    typeof context.task_id === "string"
  );
}

function isRunning(context: ChatContext): boolean {
  return context.running === true;
}

export function ConversationDeleteDialog({
  selected,
  rowsById,
  onCancel,
  onConfirm,
}: {
  selected: ChatContext[];
  rowsById: Map<string, ConversationRow>;
  onCancel: () => void;
  onConfirm: (includeSubagents: boolean) => void | Promise<void>;
}) {
  const [includeSubagents, setIncludeSubagents] = useState(true);
  const [busy, setBusy] = useState(false);

  const parentsWithKids = useMemo(
    () => selected.filter((c) => (rowsById.get(c.id)?.children.length ?? 0) > 0),
    [selected, rowsById],
  );

  const cascadeCount = useMemo(() => {
    let n = 0;
    for (const parent of parentsWithKids) {
      const row = rowsById.get(parent.id);
      if (row) n += descendantsOf(row).length;
    }
    return n;
  }, [parentsWithKids, rowsById]);

  const deleteTotal = useMemo(() => {
    if (!includeSubagents) return selected.length;
    const ids = new Set(selected.map((c) => c.id));
    for (const parent of parentsWithKids) {
      const row = rowsById.get(parent.id);
      if (!row) continue;
      for (const child of descendantsOf(row)) ids.add(child.id);
    }
    return ids.size;
  }, [includeSubagents, parentsWithKids, rowsById, selected]);

  const more = selected.length > 3 ? selected.length - 3 : 0;

  const cascadeTargets = useMemo(() => {
    if (!includeSubagents) return [] as ChatContext[];
    const selectedIds = new Set(selected.map((c) => c.id));
    const out: ChatContext[] = [];
    for (const parent of parentsWithKids) {
      const row = rowsById.get(parent.id);
      if (!row) continue;
      for (const child of descendantsOf(row)) {
        if (!selectedIds.has(child.id)) out.push(child);
      }
    }
    return out;
  }, [includeSubagents, parentsWithKids, rowsById, selected]);

  const considered = [...selected, ...cascadeTargets];
  const runningCount = considered.filter(isRunning).length;
  const scheduledCount = considered.filter(isScheduled).length;

  return (
    <Modal
      title={`Delete ${selected.length} conversation${selected.length === 1 ? "" : "s"}?`}
      danger
      onClose={onCancel}
      footer={
        <>
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="danger"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void Promise.resolve(onConfirm(includeSubagents)).finally(() => setBusy(false));
            }}
          >
            {`Delete ${deleteTotal} chat${deleteTotal === 1 ? "" : "s"}`}
          </Button>
        </>
      }
    >
      <ul className={styles.deleteTitleList}>
        {selected.slice(0, 3).map((c) => (
          <li key={c.id}>{c.name || "Untitled conversation"}</li>
        ))}
        {more > 0 && <li>{`and ${more} more`}</li>}
      </ul>

      {parentsWithKids.length > 0 && (
        <label className={styles.deleteCascade}>
          <input
            type="checkbox"
            checked={includeSubagents}
            onChange={(event) => setIncludeSubagents(event.target.checked)}
          />
          {`Also delete their sub-agent chats (${cascadeCount})`}
        </label>
      )}

      <div className={styles.deleteWarnings}>
        {runningCount > 0 && (
          <p>
            {runningCount === 1
              ? "1 chat is still running and will be stopped."
              : `${runningCount} chats are still running and will be stopped.`}
          </p>
        )}
        {scheduledCount > 0 && (
          <p>
            {scheduledCount === 1
              ? "1 schedule-created chat will also remove its schedule."
              : `${scheduledCount} schedule-created chats will also remove their schedules.`}
          </p>
        )}
        <p>This cannot be undone.</p>
      </div>
    </Modal>
  );
}
