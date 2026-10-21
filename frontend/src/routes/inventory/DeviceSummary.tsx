/** The short summary beside the device list: the seven facts people look up most. */

import type { Device } from "@/api/inventory";
import type { Place } from "@/api/inventoryPlaces";
import { Button, Mono, Tag } from "@/components";

import { PlacePath } from "./PlacePicker";
import { KV, Notice, lifecycleSummary, sourceLabel } from "./kit";
import styles from "./DevicesPanel.module.css";

const fmt = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** "wireless_controller" -> "Wireless controller". */
export const titleCase = (value: string) => (value ? value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, " ") : "");

/** "HQ · A-3.12 · R01 U40" from the derived names (the old text while the device has no place); "" when nothing says where it is. */
export function placeOf(d: Device): string {
  if (!d.site && !d.room && !d.rack) return "";
  const rack = d.rack ? (d.rack_unit ? `${d.rack} U${d.rack_unit}` : d.rack) : "";
  return [d.site?.name ?? "No site", d.room, rack].filter(Boolean).join(" · ");
}

export function DeviceSummary({
  device,
  places,
  canManage,
  onClose,
  onOpenRecord,
  onEdit,
}: {
  device: Device;
  places: Place[];
  canManage: boolean;
  onClose: () => void;
  onOpenRecord: () => void;
  onEdit: () => void;
}) {
  const src = (key: string) => sourceLabel(device.field_sources?.[key]);
  const life = lifecycleSummary(device.computed);
  const dates = [
    ["Warranty", device.warranty_expiry],
    ["Support", device.support_expiry],
    ["End of life", device.eol_date],
  ].filter((row): row is [string, string] => !!row[1]);
  const type = [titleCase(device.device_type ?? ""), device.role].filter(Boolean).join(" · ");

  return (
    <aside aria-label="Device summary" className={styles.aside}>
      <div className={styles.asideHead}>
        <div>
          <h2 className={styles.asideTitle}>{device.name}</h2>
          <div className={styles.tags}>
            <Tag tone={statusTone(device.status)}>{device.status}</Tag>
            {device.computed?.monitored && <Tag>monitored</Tag>}
            {device.computed && <Tag>{`record ${Math.round(device.computed.completeness.percent)}%`}</Tag>}
          </div>
        </div>
        <button type="button" className={styles.close} aria-label="Close summary" onClick={onClose}>
          ×
        </button>
      </div>

      {dates.length > 0 && (
        <Notice tone={life.tone}>
          <div className={styles.noticeBody}>
            <strong>{life.text}</strong>
            <span className={styles.dim}>{dates.map(([kind, iso]) => `${kind} ${fmt(iso)}`).join(" · ")}</span>
          </div>
        </Notice>
      )}

      <KV
        rows={[
          { label: "Type", value: type },
          { label: "Model", value: [device.vendor, device.model].filter(Boolean).join(" "), source: src("model") },
          { label: "Serial", value: device.serial && <Mono>{device.serial}</Mono>, source: src("serial") },
          { label: "Management IP", value: device.mgmt_ip && <Mono>{device.mgmt_ip}</Mono>, source: src("mgmt_ip") },
          { label: "Software", value: device.software_version, source: src("software_version") },
          {
            label: "Place",
            value: device.place_id ? (
              <PlacePath id={device.place_id} places={places} />
            ) : (
              placeOf(device) && (
                <>
                  {placeOf(device)} <Tag>old place text</Tag>
                </>
              )
            ),
          },
          { label: "Custodian", value: device.custodian ?? "" },
        ]}
      />

      <div className={styles.actions}>
        <Button variant="primary" onClick={onOpenRecord}>
          Open full record
        </Button>
        {canManage && <Button onClick={onEdit}>Edit</Button>}
      </div>
      <span className={styles.hint}>The summary shows the seven facts people look up most. Everything else is on the full record.</span>
    </aside>
  );
}

export function statusTone(status: string): "ok" | "warn" | "crit" | "muted" {
  if (status === "active") return "ok";
  if (status === "offline" || status === "faulty" || status === "decommissioned") return "crit";
  if (status === "staged" || status === "planned") return "warn";
  return "muted";
}
