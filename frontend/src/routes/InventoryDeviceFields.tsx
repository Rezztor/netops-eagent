/**
 * The device record as eight groups, shared by the detail page (read) and the add/edit form (write).
 * One field list so the two cannot drift apart.
 */

import type { Device } from "@/api/inventory";
import type { Place } from "@/api/inventoryPlaces";
import { Select, TextInput } from "@/components";

import { PlacePicker } from "./inventory/PlacePicker";

export const PLATFORMS = [
  "ios",
  "iosxe",
  "nxos",
  "eos",
  "junos",
  "fortios",
  "panos",
  "sonic",
  "sonic_community",
  "other",
] as const;

export const STATUSES = [
  "active",
  "planned",
  "staged",
  "offline",
  "spare",
  "faulty",
  "decommissioned",
] as const;

export const DEVICE_TYPES = [
  "router",
  "switch",
  "firewall",
  "wireless_controller",
  "access_point",
  "load_balancer",
  "other",
] as const;

export const ROLE_SUGGESTIONS = ["core", "distribution", "access", "edge", "wan", "datacentre"];

const VENDORS = ["Cisco", "Arista", "Juniper", "Fortinet", "Palo Alto", "Dell"];

type Kind = "text" | "number" | "date" | "select" | "place";

export interface FieldDef {
  key: string;
  label: string;
  /** aria-label of the form input when it differs from the label. */
  aria?: string;
  kind?: Kind;
  options?: readonly string[];
  suggest?: "roles" | "vendors";
  max?: number;
  help?: string;
  placeholder?: string;
}

export const GROUPS: { title: string; blurb?: string; fields: FieldDef[] }[] = [
  {
    title: "Identity",
    blurb: "What the device is called and what it does.",
    fields: [
      { key: "name", label: "Name", aria: "Device name", help: "Lower-case letters, digits, dots and dashes" },
      { key: "device_type", label: "Type", kind: "select", options: DEVICE_TYPES },
      { key: "role", label: "Role", suggest: "roles", help: ROLE_SUGGESTIONS.join(", ") },
      { key: "status", label: "Status", kind: "select", options: STATUSES },
      { key: "asset_tag", label: "Asset tag" },
      { key: "tags", label: "Tags", help: "Comma-separated" },
    ],
  },
  {
    title: "Hardware",
    blurb: "Filled from the device when it can be read. You can type it for a device that is not reachable yet.",
    fields: [
      { key: "vendor", label: "Vendor", suggest: "vendors" },
      { key: "model", label: "Model" },
      { key: "part_number", label: "Part number" },
      { key: "serial", label: "Serial", placeholder: "Read from device or type it" },
      { key: "base_mac", label: "Base MAC", help: "aa:bb:cc:dd:ee:ff" },
    ],
  },
  {
    title: "Network",
    blurb: "How the device is reached.",
    fields: [
      { key: "platform", label: "Platform", kind: "select", options: PLATFORMS },
      { key: "mgmt_ip", label: "Mgmt IP" },
      { key: "ha_group", label: "HA group" },
    ],
  },
  {
    title: "Software",
    blurb: "What it is running.",
    fields: [{ key: "software_version", label: "Software version" }],
  },
  {
    title: "Place",
    blurb: "Where an engineer would go to touch it. Pick it from the list.",
    fields: [
      { key: "place_id", label: "Place", kind: "place" },
      // the two below are shown only when the place is a rack
      { key: "rack_unit", label: "Rack unit", kind: "number", max: 60, help: "Lowest U it occupies" },
      { key: "u_height", label: "Height (U)", kind: "number", max: 20 },
    ],
  },
  {
    title: "Ownership",
    blurb: "Who is responsible for it.",
    fields: [
      { key: "department", label: "Department" },
      { key: "custodian", label: "Custodian" },
      { key: "support_contact", label: "Support contact" },
      { key: "support_contract", label: "Support contract" },
    ],
  },
  {
    title: "Lifecycle",
    blurb: "Dates drive the alerts on the Health page.",
    fields: [
      { key: "purchase_date", label: "Purchased", kind: "date" },
      { key: "warranty_expiry", label: "Warranty expires", kind: "date" },
      { key: "support_expiry", label: "Support expires", kind: "date" },
      { key: "eol_date", label: "End of life", kind: "date" },
    ],
  },
  { title: "Management", fields: [] },
];

