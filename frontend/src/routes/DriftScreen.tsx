/**
 * Drift: devices whose configuration changed between their last two backups, and whether an approved change explains it.
 *
 * Read only, from what is stored: the executor's backup index and the change ledger (`inventory_drift`). "Approved
 * change" means one ran on that device between the two backups; the diff says whether it is the whole difference.
 * The diff is the executor's redacted one, the same as the device record's Backups tab, and needs inventory.manage.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import { diffBackup, getDrift, type DriftItem } from "@/api/inventoryBackups";
import { PERMISSIONS } from "@/api/types";
import { Button, Card, CenteredMessage, DateTime, EmptyRow, Mono, Modal, Table, Tag, useToast } from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useSession } from "@/lib/session";
import { ExecutorRefused, isNotSetUp } from "./ExecutorState";

export function DriftScreen() {
  const session = useSession();
  const canManage = session.permissions.includes(PERMISSIONS.inventoryManage);
  const { state, reload } = useAsync(() => getDrift(), []);
  const [shown, setShown] = useState<{ title: string; text: string } | null>(null);
  const toast = useToast();

  if (state.status === "loading") return <CenteredMessage title="Loading…" />;
  if (state.status === "error" || !state.data.ok) {
    // An executor that was never set up is a step to take, not a fault to retry.
    if (state.status === "ready" && isNotSetUp(state.data.error)) return <ExecutorRefused error={state.data.error} onRetry={reload} />;
    return (
      <CenteredMessage
        title="Could not work out what changed"
        detail={state.status === "error" ? state.error.message : state.data.error}
        action={<Button onClick={reload}>Retry</Button>}
      />
    );
  }
  const items: DriftItem[] = state.data.items ?? [];
  const changed = state.data.changed ?? items.length;
  const unexplained = state.data.unexplained ?? items.filter((i) => !i.explained).length;

  async function openDiff(item: DriftItem) {
    try {
      const res = await diffBackup(item.device_id, item.from, item.to);
      if (!res.ok) throw new Error(res.error || "Could not load the diff.");
      setShown({ title: `Changes in ${item.name}`, text: res.changed_lines ? (res.diff ?? "") : "No difference." });
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  return (
    <Card
      title={`${changed} ${changed === 1 ? "device" : "devices"} changed, ${unexplained} unexplained`}
      description="Each device's last two backups, against the approved changes applied to it in between."
      actions={<Button onClick={reload}>Refresh</Button>}
    >
      <Table>
        <thead>
          <tr>
            <th>Device</th>
            <th>Changed between</th>
            <th>Explained by</th>
            <th aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <EmptyRow colSpan={4}>No device has a configuration change between its last two backups.</EmptyRow>
          ) : (
            items.map((item) => (
              <tr key={item.device_id} data-testid={`drift-${item.name}`}>
                <td>
                  <Link to={`/inventory?device=${encodeURIComponent(item.device_id)}`}><Mono>{item.name}</Mono></Link>
                </td>
                <td>
                  <DateTime value={item.since} /> – <DateTime value={item.changed_at} />
                </td>
                <td>
                  {item.explained ? (
                    item.changes.map((c) => (
                      <div key={c.id}>
                        <Tag tone="ok">approved change</Tag> {c.summary}, approved by {c.approved_by}, applied{" "}
                        <DateTime value={c.applied_at} />
                      </div>
                    ))
                  ) : (
                    <Tag tone="crit">unexplained</Tag>
                  )}
                </td>
                <td style={{ textAlign: "right" }}>
                  {canManage && <Button onClick={() => void openDiff(item)}>View diff</Button>}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </Table>
      {shown && (
        <Modal wide title={shown.title} onClose={() => setShown(null)} footer={<Button onClick={() => setShown(null)}>Close</Button>}>
          <pre style={{ whiteSpace: "pre-wrap" }} data-testid="drift-diff">{shown.text}</pre>
        </Modal>
      )}
    </Card>
  );
}
