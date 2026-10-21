/** Places tab: the master list as a tree six levels deep, the selected place beside it, racks drawn to scale. */

import { useCallback, useEffect, useMemo, useState } from "react";

import { getRacks, type RackGroup } from "@/api/inventoryReports";
import { listDevices, type Device } from "@/api/inventory";
import {
  PLACE_KINDS,
  ancestorsOf,
  childrenOf,
  createPlace,
  deletePlace,
  kindOf,
  listPlaces,
  mergePlaces,
  updatePlace,
  type MergeResult,
  type Place,
  type PlaceKind,
} from "@/api/inventoryPlaces";
import { PERMISSIONS } from "@/api/types";
import {
  Button,
  CenteredMessage,
  Mono,
  Select,
  Tag,
  TextInput,
  useConfirm,
  useToast,
} from "@/components";
import { useCan } from "@/lib/session";
import { useAsync } from "@/lib/useAsync";
import { useUrlState } from "@/lib/useUrlState";

import { PlacePath, PlaceSearch } from "./PlacePicker";
import { useCodeSuggestion } from "./useCodeSuggestion";
import styles from "./PlacesPanel.module.css";

const U_PX = 11;
const DEFAULT_U = 42;
const NONE = "none";

/** Sites, locations and buildings need `inventory.places`; floors, rooms and racks need `inventory.manage`. */
const UPPER = new Set<PlaceKind>(["site", "location", "building"]);
const label = (kind: PlaceKind) => kind[0]!.toUpperCase() + kind.slice(1);
const parentOf = (id: string) => (id.includes("-") ? id.slice(0, id.lastIndexOf("-")) : null);

function rackHeightU(rack: RackGroup): number {
  let max = DEFAULT_U;
  for (const d of rack.devices) {
    if (d.rack_unit == null) continue;
    max = Math.max(max, d.rack_unit + (d.u_height ?? 1) - 1);
  }
  return max;
}

const uTop = (unit: number, heightU: number) => (heightU - unit) * U_PX;

