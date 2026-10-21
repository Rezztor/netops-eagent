/**
 * The place picker: one level at a time (site > location > building > floor > room > rack), a search box on top,
 * a single choice filled in for you, and a create form inside the open list. Comboboxes are plain inputs with a
 * listbox under them, no library. Used by the device form, bulk edit, the Devices filter (`PlaceSearch`) and Places.
 */

import { Fragment, useId, useMemo, useState, type KeyboardEvent } from "react";

import {
  ROOM_TYPES,
  PLACE_KINDS,
  ancestorsOf,
  autoFill,
  childrenOf,
  createPlace,
  kindOf,
  searchPlaces,
  type Place,
  type PlaceKind,
} from "@/api/inventoryPlaces";
import { Button, Mono, Select, Tag, TextInput } from "@/components";

import styles from "./PlacePicker.module.css";
import { useCodeSuggestion } from "./useCodeSuggestion";

const LABEL: Record<PlaceKind, string> = {
  site: "Site",
  location: "Location",
  building: "Building",
  floor: "Floor",
  room: "Room",
  rack: "Rack",
};
const UPPER = new Set<PlaceKind>(["site", "location", "building"]);
const nameIn = (byId: Map<string, Place>, id: string) => byId.get(id)?.name ?? id.split("-").pop() ?? id;

/** Arrow keys and Enter for a list of `count` rows; Escape is handled by the wrapper. */
function useListNav(count: number, open: boolean, setOpen: (open: boolean) => void, pick: (index: number) => void) {
  const [index, setIndex] = useState(0);
  const at = Math.min(index, Math.max(count - 1, 0));
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) setOpen(true);
      else setIndex(Math.max(0, Math.min(at + (e.key === "ArrowDown" ? 1 : -1), count - 1)));
    } else if (e.key === "Enter" && open && count) {
      e.preventDefault();
      pick(at);
    }
  };
  return { at, setIndex, onKeyDown };
}

/** Escape closes the open list without closing a dialog around it; leaving the box closes it too. */
const boxProps = (open: boolean, close: () => void) => ({
  onKeyDown: (e: KeyboardEvent) => {
    if (e.key === "Escape" && open) {
      e.stopPropagation();
      close();
    }
  },
  onBlur: (e: React.FocusEvent<HTMLElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) close();
  },
});

/* ------------------------------- the search ------------------------------ */

