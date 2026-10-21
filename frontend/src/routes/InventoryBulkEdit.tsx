/**
 * Bulk edit dialog: set the same fields on many devices as one batch of pending changes.
 * `only` narrows it to one purpose: ["status"] changes status, ["tags"] adds a tag to each device's own tags.
 */

import { useEffect, useState } from "react";

import { proposeMany } from "@/api/inventoryBulk";
import { listPlaces, type Place } from "@/api/inventoryPlaces";
import { Button, Modal, Select, Tag, TextInput, useToast } from "@/components";

import { PlacePicker } from "./inventory/PlacePicker";
import { Notice } from "./inventory/kit";
import styles from "./inventory/DevicesPanel.module.css";

const DEVICE_TYPES = ["router", "switch", "firewall", "wireless_controller", "access_point", "load_balancer", "other"];
const STATUSES = ["active", "planned", "staged", "offline", "spare", "faulty"];

type Kind = "text" | "number" | "date" | "select" | "place" | "tags";
const FIELDS: { key: string; label: string; kind: Kind; options?: string[] }[] = [
  { key: "device_type", label: "Type", kind: "select", options: DEVICE_TYPES },
  { key: "role", label: "Role", kind: "text" },
  { key: "status", label: "Status", kind: "select", options: STATUSES },
  { key: "place_id", label: "Place", kind: "place" },
  { key: "rack_unit", label: "Rack unit", kind: "number" },
  { key: "u_height", label: "Height (U)", kind: "number" },
  { key: "department", label: "Department", kind: "text" },
  { key: "custodian", label: "Custodian", kind: "text" },
  { key: "support_contact", label: "Support contact", kind: "text" },
  { key: "support_contract", label: "Support contract", kind: "text" },
  { key: "purchase_date", label: "Purchase date", kind: "date" },
  { key: "warranty_expiry", label: "Warranty expiry", kind: "date" },
  { key: "support_expiry", label: "Support expiry", kind: "date" },
  { key: "eol_date", label: "End of life", kind: "date" },
  { key: "tags", label: "Tags (replaces existing)", kind: "tags" },
];

/** An empty value clears the field: "" for text, null for numbers, dates and the place. */
function toValue(kind: Kind, raw: string): unknown {
  if (kind === "number") return raw === "" ? null : Number(raw);
  if (kind === "date" || kind === "place") return raw || null;
  if (kind === "tags") return raw.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
  return raw.trim();
}

