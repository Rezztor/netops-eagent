/** The add / edit page: grouped form, section nav with completion marks, and the "Your change" panel. */

import { useCallback, useState, type ReactNode } from "react";

import { decideProposal, getDevice, listDevices, proposeChange, type Device } from "@/api/inventory";
import { PERMISSIONS } from "@/api/types";
import { kindOf, listPlaces, type Place } from "@/api/inventoryPlaces";
import { Button, CenteredMessage, Mono, useToast } from "@/components";
import { useSession } from "@/lib/session";
import { useAsync } from "@/lib/useAsync";

import {
  FieldControl,
  GROUPS,
  REQUIRED_FIELDS,
  ROLE_SUGGESTIONS,
  changedFields,
  emptyValues,
  toFields,
  valuesOf,
  type FormValues,
} from "../InventoryDeviceFields";
import { PlacePath } from "./PlacePicker";
import { Meter, Section } from "./kit";
import styles from "./DeviceEdit.module.css";

const GROUPS_SHOWN = GROUPS.filter((g) => g.fields.length);
const LABELS = new Map(GROUPS.flatMap((g) => g.fields.map((f) => [f.key, f.label] as const)));
const slug = (title: string) => `device-form-${title.toLowerCase()}`;
// rack_unit and u_height make sense only inside a rack
const RACK_ONLY = new Set(["rack_unit", "u_height"]);
const isRack = (id: string | undefined) => !!id?.trim() && kindOf(id.trim()) === "rack";
const missingOf = (values: FormValues) => [...REQUIRED_FIELDS].filter((k) => !(values[k] ?? "").trim());

export function DeviceEdit({
  deviceId,
  canApprove = false,
  onDone,
  onCancel,
}: {
  deviceId: string | null;
  /** Lets an approver add a device in one step instead of sending it to Review. */
  canApprove?: boolean;
  onDone: () => void;
  onCancel: () => void;
}) {
  const load = useCallback(async () => {
    const [list, placed, one] = await Promise.all([
      listDevices({ limit: 1 }),
      listPlaces(),
      deviceId ? getDevice(deviceId) : null,
    ]);
    return { facets: list.facets, places: placed.places ?? [], one };
  }, [deviceId]);
  const { state } = useAsync(load, [deviceId]);

  if (state.status === "loading") return <CenteredMessage title="Loading…" />;
  if (state.status === "error" || (state.data.one && !state.data.one.device)) {
    return (
      <CenteredMessage
        title="Could not load device"
        detail={state.status === "error" ? state.error.message : state.data.one?.error}
      />
    );
  }
  const { facets, places, one } = state.data;
  const roles = [...new Set([...ROLE_SUGGESTIONS, ...(facets?.role ?? [])])];
  return (
    <EditForm
      device={one?.device ?? null}
      places={places}
      roles={roles}
      canApprove={canApprove}
      onDone={onDone}
      onCancel={onCancel}
    />
  );
}

