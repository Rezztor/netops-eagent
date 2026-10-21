/** The Backups tab. Backup text stays on the executor; what is shown here is its redacted view. */

import { useCallback, useState } from "react";

import {
  diffBackup,
  listBackups,
  runBackup,
  viewBackup,
  type BackupRow,
} from "@/api/inventoryBackups";
import { Button, CenteredMessage, DateTime, EmptyRow, Mono, Modal, Table, useToast } from "@/components";
import { useAsync } from "@/lib/useAsync";
import { Notice, Section } from "./kit";
import styles from "./DeviceBackups.module.css";

export const fmtSize = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

export function DeviceBackups({
  deviceId,
  deviceName,
  canManage,
  onChanged,
}: {
  deviceId: string;
  deviceName: string;
  canManage: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const load = useCallback(() => listBackups(deviceId), [deviceId]);
  const { state, reload } = useAsync(load, [deviceId]);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [shown, setShown] = useState<{ title: string; text: string } | null>(null);

  if (state.status === "loading") return <CenteredMessage title="Loading…" />;
  if (state.status === "error" || !state.data.ok) {
    return (
      <CenteredMessage
        title="Could not load backups"
        detail={state.status === "error" ? state.error.message : state.data.error}
        action={<Button onClick={reload}>Retry</Button>}
      />
    );
  }
  const backups: BackupRow[] = state.data.backups ?? [];

  async function backUp() {
    setBusy(true);
    try {
      const res = await runBackup([deviceId]);
      if (!res.ok) throw new Error(res.error || "Backup failed.");
      const mine = res.results?.[0];
      if (!mine?.ok) throw new Error(mine?.error || "Backup failed.");
      toast.success(mine.changed ? "Backed up." : "Unchanged since the last backup.");
      reload();
      onChanged();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function open(title: string, fetchText: () => Promise<{ ok: boolean; error?: string; text?: string }>) {
    try {
      const res = await fetchText();
      if (!res.ok) throw new Error(res.error || "Could not load.");
      setShown({ title, text: res.text ?? "" });
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    }
  }

  const toggle = (id: string) => setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id].slice(-2)));
  // newest first, so the later row in the list is the older backup
  const [older, newer] = [...picked].sort((a, b) => backups.findIndex((x) => x.id === b) - backups.findIndex((x) => x.id === a));

  return (
    <div className={styles.root} data-testid="device-backups">
      <Notice>
        Configuration text is stored encrypted on the executor. What you can open here has secrets redacted, and it is never
        shown to the assistant.
      </Notice>
      {canManage && (
        <div className={styles.bar}>
          <Button variant="primary" disabled={busy} onClick={() => void backUp()}>
            Back up now
          </Button>
          <Button
            disabled={picked.length !== 2}
            onClick={() =>
              void open(`Changes in ${deviceName}`, async () => {
                const res = await diffBackup(deviceId, older!, newer!);
                return { ...res, text: res.ok ? (res.changed_lines ? (res.diff ?? "") : "No difference.") : "" };
              })
            }
          >
            Compare selected
          </Button>
          <span className={styles.meta}>Tick two backups to compare them.</span>
        </div>
      )}
      <Section title={`Backups (${backups.length})`}>
        <Table>
          <thead>
            <tr>
              {canManage && <th aria-label="Select" />}
              <th>Taken</th>
              <th>Last checked</th>
              <th>Size</th>
              <th>Hash</th>
              {canManage && <th aria-label="Actions" />}
            </tr>
          </thead>
          <tbody>
            {backups.length === 0 ? (
              <EmptyRow colSpan={canManage ? 6 : 4}>No backups yet.</EmptyRow>
            ) : (
              backups.map((b) => (
                <tr key={b.id} data-testid={`backup-${b.id}`}>
                  {canManage && (
                    <td>
                      <input type="checkbox" aria-label={`Select backup ${b.id}`} checked={picked.includes(b.id)} onChange={() => toggle(b.id)} />
                    </td>
                  )}
                  <td><DateTime value={b.taken_at} /></td>
                  <td>{b.checked_at ? <DateTime value={b.checked_at} mode="relative" /> : "—"}</td>
                  <td className={styles.num}>{fmtSize(b.size)}</td>
                  <td><Mono>{b.sha256.slice(0, 12)}</Mono></td>
                  {canManage && (
                    <td>
                      <Button aria-label={`View backup ${b.id}`} onClick={() => void open(`Backup of ${deviceName}`, () => viewBackup(deviceId, b.id))}>
                        View
                      </Button>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </Table>
      </Section>
      {shown && (
        <Modal wide title={shown.title} onClose={() => setShown(null)} footer={<Button onClick={() => setShown(null)}>Close</Button>}>
          <pre className={styles.text} data-testid="backup-text">{shown.text}</pre>
        </Modal>
      )}
    </div>
  );
}