/* ----------------------------- form values ------------------------------ */

export type FormValues = Record<string, string>;

const FIELD_KEYS = GROUPS.flatMap((g) => g.fields.map((f) => f.key));
const KIND = new Map(GROUPS.flatMap((g) => g.fields.map((f) => [f.key, f.kind ?? "text"] as const)));

export function emptyValues(): FormValues {
  return {
    ...Object.fromEntries(FIELD_KEYS.map((k) => [k, ""])),
    device_type: "other",
    platform: "ios",
    status: "planned",
  };
}

export function valuesOf(device: Device): FormValues {
  const raw = device as unknown as Record<string, unknown>;
  return Object.fromEntries(
    FIELD_KEYS.map((k) => {
      const v = raw[k];
      if (k === "tags") return [k, device.tags.join(", ")];
      return [k, v === null || v === undefined ? "" : String(v)];
    }),
  );
}

/** Form strings to the request's JSON types: empty numbers/dates/place/serial are null, tags a list. */
export function toFields(values: FormValues): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of FIELD_KEYS) {
    const v = (values[key] ?? "").trim();
    const kind = KIND.get(key);
    if (key === "tags") out[key] = v.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
    else if (kind === "number") out[key] = v === "" ? null : Number(v);
    else if (kind === "date" || kind === "place" || key === "serial") out[key] = v || null;
    else out[key] = v;
  }
  return out;
}

/** Fields whose value differs from the device (or from a blank form when `device` is null) — what an edit proposes. */
export function changedFields(device: Device | null, values: FormValues): Record<string, unknown> {
  const before = toFields(device ? valuesOf(device) : emptyValues());
  return Object.fromEntries(
    Object.entries(toFields(values)).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(before[k])),
  );
}

/** Fields the Health page counts as missing when empty (reports.py `_DEFAULT_REQUIRED`). */
// ponytail: fixed list; the server's required_fields setting can override it, expose it if admins change it.
export const REQUIRED_FIELDS = new Set([
  "name", "device_type", "role", "vendor", "model", "serial", "mgmt_ip", "place_id", "status",
]);

/** The input for one field: a select, the place picker, or a text/number/date box. */
export function FieldControl({
  field: f,
  value,
  onChange,
  places,
  canCreate,
  onPlaceCreated,
  roles,
  id,
}: {
  field: FieldDef;
  value: string;
  onChange: (value: string) => void;
  places: Place[];
  canCreate: { upper: boolean; lower: boolean };
  onPlaceCreated: (place: Place) => void;
  roles: string[];
  id?: string;
}) {
  const aria = f.aria ?? f.label;
  const list = f.suggest ? `inventory-${f.suggest}` : undefined;
  const suggestions = f.suggest === "roles" ? roles : VENDORS;
  if (f.kind === "select")
    return (
      <Select id={id} aria-label={aria} value={value} onChange={(e) => onChange(e.target.value)}>
        {f.options!.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </Select>
    );
  if (f.kind === "place")
    return (
      <PlacePicker
        value={value || null}
        onChange={(v) => onChange(v ?? "")}
        places={places}
        canCreate={canCreate}
        onCreated={onPlaceCreated}
      />
    );
  return (
    <>
      <TextInput
        id={id}
        aria-label={aria}
        type={f.kind === "number" || f.kind === "date" ? f.kind : "text"}
        min={f.kind === "number" ? 1 : undefined}
        max={f.max}
        list={list}
        placeholder={f.placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {list && (
        <datalist id={list}>
          {suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      )}
    </>
  );
}