function EditForm({
  device,
  places: initialPlaces,
  roles,
  canApprove,
  onDone,
  onCancel,
}: {
  device: Device | null;
  places: Place[];
  roles: string[];
  canApprove: boolean;
  onDone: () => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const { permissions } = useSession();
  const canCreate = {
    upper: permissions.includes(PERMISSIONS.inventoryPlaces),
    lower: permissions.includes(PERMISSIONS.inventoryManage),
  };
  const before = device ? valuesOf(device) : emptyValues();
  const [values, setValues] = useState<FormValues>(before);
  const [places, setPlaces] = useState(initialPlaces);
  const [active, setActive] = useState(GROUPS_SHOWN[0]?.title ?? "");
  const [busy, setBusy] = useState(false);

  const changed = Object.keys(changedFields(device, values));
  const missing = missingOf(values);
  const percent = ((REQUIRED_FIELDS.size - missing.length) / REQUIRED_FIELDS.size) * 100;
  const show = (key: string, v: string): ReactNode =>
    v.trim() ? (key === "place_id" ? <PlacePath id={v.trim()} places={places} /> : v.trim()) : "not set";

  /** Leaving a rack clears the rack unit here, as Review will show it (proposals add the same change). */
  function setValue(key: string, v: string) {
    setValues((cur) => ({ ...cur, [key]: v, ...(key === "place_id" && !isRack(v) ? { rack_unit: "" } : {}) }));
  }

  async function submit(approve = false) {
    if (!values.name?.trim()) {
      toast.error("Name is required.");
      return;
    }
    setBusy(true);
    try {
      const result = await proposeChange(
        device
          ? { kind: "update", device_id: device.id, fields: changedFields(device, values) }
          : { kind: "create", fields: toFields(values) },
      );
      if (!result.ok || !result.proposal) throw new Error(result.error || "Could not send to Review.");
      if (approve) {
        const decided = await decideProposal({ id: result.proposal.id, decision: "approve" });
        if (!decided.ok) throw new Error(decided.error || "Sent to Review, but could not approve it.");
        toast.success("Device created.");
      } else {
        toast.success(device ? "Change sent to Review." : "Device sent to Review.");
      }
      onDone();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.page}>
      <div>
        <div className={styles.crumbs}>
          <button type="button" className={styles.crumb} onClick={onCancel}>
            Devices
          </button>
          <span>/</span>
          {device && (
            <>
              <button type="button" className={styles.crumb} onClick={onCancel}>
                {device.name}
              </button>
              <span>/</span>
            </>
          )}
          <span>{device ? "Edit" : "Add device"}</span>
        </div>
        <h2 className={styles.title}>
          {device ? (
            <>
              Edit <Mono>{device.name}</Mono>
            </>
          ) : (
            "Add device"
          )}
        </h2>
      </div>

      <div className={styles.layout}>
        <nav aria-label="Form sections" className={styles.nav}>
          {GROUPS_SHOWN.map((g) => {
            const required = g.fields.filter((f) => REQUIRED_FIELDS.has(f.key));
            const done = required.length > 0 && required.every((f) => (values[f.key] ?? "").trim());
            const on = g.title === active;
            return (
              <button
                key={g.title}
                type="button"
                aria-current={on ? "true" : undefined}
                className={`${styles.navItem} ${on ? styles.navOn : ""}`}
                onClick={() => {
                  setActive(g.title);
                  document.getElementById(slug(g.title))?.scrollIntoView?.({ block: "start" });
                }}
              >
                <span className={styles.mark}>
                  {done && !on ? (
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-label="complete" role="img">
                      <path d="M5 12l5 5 9-10" />
                    </svg>
                  ) : (
                    <span className={`${styles.dot} ${on ? styles.dotOn : ""}`} />
                  )}
                </span>
                {g.title}
              </button>
            );
          })}
          <div className={styles.record}>
            <span className={styles.recordTitle}>Record</span>
            <Meter percent={percent} tone={missing.length ? "warn" : "ok"} />
            {missing.length > 0 && <span>Missing: {missing.map((k) => LABELS.get(k)?.toLowerCase() ?? k).join(", ")}</span>}
          </div>
        </nav>

        <div className={styles.sections}>
          {GROUPS_SHOWN.map((g) => (
            <div key={g.title} id={slug(g.title)}>
              <Section title={g.title}>
                {g.blurb && <p className={styles.blurb}>{g.blurb}</p>}
                <div className={styles.grid}>
                  {g.fields
                    .filter((f) => !RACK_ONLY.has(f.key) || isRack(values.place_id))
                    .map((f) => {
                      const id = `f-${f.key}`;
                      const star = REQUIRED_FIELDS.has(f.key) && <span className={styles.star} aria-hidden="true"> *</span>;
                      return (
                        <div key={f.key} className={`${styles.field} ${f.kind === "place" ? styles.wide : ""}`}>
                          {f.kind === "place" ? (
                            <span className={styles.label}>
                              {f.label}
                              {star}
                            </span>
                          ) : (
                            <label htmlFor={id} className={styles.label}>
                              {f.label}
                              {star}
                            </label>
                          )}
                          <FieldControl
                            id={id}
                            field={f}
                            value={values[f.key] ?? ""}
                            onChange={(v) => setValue(f.key, v)}
                            places={places}
                            canCreate={canCreate}
                            onPlaceCreated={() =>
                              void listPlaces()
                                .then((r) => r.ok && setPlaces(r.places))
                                .catch(() => undefined)
                            }
                            roles={roles}
                          />
                          {f.help && <span className={styles.hint}>{f.help}</span>}
                          {f.kind === "place" && !device?.place_id && typeof device?.custom?.old_place_text === "string" && (
                            <span className={styles.hint}>Old place text: {device.custom.old_place_text}. Pick the place from the list.</span>
                          )}
                        </div>
                      );
                    })}
                </div>
              </Section>
            </div>
          ))}
        </div>

        <aside aria-label="Summary of your change" className={styles.change}>
          <Section title="Your change">
            <div>
              {changed.map((k) => (
                <div key={k} className={styles.changeRow}>
                  <span className={styles.changeLabel}>{LABELS.get(k) ?? k}</span>
                  <span>
                    <span className={styles.old}>{show(k, before[k] ?? "")}</span> <span className={styles.to}>to</span>{" "}
                    <strong>{show(k, values[k] ?? "")}</strong>
                  </span>
                </div>
              ))}
            </div>
            <span className={styles.note}>
              {device
                ? changed.length
                  ? `Saving sends ${changed.length} field change${changed.length === 1 ? "" : "s"} to Review. The device record changes when the request is approved.`
                  : "No changes yet. Edit a field to send it to Review."
                : "Saving sends a request to add this device to Review. It joins the inventory when the request is approved."}
            </span>
            <div className={styles.actions}>
              <Button variant="primary" disabled={busy || (!!device && !changed.length)} onClick={() => void submit()}>
                Send to Review
              </Button>
              {canApprove && !device && (
                <Button disabled={busy} onClick={() => void submit(true)}>
                  Create and approve
                </Button>
              )}
              <Button onClick={onCancel}>Cancel</Button>
            </div>
          </Section>
        </aside>
      </div>
    </div>
  );
}
