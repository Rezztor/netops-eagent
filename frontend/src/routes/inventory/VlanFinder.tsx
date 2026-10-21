/** "Find a VLAN": a number in, the ports that carry it out, each device a link to its record. */

import { useState, type FormEvent } from "react";

import { getVlan } from "@/api/inventoryPorts";
import { Button, EmptyRow, Modal, Table, TextInput, useToast } from "@/components";

import styles from "./DevicesPanel.module.css";

type Found = NonNullable<Awaited<ReturnType<typeof getVlan>>["ports"]>;

export function VlanFinder({ onClose, onOpenRecord }: { onClose: () => void; onOpenRecord: (deviceId: string) => void }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState<{ vlan: number; ports: Found } | null>(null);

  async function find(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const result = await getVlan(Number(text));
      if (!result.ok) throw new Error(result.error || "Could not look up that VLAN.");
      setFound({ vlan: result.vlan ?? Number(text), ports: result.ports ?? [] });
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Find a VLAN" onClose={onClose} footer={<Button onClick={onClose}>Close</Button>}>
      <div style={{ display: "grid", gap: "var(--space-3)" }}>
        <form onSubmit={(e) => void find(e)} style={{ display: "flex", gap: "var(--space-2)" }}>
          <TextInput
            aria-label="VLAN number"
            inputMode="numeric"
            placeholder="1 to 4094"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <Button type="submit" variant="primary" disabled={busy || !text.trim()}>
            Find
          </Button>
        </form>
        {found && (
          <Table>
            <thead>
              <tr>
                <th>Device</th>
                <th>Port</th>
                <th>Mode</th>
              </tr>
            </thead>
            <tbody>
              {found.ports.length === 0 ? (
                <EmptyRow colSpan={3}>No stored port carries VLAN {found.vlan}.</EmptyRow>
              ) : (
                found.ports.map((p) => (
                  <tr key={`${p.device_id}/${p.port}`}>
                    <td>
                      <button type="button" className={styles.nameBtn} onClick={() => onOpenRecord(p.device_id)}>
                        {p.device}
                      </button>
                    </td>
                    <td>{p.port}</td>
                    <td>{p.mode}</td>
                  </tr>
                ))
              )}
            </tbody>
          </Table>
        )}
      </div>
    </Modal>
  );
}