export function PlaceSearch({
  places,
  onPick,
  empty = "Nothing matches.",
}: {
  places: Place[];
  onPick: (id: string) => void;
  empty?: string;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const listId = useId();
  const byId = useMemo(() => new Map(places.map((p) => [p.id, p])), [places]);
  const results = useMemo(() => searchPlaces(places, text).slice(0, 50), [places, text]);
  const close = () => setOpen(false);
  const pick = (i: number) => {
    onPick(results[i]!.id);
    setText("");
    close();
  };
  const nav = useListNav(results.length, open, setOpen, pick);
  const shown = open && text.trim() !== "";

  return (
    <div className={styles.box} {...boxProps(open, close)}>
      <TextInput
        role="combobox"
        aria-label="Find a place"
        aria-expanded={shown}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={shown && results.length ? `${listId}-${nav.at}` : undefined}
        placeholder="Type any part of an ID or a name"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          nav.setIndex(0);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={nav.onKeyDown}
      />
      {shown && (
        <div className={styles.popup}>
          {results.length ? (
            <ul role="listbox" id={listId} aria-label="Matching places" className={styles.list}>
              {results.map((p, i) => (
                <li
                  key={p.id}
                  id={`${listId}-${i}`}
                  role="option"
                  aria-selected={i === nav.at}
                  className={`${styles.option} ${i === nav.at ? styles.optionOn : ""}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => pick(i)}
                >
                  <span className={styles.optionMain}>
                    <Mono>{p.id}</Mono> {p.name}
                  </span>
                  {p.parent_id && (
                    <span className={styles.optionSub}>
                      {ancestorsOf(p.parent_id).map((a) => nameIn(byId, a)).join(" › ")}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.note}>{empty}</p>
          )}
        </div>
      )}
    </div>
  );
}

/* -------------------------------- the path ------------------------------- */

/** The breadcrumb with the ID: `DIFC › Gate Avenue › Gate Village Building 4  DIFC-GA-GV4`. */
export function PlacePath({ id, places }: { id: string; places: Place[] }) {
  const byId = new Map(places.map((p) => [p.id, p]));
  const chain = ancestorsOf(id);
  const inactive = chain.some((a) => byId.get(a) && !byId.get(a)!.active);
  return (
    <span className={styles.path}>
      {chain.map((a, i) => (
        <Fragment key={a}>
          {i > 0 && <span aria-hidden="true"> › </span>}
          <span>{nameIn(byId, a)}</span>
        </Fragment>
      ))}{" "}
      <Mono>{id}</Mono>
      {inactive && <Tag tone="warn">inactive</Tag>}
    </span>
  );
}

/* ------------------------------ the create form --------------------------- */

function CreateForm({
  kind,
  parentId,
  places,
  initialName,
  onCreated,
  onUse,
  onCancel,
}: {
  kind: PlaceKind;
  parentId: string | null;
  places: Place[];
  initialName: string;
  onCreated: (created: Place[], place: Place) => void;
  onUse: (place: Place) => void;
  onCancel: () => void;
}) {
  const byId = new Map(places.map((p) => [p.id, p]));
  const [name, setName] = useState(initialName);
  const [roomType, setRoomType] = useState("");
  const [address, setAddress] = useState("");
  const [contact, setContact] = useState("");
  const [small, setSmall] = useState(false);
  const [location, setLocation] = useState({ name: "Main", code: "M" });
  const [building, setBuilding] = useState({ name: "Main", code: "M" });
  const [error, setError] = useState("");
  const [existing, setExisting] = useState<Place | undefined>();
  const [busy, setBusy] = useState(false);
  const { code, setCode, id, taken } = useCodeSuggestion(parentId, name, places);
  const clean = (v: string) => v.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8);

  async function submit() {
    setBusy(true);
    setError("");
    setExisting(undefined);
    try {
      const result = await createPlace({
        parent_id: parentId,
        name: name.trim(),
        code,
        ...(kind === "room" && roomType ? { room_type: roomType } : {}),
        ...(kind === "site" ? { address: address.trim(), contact: contact.trim() } : {}),
        ...(kind === "site" && small ? { chain: [location, building] } : {}),
      });
      if (!result.ok || !result.place) {
        setError(result.error || "Could not create it.");
        setExisting(result.existing);
        return;
      }
      onCreated(result.created ?? [result.place], result.place);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.form} role="group" aria-label={`New ${kind}`}>
      <strong>New {kind}</strong>
      {parentId && (
        <div className={styles.chips} aria-label="Inside">
          <span className={styles.hint}>Inside (fixed by what you picked)</span>
          {ancestorsOf(parentId).map((a, i) => (
            <Fragment key={a}>
              {i > 0 && <span aria-hidden="true">›</span>}
              <span className={styles.chip}>
                {nameIn(byId, a)} <Mono>{a.split("-").pop()}</Mono>
              </span>
            </Fragment>
          ))}
        </div>
      )}
      <label className={styles.formRow}>
        Name
        <TextInput
          value={name}
          maxLength={128}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className={styles.formRow}>
        Code
        <TextInput
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      <span className={styles.hint}>
        ID will be <Mono>{id || "…"}</Mono>. The ID is permanent. The name can be changed later.
      </span>
      {taken && (
        <div role="alert" className={styles.error}>
          {`Already used by ${taken.name} (${taken.id}).`} <Button onClick={() => onUse(taken)}>Use it</Button>
        </div>
      )}
      {kind === "room" && (
        <label className={styles.formRow}>
          Room type
          <Select value={roomType} onChange={(e) => setRoomType(e.target.value)}>
            <option value="">Not set</option>
            {ROOM_TYPES.map((t) => (
              <option key={t} value={t}>
                {t.replace("_", " ")}
              </option>
            ))}
          </Select>
        </label>
      )}
      {kind === "site" && (
        <>
          <label className={styles.formRow}>
            Address (optional)
            <TextInput value={address} onChange={(e) => setAddress(e.target.value)} />
          </label>
          <label className={styles.formRow}>
            Contact (optional)
            <TextInput value={contact} onChange={(e) => setContact(e.target.value)} />
          </label>
          <label className={styles.tick}>
            <input type="checkbox" checked={small} onChange={(e) => setSmall(e.target.checked)} />
            Small site: one location and one building
          </label>
          {small && (
            <>
              <div className={styles.pair}>
                <label className={styles.formRow}>
                  Location name
                  <TextInput value={location.name} onChange={(e) => setLocation({ ...location, name: e.target.value })} />
                </label>
                <label className={styles.formRow}>
                  Location code
                  <TextInput value={location.code} onChange={(e) => setLocation({ ...location, code: clean(e.target.value) })} />
                </label>
              </div>
              <div className={styles.pair}>
                <label className={styles.formRow}>
                  Building name
                  <TextInput value={building.name} onChange={(e) => setBuilding({ ...building, name: e.target.value })} />
                </label>
                <label className={styles.formRow}>
                  Building code
                  <TextInput value={building.code} onChange={(e) => setBuilding({ ...building, code: clean(e.target.value) })} />
                </label>
              </div>
              <span className={styles.hint}>
                Will create <Mono>{code || "…"}</Mono>, <Mono>{`${code}-${location.code}`}</Mono> and{" "}
                <Mono>{`${code}-${location.code}-${building.code}`}</Mono>.
              </span>
            </>
          )}
        </>
      )}
      {error && (
        <div role="alert" className={styles.error}>
          {error}
          {existing && (
            <>
              {" "}
              <Button onClick={() => onUse(existing)}>Use it</Button>
            </>
          )}
        </div>
      )}
      <div className={styles.actions}>
        <Button variant="primary" disabled={busy || !name.trim() || !code || !!taken} onClick={() => void submit()}>
          Create {kind}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/* -------------------------------- one level ------------------------------- */

type Row = { type: "place"; place: Place } | { type: "create" };

function Level({
  kind,
  parentId,
  places,
  selected,
  canCreate,
  onlyOne,
  onPick,
  onClear,
  onCreated,
}: {
  kind: PlaceKind;
  parentId: string | null;
  places: Place[];
  selected: string | null;
  canCreate: boolean;
  onlyOne: boolean;
  onPick: (id: string) => void;
  onClear: () => void;
  onCreated: (created: Place[], place: Place) => void;
}) {
  const label = LABEL[kind];
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [creating, setCreating] = useState(false);
  const listId = useId();
  const byId = new Map(places.map((p) => [p.id, p]));
  const sel = selected ? byId.get(selected) : undefined;
  const q = text.trim().toLowerCase();
  const all = childrenOf(places, parentId).filter((p) => p.active);
  const matches = q ? all.filter((p) => p.name.toLowerCase().includes(q) || p.code.toLowerCase().includes(q)) : all;
  const nothing = q !== "" && matches.length === 0;
  const found: Row[] = (nothing ? all : matches).map((place) => ({ type: "place", place }));
  // With nothing matching, creating what was typed comes first; otherwise the create line ends the list.
  const rows: Row[] = !canCreate ? found : nothing ? [{ type: "create" }, ...found] : [...found, { type: "create" }];
  const where = parentId ? nameIn(byId, parentId) : "the list";
  const close = () => {
    setOpen(false);
    setCreating(false);
    setText("");
  };
  const choose = (row: Row | undefined) => {
    if (row?.type === "place") {
      onPick(row.place.id);
      close();
    } else if (row) setCreating(true);
  };
  const nav = useListNav(rows.length, open, setOpen, (i) => choose(rows[i]));

  return (
    <div className={styles.level} {...boxProps(open, close)}>
      <span className={styles.levelLabel}>{label}</span>
      <div className={styles.levelBox}>
        <TextInput
          role="combobox"
          aria-label={label}
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && !creating && rows.length ? `${listId}-${nav.at}` : undefined}
          placeholder={`Pick a ${kind}`}
          value={open ? text : (sel?.name ?? "")}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
            nav.setIndex(0);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={nav.onKeyDown}
        />
        {sel && <Mono>{sel.code}</Mono>}
        {sel && !sel.active && <Tag tone="warn">inactive</Tag>}
        {onlyOne && <span className={styles.only}>only one</span>}
        {sel && (
          <button type="button" className={styles.clear} aria-label={`Clear ${label}`} onClick={onClear}>
            ×
          </button>
        )}
      </div>
      {open && (
        <div className={styles.popup} tabIndex={-1}>
          {creating ? (
            <CreateForm
              kind={kind}
              parentId={parentId}
              places={places}
              initialName={text.trim()}
              onCreated={(created, place) => {
                onCreated(created, place);
                close();
              }}
              onUse={(place) => {
                onPick(place.id);
                close();
              }}
              onCancel={() => setCreating(false)}
            />
          ) : (
            <>
              {nothing && <p className={styles.note}>Nothing in {where} is called “{text.trim()}”.</p>}
              <ul role="listbox" id={listId} aria-label={`${label} choices`} className={styles.list}>
                {rows.map((row, i) => (
                  <li
                    key={row.type === "create" ? "create" : row.place.id}
                    id={`${listId}-${i}`}
                    role="option"
                    aria-selected={i === nav.at}
                    className={`${styles.option} ${i === nav.at ? styles.optionOn : ""}`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => choose(row)}
                  >
                    {row.type === "create" ? (
                      <span className={styles.create}>
                        + Create {kind}
                        {q ? ` “${text.trim()}”` : ""}
                      </span>
                    ) : (
                      <span className={styles.optionMain}>
                        <Mono>{row.place.code}</Mono> {row.place.name}
                      </span>
                    )}
                  </li>
                ))}
                {!rows.length && <li className={styles.note}>Nothing here yet.</li>}
              </ul>
              {!canCreate && (
                <p className={styles.note}>
                  Not in the list?{" "}
                  {UPPER.has(kind)
                    ? "Sites, locations and buildings are added by an administrator."
                    : "Floors, rooms and racks are added by someone who may manage the inventory."}
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------- the picker ------------------------------ */

export function PlacePicker({
  value,
  onChange,
  places,
  canCreate,
  onCreated,
}: {
  value: string | null;
  onChange: (id: string | null) => void;
  places: Place[];
  canCreate: { upper: boolean; lower: boolean };
  onCreated: (place: Place) => void;
}) {
  const [made, setMade] = useState<Place[]>([]);
  const [auto, setAuto] = useState<string[]>([]);
  const all = useMemo(() => {
    const known = new Set(places.map((p) => p.id));
    return [...places, ...made.filter((p) => !known.has(p.id))];
  }, [places, made]);
  const chain = value ? ancestorsOf(value) : [];
  const levels = PLACE_KINDS.slice(0, Math.min(chain.length + 1, PLACE_KINDS.length));

  /** Every pick passes through autoFill; the levels it filled get the "only one" mark. */
  function pick(id: string, base: Place[] = all) {
    const final = autoFill(base, id);
    const above = ancestorsOf(id);
    setAuto([...auto.filter((a) => above.includes(a)), ...ancestorsOf(final).filter((a) => a.length > id.length)]);
    onChange(final);
  }

  return (
    <div className={styles.picker}>
      <PlaceSearch
        places={all}
        onPick={(id) => pick(id)}
        empty="Nothing matches. A place is added from the list of its own level below."
      />
      {levels.map((kind, i) => (
        <Level
          key={kind}
          kind={kind}
          parentId={chain[i - 1] ?? null}
          places={all}
          selected={chain[i] ?? null}
          canCreate={UPPER.has(kind) ? canCreate.upper : canCreate.lower}
          onlyOne={chain[i] !== undefined && auto.includes(chain[i]!)}
          onPick={(id) => pick(id)}
          onClear={() => {
            setAuto(auto.filter((a) => chain.slice(0, i).includes(a)));
            onChange(chain[i - 1] ?? null);
          }}
          onCreated={(created, place) => {
            setMade((m) => [...m, ...created]);
            pick(place.id, [...all, ...created]);
            onCreated(place);
          }}
        />
      ))}
      <div className={styles.idLine}>
        Place ID <Mono>{value ?? "not set"}</Mono>
        <span className={styles.hint}>
          {value
            ? `${kindOf(value)} level${kindOf(value) === "rack" ? "" : " so far"}`
            : "Location, building, floor, room and rack appear one at a time as you pick."}
        </span>
      </div>
    </div>
  );
}