function RackCard({ rack, onOpenDevice }: { rack: RackGroup; onOpenDevice: (id: string) => void }) {
  const heightU = rackHeightU(rack);
  const heightPx = heightU * U_PX;
  const drawn = rack.devices.filter((d) => d.rack_unit != null);
  const listed = rack.devices.filter((d) => d.rack_unit == null);
  const ticks = Array.from({ length: Math.floor(heightU / 5) + 1 }, (_, i) => (i === 0 ? heightU : heightU - i * 5)).filter((u) => u >= 1);
  if (!ticks.includes(1)) ticks.push(1);

  return (
    <div className={styles.rackCard} data-testid={`rack-${rack.place_id}`}>
      <div className={styles.rackHead}>
        <strong>{rack.rack}</strong>
        <span className={styles.rackMeta}>
          {rack.used_u} of {heightU} U used
        </span>
      </div>
      <div className={styles.rackFrame} style={{ height: heightPx }}>
        {ticks.map((u) => (
          <span key={u} className={styles.uLabel} style={{ top: uTop(u, heightU) }}>
            {u}
          </span>
        ))}
        <span className={styles.rail} style={{ height: heightPx }} />
        {drawn.map((device) => {
          const unit = device.rack_unit!;
          const uH = device.u_height && device.u_height > 1 ? device.u_height : 1;
          return (
            <button
              key={device.device_id}
              type="button"
              className={styles.device}
              style={{ top: uTop(unit + uH - 1, heightU) + 1, height: Math.max(U_PX * uH - 2, 9) }}
              data-testid={`rack-open-${device.device_id}`}
              onClick={() => onOpenDevice(device.device_id)}
            >
              {`U${unit}${uH > 1 ? `–${unit + uH - 1}` : ""} ${device.name}`}
            </button>
          );
        })}
      </div>
      {listed.length > 0 && (
        <div className={styles.listed}>
          {listed.map((device) => (
            <div key={device.device_id} className={styles.listedRow}>
              <button type="button" data-testid={`rack-open-${device.device_id}`} onClick={() => onOpenDevice(device.device_id)}>
                <Mono>{device.name}</Mono>
              </button>
              <span>in rack, no unit</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------ add and rename ----------------------------- */

type FormState = { mode: "add"; parent: Place | null } | { mode: "edit"; place: Place };

function PlaceForm({
  form,
  places,
  roomTypes,
  onDone,
  onCancel,
}: {
  form: FormState;
  places: Place[];
  roomTypes: string[];
  onDone: (id: string) => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const edit = form.mode === "edit" ? form.place : null;
  const kind: PlaceKind = edit ? edit.kind : form.mode === "add" && form.parent ? PLACE_KINDS[PLACE_KINDS.indexOf(form.parent.kind) + 1]! : "site";
  const parentId = form.mode === "add" ? (form.parent?.id ?? null) : null;
  const [name, setName] = useState(edit?.name ?? "");
  const { code, setCode, id: newId, taken } = useCodeSuggestion(parentId, name, places, !edit);
  const [roomType, setRoomType] = useState(edit?.room_type ?? "");
  const [address, setAddress] = useState(edit?.address ?? "");
  const [contact, setContact] = useState(edit?.contact ?? "");
  const [notes, setNotes] = useState(edit?.notes ?? "");
  const [small, setSmall] = useState(false);
  const [chain, setChain] = useState({ locName: "Main", locCode: "M", bldName: "Main", bldCode: "M" });
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!name.trim()) {
      toast.error("A name is required.");
      return;
    }
    setBusy(true);
    try {
      const fields = {
        name: name.trim(),
        ...(kind === "room" ? { room_type: roomType } : {}),
        ...(kind === "site" ? { address: address.trim(), contact: contact.trim() } : {}),
        notes: notes.trim(),
      };
      const result = edit
        ? await updatePlace(edit.id, fields)
        : await createPlace({
            parent_id: parentId,
            code: code.trim().toUpperCase(),
            ...fields,
            ...(small && kind === "site"
              ? {
                  chain: [
                    { name: chain.locName.trim(), code: chain.locCode.trim().toUpperCase() },
                    { name: chain.bldName.trim(), code: chain.bldCode.trim().toUpperCase() },
                  ],
                }
              : {}),
          });
      if (!result.ok) throw new Error(result.error || "Save failed.");
      toast.success(edit ? "Place updated." : "Place created.");
      onDone(result.place?.id ?? edit?.id ?? "");
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.form} data-testid="place-form">
      <strong>{edit ? `Rename ${edit.id}` : `Add ${kind}${form.mode === "add" && form.parent ? ` under ${form.parent.id}` : ""}`}</strong>
      <TextInput aria-label="Place name" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
      {!edit && (
        <TextInput
          aria-label="Place code"
          placeholder="Code (permanent, A-Z and 0-9, up to 8)"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      )}
      {!edit && (
        <span className={styles.mainSub}>
          ID will be <Mono>{newId || "…"}</Mono>. The ID is permanent; the name can be changed later.
        </span>
      )}
      {taken && (
        <span role="alert">{`Already used by ${taken.name} (${taken.id}).`}</span>
      )}
      {kind === "room" && (
        <Select aria-label="Room type" value={roomType} onChange={(e) => setRoomType(e.target.value)}>
          <option value="">Room type</option>
          {roomTypes.map((t) => (
            <option key={t} value={t}>
              {t.replace(/_/g, " ")}
            </option>
          ))}
        </Select>
      )}
      {kind === "site" && (
        <>
          <TextInput aria-label="Address" placeholder="Address" value={address} onChange={(e) => setAddress(e.target.value)} />
          <TextInput aria-label="Contact" placeholder="Contact" value={contact} onChange={(e) => setContact(e.target.value)} />
        </>
      )}
      <TextInput aria-label="Notes" placeholder="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
      {!edit && kind === "site" && (
        <>
          <label className={styles.inline}>
            <input type="checkbox" aria-label="Small site" checked={small} onChange={(e) => setSmall(e.target.checked)} />
            Small site: create its one location and one building with it
          </label>
          {small && (
            <div className={styles.formRow}>
              <TextInput aria-label="Location name" value={chain.locName} onChange={(e) => setChain({ ...chain, locName: e.target.value })} />
              <TextInput aria-label="Location code" value={chain.locCode} onChange={(e) => setChain({ ...chain, locCode: e.target.value })} />
              <TextInput aria-label="Building name" value={chain.bldName} onChange={(e) => setChain({ ...chain, bldName: e.target.value })} />
              <TextInput aria-label="Building code" value={chain.bldCode} onChange={(e) => setChain({ ...chain, bldCode: e.target.value })} />
            </div>
          )}
        </>
      )}
      <div className={styles.formRow}>
        <Button variant="primary" disabled={busy || !!taken} onClick={() => void save()}>
          {edit ? "Save" : `Create ${kind}`}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/* ----------------------------------- merge ---------------------------------- */

/** Fold a place into another of the same kind: pick it, read what would happen, then confirm. */
function MergeForm({ from, places, onDone, onCancel }: { from: Place; places: Place[]; onDone: (id: string) => void; onCancel: () => void }) {
  const toast = useToast();
  const [into, setInto] = useState("");
  const [plan, setPlan] = useState<MergeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const options = places.filter((p) => p.kind === from.kind && p.active && p.id !== from.id);

  useEffect(() => {
    setPlan(null);
    if (!into) return;
    let live = true;
    mergePlaces(from.id, into, true)
      .then((r) => live && (r.ok ? setPlan(r) : toast.error(r.error || "Could not read the merge.")))
      .catch((caught: unknown) => live && toast.error(caught instanceof Error ? caught.message : String(caught)));
    return () => {
      live = false;
    };
    // toast is stable enough to leave out.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from.id, into]);

  async function apply() {
    setBusy(true);
    try {
      const r = await mergePlaces(from.id, into, false);
      if (!r.ok) throw new Error(r.error || "Merge failed.");
      toast.success(`${from.id} merged into ${into}. ${r.devices} device move${r.devices === 1 ? "" : "s"} waiting in Review.`);
      onDone(into);
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.form} data-testid="merge-form">
      <strong>{`Merge ${from.id} into`}</strong>
      <Select aria-label="Merge into" value={into} onChange={(e) => setInto(e.target.value)}>
        <option value="">{`Which ${from.kind}?`}</option>
        {options.map((p) => (
          <option key={p.id} value={p.id}>
            {`${p.name} (${p.id})`}
          </option>
        ))}
      </Select>
      {plan && (
        <div data-testid="merge-plan">
          <span>
            {`${plan.create.length} place${plan.create.length === 1 ? "" : "s"} would be created under ${plan.into_id}, ${plan.devices} device${plan.devices === 1 ? "" : "s"} would move (as proposals in Review)`}
            {plan.redirected ? `, ${plan.redirected} waiting move${plan.redirected === 1 ? "" : "s"} redirected` : ""}
            {`; ${plan.from_id} and what is under it would be marked inactive.`}
          </span>
          {plan.create.length > 0 && (
            <div className={styles.siteMeta}>{plan.create.map((c) => c.id).join(", ")}</div>
          )}
          {plan.sample.map((s) => (
            <div key={s.device_id} className={styles.siteMeta}>{`${s.name}: ${s.from} → ${s.to}`}</div>
          ))}
          {plan.devices > plan.sample.length && <div className={styles.siteMeta}>{`…and ${plan.devices - plan.sample.length} more`}</div>}
          {plan.clashes.map((c) => (
            <div key={c.id} role="alert">{`${c.name} (${c.id}) is already used under ${plan.into_id} as ${c.existing}. Rename one of them first.`}</div>
          ))}
        </div>
      )}
      <div className={styles.formRow}>
        <Button variant="primary" disabled={busy || !plan || plan.clashes.length > 0} data-testid="merge-apply" onClick={() => void apply()}>
          Merge
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/* ---------------------------------- the tab ---------------------------------- */

export function PlacesPanel({ canManage, onOpenDevice }: { canManage: boolean; onOpenDevice: (id: string) => void }) {
  const toast = useToast();
  const { confirm } = useConfirm();
  const canPlaces = useCan(PERMISSIONS.inventoryPlaces);
  const [place, setPlace] = useUrlState("place", "");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [form, setForm] = useState<FormState | null>(null);
  const [merging, setMerging] = useState<Place | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [busy, setBusy] = useState(false);

  const placesState = useAsync(() => listPlaces(true), []);
  const noPlaceState = useAsync(() => listDevices({ place: NONE, limit: 500 }), []);
  const places = useMemo(
    () => (placesState.state.status === "ready" && placesState.state.data.ok ? placesState.state.data.places : []),
    [placesState.state],
  );
  const roomTypes = placesState.state.status === "ready" ? (placesState.state.data.room_types ?? []) : [];
  const noPlace: Device[] =
    noPlaceState.state.status === "ready" && noPlaceState.state.data.ok
      ? (noPlaceState.state.data.devices ?? []).filter((d) => d.status !== "decommissioned")
      : [];

  const byId = useMemo(() => new Map(places.map((p) => [p.id, p])), [places]);
  const selectedId = place || (places.find((p) => p.parent_id === null)?.id ?? (noPlace.length ? NONE : ""));
  const selected = byId.get(selectedId) ?? null;
  const viewingNone = selectedId === NONE;

  const loadRacks = useCallback(() => (selected ? getRacks({ place: selected.id }) : Promise.resolve({ ok: true, racks: [] as RackGroup[] })), [selected]);
  const loadHere = useCallback(
    () => (selected && selected.kind !== "rack" ? listDevices({ place: selected.id, limit: 500 }) : Promise.resolve({ ok: true, devices: [] as Device[] })),
    [selected],
  );
  const racksState = useAsync(loadRacks, [selected?.id]);
  const hereState = useAsync(loadHere, [selected?.id]);
  const racks = useMemo(() => (racksState.state.status === "ready" ? (racksState.state.data.racks ?? []) : []), [racksState.state]);
  const unracked =
    selected && hereState.state.status === "ready"
      ? (hereState.state.data.devices ?? []).filter((d) => d.status !== "decommissioned" && d.place_id === selected.id)
      : [];

  // The rack drawings sit under the room they stand in.
  const rooms = useMemo(() => {
    const groups = new Map<string, RackGroup[]>();
    for (const r of racks) {
      const room = parentOf(r.place_id) ?? "";
      groups.set(room, [...(groups.get(room) ?? []), r]);
    }
    return [...groups.entries()];
  }, [racks]);

  // The tree, in ID order, minus whatever sits under a collapsed line.
  // Inactive places (and what is under them) stay hidden unless asked for, or unless the selected place is one of them.
  const shown = (p: Place) =>
    showInactive || ancestorsOf(p.id).every((a) => byId.get(a)?.active !== false) || ancestorsOf(selectedId).includes(p.id) || p.id === selectedId;
  const rows = places.filter((p) => shown(p) && !ancestorsOf(p.id).slice(0, -1).some((a) => collapsed.has(a)));
  const hasKids = (id: string) => childrenOf(places, id).length > 0;
  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  const mayAdd = (kind: PlaceKind) => (UPPER.has(kind) ? canPlaces : canManage);
  const childKind = selected && selected.kind !== "rack" ? PLACE_KINDS[PLACE_KINDS.indexOf(selected.kind) + 1]! : null;

  function reload() {
    placesState.reload();
    noPlaceState.reload();
    racksState.reload();
    hereState.reload();
  }

  async function setActive(p: Place, active: boolean) {
    setBusy(true);
    try {
      const result = await updatePlace(p.id, { active });
      if (!result.ok) throw new Error(result.error || "Update failed.");
      toast.success(active ? "Place is active again." : "Place marked inactive.");
      reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function remove(p: Place) {
    const ok = await confirm({
      title: `Delete ${p.id}?`,
      body: "Refused while a device, a place under it or a change request names it. Mark it inactive instead.",
      danger: true,
      confirmLabel: "Delete",
    });
    if (!ok) return;
    setBusy(true);
    try {
      const result = await deletePlace(p.id);
      if (!result.ok) throw new Error(result.error || "Delete failed.");
      toast.success("Place deleted.");
      setPlace(p.parent_id ?? "");
      reload();
    } catch (caught) {
      toast.error(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  if (placesState.state.status === "loading") return <CenteredMessage title="Loading places…" />;
  if (placesState.state.status === "error" || !placesState.state.data.ok) {
    return (
      <CenteredMessage
        title="Could not load places"
        detail={placesState.state.status === "error" ? placesState.state.error.message : placesState.state.data.error}
        action={
          <Button variant="primary" onClick={placesState.reload}>
            Retry
          </Button>
        }
      />
    );
  }

  return (
    <div className={styles.root} data-testid="places-panel">
      <div className={styles.sidebar}>
        <PlaceSearch places={places} onPick={setPlace} />
        <label className={styles.inline}>
          <input type="checkbox" aria-label="Show inactive" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
          Show inactive
        </label>
        <div className={styles.tree} role="tree" aria-label="Places">
          {rows.map((p) => {
            const depth = ancestorsOf(p.id).length - 1;
            return (
              <div key={p.id} className={styles.treeRow} style={{ paddingLeft: depth * 16 }}>
                {hasKids(p.id) ? (
                  <button type="button" className={styles.twisty} aria-label={`${collapsed.has(p.id) ? "Expand" : "Collapse"} ${p.id}`} onClick={() => toggle(p.id)}>
                    {collapsed.has(p.id) ? "▸" : "▾"}
                  </button>
                ) : (
                  <span className={styles.twisty} />
                )}
                <button
                  type="button"
                  role="treeitem"
                  className={`${styles.siteBtn} ${selectedId === p.id ? styles.siteOn : ""}`}
                  data-testid={`place-${p.id}`}
                  aria-selected={selectedId === p.id}
                  onClick={() => setPlace(p.id)}
                >
                  <span className={styles.siteName}>
                    <span>
                      <Mono>{p.code}</Mono> <strong>{p.name}</strong>
                    </span>
                    {!p.active && <Tag tone="muted">inactive</Tag>}
                  </span>
                  <span className={styles.siteCount}>{p.device_total}</span>
                </button>
              </div>
            );
          })}
          {noPlace.length > 0 && (
            <button
              type="button"
              className={`${styles.siteBtn} ${viewingNone ? styles.siteOn : ""}`}
              data-testid="place-none"
              aria-pressed={viewingNone}
              onClick={() => setPlace(NONE)}
            >
              <span className={styles.siteName}>
                <strong>No place yet</strong>
              </span>
              <span className={styles.siteCount}>{noPlace.length}</span>
            </button>
          )}
        </div>
        {canPlaces && (
          <Button data-testid="place-add-site" onClick={() => setForm({ mode: "add", parent: null })}>
            Add site
          </Button>
        )}
        {form?.mode === "add" && !form.parent && (
          <PlaceForm form={form} places={places} roomTypes={roomTypes} onCancel={() => setForm(null)} onDone={(id) => { setForm(null); if (id) setPlace(id); reload(); }} />
        )}
      </div>

      <div className={styles.main}>
        {viewingNone ? (
          <div className={styles.noSiteList} data-testid="place-no-site">
            <h2 className={styles.mainTitle}>No place yet</h2>
            <span className={styles.mainSub}>Devices with no place picked.</span>
            {noPlace.map((d) => (
              <button key={d.id} type="button" data-testid={`place-nosite-${d.id}`} onClick={() => onOpenDevice(d.id)}>
                <Mono>{d.name}</Mono>
              </button>
            ))}
          </div>
        ) : !selected ? (
          <div className={styles.empty} data-testid="places-empty">
            No places yet.
          </div>
        ) : (
          <>
            <div className={styles.mainHead}>
              <div>
                <PlacePath id={selected.id} places={places} />
                <h2 className={styles.mainTitle}>
                  {selected.name} 
                </h2>
                <span className={styles.mainSub}>
                  {label(selected.kind)}
                  {selected.room_type ? ` · ${selected.room_type.replace(/_/g, " ")}` : ""} · {selected.device_count} device
                  {selected.device_count === 1 ? "" : "s"} here, {selected.device_total} with everything below
                  {!selected.active && " · inactive"}
                </span>
              </div>
              <div className={styles.formRow}>
                {childKind && mayAdd(childKind) && (
                  <Button data-testid="place-add-child" onClick={() => setForm({ mode: "add", parent: selected })}>
                    {`Add ${childKind}`}
                  </Button>
                )}
                {mayAdd(selected.kind) && (
                  <>
                    <Button data-testid="place-rename" onClick={() => setForm({ mode: "edit", place: selected })}>
                      Rename
                    </Button>
                    <Button data-testid="place-active" disabled={busy} onClick={() => void setActive(selected, !selected.active)}>
                      {selected.active ? "Mark inactive" : "Mark active"}
                    </Button>
                  </>
                )}
                {canPlaces && selected.active && (
                  <Button data-testid="place-merge" disabled={busy} onClick={() => setMerging(selected)}>
                    Merge into
                  </Button>
                )}
                {canPlaces && (
                  <Button variant="danger" data-testid="place-delete" disabled={busy} onClick={() => void remove(selected)}>
                    Delete
                  </Button>
                )}
              </div>
            </div>
            {form && (form.mode === "edit" || form.parent) && (
              <PlaceForm
                key={form.mode === "edit" ? form.place.id : `add-${form.parent?.id}`}
                form={form}
                places={places}
                roomTypes={roomTypes}
                onCancel={() => setForm(null)}
                onDone={(id) => {
                  setForm(null);
                  if (id && form.mode === "add") setPlace(id);
                  reload();
                }}
              />
            )}
            {merging && merging.id === selected.id && (
              <MergeForm
                key={merging.id}
                from={merging}
                places={places}
                onCancel={() => setMerging(null)}
                onDone={(id) => {
                  setMerging(null);
                  setPlace(id);
                  reload();
                }}
              />
            )}
            {racksState.state.status === "ready" && !racksState.state.data.ok && (
              <div className={styles.empty}>{racksState.state.data.error || "Could not load racks."}</div>
            )}
            {rooms.length === 0 && kindOf(selected.id) !== "rack" && (
              <div className={styles.empty} data-testid="racks-empty">
                No racks under {selected.name}.
              </div>
            )}
            {rooms.map(([roomId, group]) => (
              <div key={roomId} className={styles.racks} data-testid="racks-panel">
                {roomId !== selected.id && <strong className={styles.rackRoom}>{byId.get(roomId)?.name ?? roomId}</strong>}
                <div className={styles.rackRow}>
                  {group.map((rack) => (
                    <RackCard key={rack.place_id} rack={rack} onOpenDevice={onOpenDevice} />
                  ))}
                </div>
              </div>
            ))}
            {unracked.length > 0 && (
              <div className={styles.unplaced} data-testid="racks-unplaced">
                <div className={styles.unplacedLeft}>
                  <strong>In {selected.name}, not in a rack</strong>
                  {unracked.map((d) => (
                    <button key={d.id} type="button" className={styles.link} data-testid={`place-here-${d.id}`} onClick={() => onOpenDevice(d.id)}>
                      <Tag tone="muted">{d.name}</Tag>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