export function BulkEditDialog({
  devices,
  canCreate = { upper: false, lower: true },
  only,
  onClose,
  onDone,
}: {
  devices: { id: string; name: string; tags?: string[] }[];
  /** What the person may add to the place list from the picker (the screen knows the permissions). */
  canCreate?: { upper: boolean; lower: boolean };
  only?: string[] | undefined;
  onClose(): void;
  onDone(batchId: string): void;
}) {
  const toast = useToast();
  const fields = only ? FIELDS.filter((f) => only.includes(f.key)).map((f) => (f.kind === "tags" ? { ...f, label: "Tag to add" } : f)) : FIELDS;
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries((only ?? []).map((k) => [k, ""])));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [places, setPlaces] = useState<Place[]>([]);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let live = true;
    listPlaces()
      .then((r) => live && r.ok && setPlaces(r.places))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [nonce]);
  const chosen = fields.filter((f) => f.key in values);
  const count = `${devices.length} device${devices.length === 1 ? "" : "s"}`;
  const ready = chosen.length > 0 && (!only || chosen.every((f) => values[f.key]?.trim()));

  function toggle(key: string, on: boolean) {
    setValues((v) => {
      const next = { ...v };
      if (on) next[key] = "";
      else delete next[key];
      return next;
    });
  }

  async function submit() {
    setBusy(true);
    try {
      const fixed = Object.fromEntries(chosen.map((f) => [f.key, toValue(f.kind, values[f.key] ?? "")]));
      // Adding a tag keeps what each device has, so devices are sent in groups that end up with the same tags.
      // ponytail: one batch per distinct tag set; a server-side "add tags" would make it one batch.
      const groups = new Map<string, { ids: string[]; fields: Record<string, unknown> }>();
      for (const d of devices) {
        const merged = only?.[0] === "tags" ? [...new Set([...(d.tags ?? []), ...(fixed.tags as string[])])].sort() : null;
        const key = JSON.stringify(merged);
        const group = groups.get(key) ?? { ids: [], fields: merged ? { tags: merged } : fixed };
        group.ids.push(d.id);
        groups.set(key, group);
      }
      let created = 0, unchanged = 0, batchId = "";
      const failed: { device_id: string; error: string }[] = [];
      for (const group of groups.values()) {
        const out = await proposeMany(group.ids, group.fields, note.trim() || undefined);
        if (!out.ok) throw new Error(out.error || "Could not propose the changes.");
        created += out.created ?? 0;
        unchanged += out.unchanged ?? 0;
        failed.push(...(out.failed ?? []));
        batchId ||= out.batch_id ?? "";
      }
      if (failed.length) toast.error(`${failed.length} device(s) refused: ${failed[0]?.error}`);
      if (!created) {
        if (!failed.length) toast.error("Every selected device already has these values.");
        return;
      }
      toast.success(`${created} change(s) proposed${unchanged ? `, ${unchanged} already matched` : ""}.`);
      onDone(batchId);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  function input(f: (typeof FIELDS)[number]) {
    const value = values[f.key] ?? "";
    const on = f.key in values;
    const set = (v: string) => setValues((all) => ({ ...all, [f.key]: v }));
    if (f.kind === "place") {
      // one place_id for every device; leaving the picker empty clears it
      return on ? (
        <PlacePicker value={value || null} onChange={(v) => set(v ?? "")} places={places} canCreate={canCreate} onCreated={() => setNonce((n) => n + 1)} />
      ) : (
        <TextInput aria-label={f.label} disabled placeholder="Unchanged" value="" readOnly />
      );
    }
    if (f.kind === "select") {
      return (
        <Select aria-label={f.label} disabled={!on} value={value} onChange={(e) => set(e.target.value)}>
          <option value="">{on ? "Choose…" : "Unchanged"}</option>
          {f.options!.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </Select>
      );
    }
    const type = f.kind === "number" || f.kind === "date" ? f.kind : "text";
    const range = f.key === "rack_unit" ? { min: 1, max: 60 } : f.key === "u_height" ? { min: 1, max: 20 } : {};
    return <TextInput aria-label={f.label} type={type} disabled={!on} placeholder="Unchanged" value={value} onChange={(e) => set(e.target.value)} {...range} />;
  }

  return (
    <Modal
      title={only?.[0] === "status" ? `Change status of ${count}` : only ? `Add a tag to ${count}` : `Edit ${count}`}
      wide
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={busy || !ready} onClick={() => void submit()}>
            {`Send ${devices.length} change${devices.length === 1 ? "" : "s"} to Review`}
          </Button>
        </>
      }
    >
      <div style={{ display: "grid", gap: "var(--space-3)" }}>
        <p style={{ margin: 0 }} data-testid="bulk-count">
          {count} selected.{only ? "" : " Tick a field to change it on every selected device. Unticked fields stay as they are. An empty value clears the field. Name, serial, management IP and asset tag differ per device and are not offered."}
        </p>
        <div className={styles.chips}>
          {devices.map((d) => (
            <Tag key={d.id}>{d.name}</Tag>
          ))}
        </div>
        <div className={styles.fieldRows}>
          {fields.map((f) => (
            <div key={f.key} className={styles.fieldRow}>
              {!only && (
                <input
                  type="checkbox"
                  aria-label={`Change ${f.label}`}
                  checked={f.key in values}
                  onChange={(e) => toggle(f.key, e.target.checked)}
                />
              )}
              <span className={styles.fieldName}>{f.label}</span>
              {input(f)}
            </div>
          ))}
        </div>
        <TextInput aria-label="Note" placeholder="Note for Review (optional)" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
        <Notice tone="accent">
          Nothing changes yet. This creates one batch of {devices.length} pending change{devices.length === 1 ? "" : "s"}. You requested it, so approving it in Review makes you accountable for it.
        </Notice>
      </div>
    </Modal>
  );
}
