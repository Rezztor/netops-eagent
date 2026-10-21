"""The place tree: site > location > building > floor > room > rack (ADR 0054).

Any node at any level is a *place*; `location` is only the second level. The rules (ID is the
parent's ID plus the code, kind is the depth, IDs never change) are enforced by the table in
`helpers/schema/0015_inventory_places.sql`; the messages here only make a refusal readable.

The first half is pure. The second half reads and writes `inventory_place` as the application
role with no user scope, like the rest of the inventory.

`normalise` is imported as a module, not by name, so `normalise.py` may import this module back.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Iterable

from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from psycopg.rows import dict_row

from helpers import db
from plugins._ea_inventory.helpers import normalise

KINDS = ("site", "location", "building", "floor", "room", "rack")
ROOM_TYPES = ("server_room", "comms_room", "riser", "office", "store", "other")
CODE = re.compile(r"^[A-Z0-9]{1,8}$")
_ID = re.compile(r"^[A-Z0-9]{1,8}(-[A-Z0-9]{1,8}){0,5}$")
#: The default name and code of a small site's one location and one building.
SINGLE = ("Main", "M")


def _refuse(message: str, existing: dict[str, Any] | None = None) -> Exception:
    error = normalise.InventoryError(message)
    error.existing = existing  # type: ignore[attr-defined]  # a taken code or name: the picker offers "Use it"
    return error


# --- pure --------------------------------------------------------------------

def normalise_id(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if not _ID.match(text):
        raise _refuse(
            f"{text!r} is not a place ID: codes of 1 to 8 letters or digits, joined by '-', at most six levels."
        )
    return text


def depth(id: str) -> int:
    return id.count("-") + 1


def kind_of(id: str) -> str:
    return KINDS[depth(id) - 1]


def ancestors(id: str) -> list[str]:
    parts = id.split("-")
    return ["-".join(parts[: i + 1]) for i in range(len(parts))]


def under(id: str) -> tuple[str, str]:
    """The equality value and the LIKE pattern that together match a place and everything below it."""
    return id, id + "-%"


def name_key(text: Any) -> str:
    return "".join(c for c in str(text or "").casefold() if c.isalnum())


_DROP = {"building", "block", "tower"}


def _free(base: str, taken: Iterable[str]) -> str:
    taken = {t.upper() for t in taken}
    base = (re.sub(r"[^A-Z0-9]", "", base.upper()) or "X")[:8]
    code, n = base, 1
    while code in taken:
        n += 1
        code = base[: 8 - len(str(n))] + str(n)
    return code


def _next(prefix: str, taken: set[str]) -> str:
    n = 1
    while f"{prefix}{n:02d}" in taken:
        n += 1
    return f"{prefix}{n:02d}"


def _initials(name: str, drop: bool = True) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    kept = [w for w in words if not drop or w.lower() not in _DROP] or words
    if len(kept) == 1 and len(kept[0]) <= 8:
        return kept[0]
    return "".join(w if w.isdigit() else w[0] for w in kept)


def _number(name: str) -> str:
    """The first number in a name, separators removed: "Room 12" -> "12", "1.01" -> "101"."""
    found = re.search(r"\d+(?:[.-]\d+)*", name)
    return re.sub(r"\D", "", found.group()) if found else ""


def suggest_code(kind: str, name: str, taken: set[str]) -> str:
    """A suggestion only: the owner chooses the code."""
    taken = {t.upper() for t in taken}
    low, number = name.lower(), _number(name)
    if kind == "floor":
        if "ground" in low:
            return _free("GF", taken)
        if "basement" in low:
            return _free("B" + (number or "1"), taken)
        if "roof" in low:
            return _free("RF", taken)
        if number:
            return _free("L" + number, taken)
    elif kind == "room":
        return _free("R" + number.zfill(2), taken) if number else _next("R", taken)
    elif kind == "rack":
        return _free("RK" + number.zfill(2), taken) if number else _next("RK", taken)
    return _free(_initials(name, drop=kind != "site"), taken)


# --- database ----------------------------------------------------------------

def _tx():
    return db.transaction(db.NO_SCOPE)


def _rows(conn: Any, sql: str, params: Any = ()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


_COLUMNS = ("id", "parent_id", "kind", "code", "name", "active", "room_type", "address", "contact", "notes")
_COUNTS = (
    "SELECT place_id, count(*) AS n FROM inventory_device "
    "WHERE place_id IS NOT NULL AND status <> 'decommissioned' GROUP BY place_id"
)


def _json(row: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    out = {k: row[k] for k in _COLUMNS}
    out["device_count"] = counts.get(row["id"], 0)
    out["device_total"] = sum(n for pid, n in counts.items() if pid == row["id"] or pid.startswith(row["id"] + "-"))
    return out


def effective_active(id: str, by_id: dict[str, dict[str, Any]]) -> bool:
    """False when the place or any place above it is inactive (or missing)."""
    return all(a in by_id and by_id[a]["active"] for a in ancestors(id))


def list_places(include_inactive: bool = True) -> list[dict[str, Any]]:
    with _tx() as conn:
        found = _rows(conn, f"SELECT {', '.join(_COLUMNS)} FROM inventory_place ORDER BY id")
        counts = {r["place_id"]: r["n"] for r in _rows(conn, _COUNTS)}
    by_id = {r["id"]: r for r in found}
    return [
        _json(r, counts) for r in found
        if include_inactive or effective_active(r["id"], by_id)
    ]


def get(id: str, conn: Any = None) -> dict[str, Any] | None:
    id = normalise_id(id)
    if not id:
        return None
    if conn is None:
        with _tx() as conn:
            return get(id, conn)
    found = _rows(conn, f"SELECT {', '.join(_COLUMNS)} FROM inventory_place WHERE id = %s", (id,))
    if not found:
        return None
    eq, like = under(id)
    counts = {
        r["place_id"]: r["n"] for r in _rows(
            conn, _COUNTS.replace("GROUP BY", "AND (place_id = %s OR place_id LIKE %s) GROUP BY"), (eq, like)
        )
    }
    return _json(found[0], counts)


def _audit(event: str, **fields: Any) -> None:
    from plugins._ea_inventory.helpers import proposals  # deferred: proposals imports store

    proposals._emit(event, **fields)  # never `contact`


def _name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 128:
        raise _refuse("A place needs a name (max 128 characters).")
    return name


def _code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if not CODE.match(code):
        raise _refuse(f"{code!r} is not a code: 1 to 8 letters or digits, no spaces or dashes.")
    return code


def _extras(kind: str, extras: dict[str, str]) -> dict[str, str]:
    """Trim, and refuse a value the level cannot hold (the table checks the same)."""
    out = {k: str(v or "").strip() for k, v in extras.items()}
    if out.get("room_type") and kind != "room":
        raise _refuse("Only a room has a room type.")
    if out.get("room_type") and out["room_type"] not in ROOM_TYPES:
        raise _refuse(f"Room type must be one of: {', '.join(ROOM_TYPES)}.")
    if (out.get("address") or out.get("contact")) and kind != "site":
        raise _refuse("Only a site has an address or a contact.")
    return out


def _taken(conn: Any, parent_id: str | None, id: str, name: str) -> None:
    """Refuse a code or a name already used under the parent, naming the place that has it."""
    for sql, params, what in (
        ("id = %s", (id,), "code"),
        ("COALESCE(parent_id, '') = %s AND lower(name) = lower(%s)", (parent_id or "", name), "name"),
    ):
        hit = _rows(conn, f"SELECT id, name FROM inventory_place WHERE {sql}", params)
        if hit:
            raise _refuse(
                f"The {what} is already used by {hit[0]['name']!r} ({hit[0]['id']}). Use that place or choose another.",
                get(hit[0]["id"], conn),
            )


def _violation(error: Exception) -> Exception:
    if isinstance(error, UniqueViolation):
        return _refuse("A place with that code or name already exists there.")
    return _refuse("That place breaks a rule of the tree: " + str(error).splitlines()[0])


def create(
    parent_id: str | None, name: str, code: str, *, room_type: str = "", address: str = "",
    contact: str = "", notes: str = "", chain: Iterable[tuple[str, str]] = (), actor: str = "",
) -> list[dict[str, Any]]:
    """Create a place, then each `chain` entry under the one before. All or nothing; the named place is first."""
    parent_id = normalise_id(parent_id)
    levels = [(_name(name), _code(code))] + [(_name(n), _code(c)) for n, c in chain]
    above = depth(parent_id) if parent_id else 0
    if parent_id and kind_of(parent_id) == "rack":
        raise _refuse("A rack is the lowest level; nothing goes under it.")
    if above + len(levels) > len(KINDS):
        raise _refuse("A place can sit at most six levels deep: site, location, building, floor, room, rack.")
    extra = _extras(KINDS[above], {"room_type": room_type, "address": address, "contact": contact})
    created: list[str] = []
    try:
        with _tx() as conn:
            if parent_id:
                by_id = {r["id"]: r for r in _rows(conn, "SELECT id, active FROM inventory_place WHERE id = ANY(%s)",
                                                  (ancestors(parent_id),))}
                if parent_id not in by_id:
                    raise _refuse(f"There is no place {parent_id}.")
                if not effective_active(parent_id, by_id):
                    raise _refuse(f"{parent_id} is inactive; nothing can be added under it.")
            current = parent_id
            for i, (n, c) in enumerate(levels):
                pid = f"{current}-{c}" if current else c
                _taken(conn, current, pid, n)
                kind = kind_of(pid)
                conn.execute(
                    "INSERT INTO inventory_place (id, parent_id, kind, code, name, room_type, address, contact, notes, "
                    "created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (pid, current, kind, c, n, extra.get("room_type", "") if i == 0 else "",
                     extra.get("address", "") if i == 0 else "", extra.get("contact", "") if i == 0 else "",
                     str(notes or "").strip() if i == 0 else "", actor),
                )
                created.append(pid)
                current = pid
            placed = [get(i, conn) for i in created]
    except (UniqueViolation, CheckViolation) as error:
        raise _violation(error) from None
    for p in placed:
        _audit("inventory.place_created", id=p["id"], kind=p["kind"], **({"actor": actor} if actor else {}))
    return placed  # type: ignore[return-value]


_EDITABLE = ("name", "room_type", "address", "contact", "notes", "active")


def update(id: str, **changes: Any) -> dict[str, Any]:
    id = normalise_id(id)
    unknown = sorted(set(changes) - set(_EDITABLE))
    if unknown:
        raise _refuse(f"A place's {', '.join(unknown)} cannot be changed here; only {', '.join(_EDITABLE)}.")
    values: dict[str, Any] = {}
    kind = kind_of(id) if id else ""
    if "name" in changes:
        values["name"] = _name(changes["name"])
    if "active" in changes:
        values["active"] = bool(changes["active"])
    for field, value in _extras(kind, {k: changes[k] for k in ("room_type", "address", "contact") if k in changes}).items():
        values[field] = value
    if "notes" in changes:
        values["notes"] = str(changes["notes"] or "").strip()
    try:
        with _tx() as conn:
            before = get(id, conn) if id else None
            if not before:
                raise _refuse(f"There is no place {id}.")
            if values:
                sets = ", ".join(f"{k} = %s" for k in values)
                conn.execute(f"UPDATE inventory_place SET {sets}, updated_at = now() WHERE id = %s", (*values.values(), id))
            after = get(id, conn)
    except UniqueViolation:
        clash = [p for p in list_places() if p["parent_id"] == before["parent_id"]
                 and p["name"].lower() == values.get("name", "").lower() and p["id"] != id]
        raise _refuse(
            f"The name is already used by {clash[0]['name']!r} ({clash[0]['id']})." if clash else "That name is taken here.",
            clash[0] if clash else None,
        ) from None
    except CheckViolation as error:
        raise _violation(error) from None
    changed = sorted(k for k in values if before[k] != after[k])
    if changed:
        _audit("inventory.place_updated", id=id, kind=kind, fields=changed)
    if "active" in changed:
        _audit("inventory.place_activated" if after["active"] else "inventory.place_deactivated", id=id, kind=kind)
    return after  # type: ignore[return-value]


def delete(id: str) -> None:
    """Only a place nothing ever pointed at; otherwise mark it inactive."""
    id = normalise_id(id)
    try:
        with _tx() as conn:
            row = get(id, conn) if id else None
            if not row:
                raise _refuse(f"There is no place {id}.")
            use = _rows(
                conn,
                "SELECT count(*) AS n, count(*) FILTER (WHERE status = 'decommissioned') AS gone "
                "FROM inventory_device WHERE place_id = %s", (id,),
            )[0]
            if use["n"]:
                raise _refuse(
                    f"{use['n']} device(s) point at {id} ({use['gone']} decommissioned). Mark it inactive instead."
                )
            kids = _rows(conn, "SELECT count(*) AS n FROM inventory_place WHERE parent_id = %s", (id,))[0]["n"]
            if kids:
                raise _refuse(f"{id} has {kids} place(s) under it. Mark it inactive instead.")
            seen = _rows(
                conn,
                "SELECT count(*) AS n FROM inventory_proposal "
                "WHERE changes -> 'place_id' ->> 'old' = %s OR changes -> 'place_id' ->> 'new' = %s", (id, id),
            )[0]["n"]
            if seen:
                raise _refuse(f"{seen} proposal(s) in the history name {id}. Mark it inactive instead.")
            conn.execute("DELETE FROM inventory_place WHERE id = %s", (id,))
    except ForeignKeyViolation:
        raise _refuse(f"{id} is still in use. Mark it inactive instead.") from None
    _audit("inventory.place_deleted", id=id, kind=row["kind"])


def resolve(
    site: str = "", location: str = "", building: str = "", floor: str = "", room: str = "", rack: str = "",
    place_id: str = "",
) -> tuple[str | None, str]:
    """`(id, "")`, or `(deepest id reached or None, why it stopped)`. Exact code or name only; inactive places never match."""
    with _tx() as conn:
        found = _rows(conn, "SELECT id, parent_id, kind, code, name, active FROM inventory_place")
    by_id = {r["id"]: r for r in found}
    pid = normalise_id(place_id)
    if pid:
        if pid not in by_id:
            return None, f"There is no place {pid}."
        if not effective_active(pid, by_id):
            return None, f"{pid} is inactive."
        return pid, ""
    given = [str(v or "").strip() for v in (site, location, building, floor, room, rack)]
    last = max((i for i, v in enumerate(given) if v), default=-1)
    if last < 0:
        return None, "No place named."
    current: str | None = None
    for i in range(last + 1):
        kind, value = KINDS[i], given[i]
        options = [r for r in found if r["parent_id"] == current and r["active"] and r["kind"] == kind]
        where = current or "any site"
        if not value:
            if len(options) != 1:
                return current, f"{kind} missing: {where} has {len(options)} {kind}s"
            current = options[0]["id"]
            continue
        hits = [r for r in options if r["code"] == value.upper() or name_key(r["name"]) == name_key(value)]
        if len(hits) != 1:
            return current, f"{kind} {value!r} " + ("is ambiguous" if hits else "not found") + f" under {where}"
        current = hits[0]["id"]
    return current, ""


def merge(from_id: str, into_id: str, *, dry_run: bool = True, actor: str = "") -> dict[str, Any]:
    """Fold `from_id` and everything under it into `into_id`, a place of the same kind (ADR 0057).

    Each place under `from_id` is found, or created with the same code, under `into_id`. Every device at or
    below `from_id` gets one `place_id` proposal to its match, in one batch for Review: devices are never
    written here (ADR 0040). Pending proposals that named a place under `from_id` are pointed at the match,
    and `from_id` and its subtree are marked inactive. A dry run changes nothing and says what would happen.
    """
    from plugins._ea_inventory.helpers import proposals, store  # deferred: both import this module

    src, dst = normalise_id(from_id), normalise_id(into_id)
    if not src or not dst:
        raise _refuse("Name the place to merge and the place to merge it into.")
    if src == dst:
        raise _refuse("Pick a different place to merge into.")
    if kind_of(src) != kind_of(dst):
        raise _refuse(f"A {kind_of(src)} can be merged only into another {kind_of(src)}, not a {kind_of(dst)}.")
    with _tx() as conn:
        by_id = {r["id"]: r for r in _rows(
            conn, "SELECT id, parent_id, kind, code, name, active, room_type, notes FROM inventory_place ORDER BY id")}
        if src not in by_id or dst not in by_id:
            raise _refuse(f"There is no place {src if src not in by_id else dst}.")
        if not effective_active(dst, by_id):
            raise _refuse(f"{dst} is inactive; merge into a place that is in use.")
        eq, like = under(src)
        moving = _rows(
            conn, "SELECT id::text AS id, name, place_id FROM inventory_device WHERE place_id = %s OR place_id LIKE %s "
            "ORDER BY name", (eq, like))
        staged = {r["device_id"]: r["new"] for r in _rows(
            conn, "SELECT device_id::text AS device_id, changes -> 'place_id' ->> 'new' AS new FROM inventory_proposal "
            "WHERE status = 'pending' AND device_id IS NOT NULL AND changes ? 'place_id'")}
        redirect = _rows(
            conn, "SELECT count(*) AS n FROM inventory_proposal WHERE status = 'pending' AND "
            "(changes -> 'place_id' ->> 'new' = %s OR changes -> 'place_id' ->> 'new' LIKE %s)", (eq, like))[0]["n"]
    match = lambda i: dst + i[len(src):]  # noqa: E731  the same codes under the new place
    missing, create_list, clashes = [], [], []
    for i in (i for i in by_id if i == src or i.startswith(src + "-")):  # ordered by id: parents first
        if match(i) in by_id:
            continue
        missing.append(i)
        sibling = next((p for p in by_id.values()
                        if p["parent_id"] == match(i).rpartition("-")[0] and p["name"].lower() == by_id[i]["name"].lower()), None)
        if sibling:
            clashes.append({"id": i, "name": by_id[i]["name"], "existing": sibling["id"]})
        create_list.append({"id": match(i), "name": by_id[i]["name"], "kind": by_id[i]["kind"]})
    todo = [m for m in moving if staged.get(m["id"]) != match(m["place_id"])]
    out: dict[str, Any] = {
        "from_id": src, "into_id": dst, "kind": kind_of(src), "dry_run": dry_run, "create": create_list,
        "clashes": clashes, "devices": len(todo), "redirected": redirect,
        "sample": [{"device_id": m["id"], "name": m["name"], "from": m["place_id"], "to": match(m["place_id"])}
                   for m in todo[:20]],
    }
    if dry_run:
        return out
    if clashes:
        raise _refuse(f"{clashes[0]['name']!r} under {src} already exists under {dst} as {clashes[0]['existing']}"
                      " with another code. Rename one of them first.")
    for i in missing:
        create(match(i).rpartition("-")[0], by_id[i]["name"], by_id[i]["code"], room_type=by_id[i]["room_type"],
               notes=by_id[i]["notes"], actor=actor)
    batch = str(uuid.uuid4())
    evidence = {"batch_id": batch, "file": "Place merge", "quote": f"{src} into {dst}"}
    for m in todo:
        proposals.propose("update", m["id"], {"place_id": match(m["place_id"])}, "manual", evidence, actor)
    with _tx() as conn:
        conn.execute(
            "UPDATE inventory_proposal SET changes = jsonb_set(changes, '{place_id,new}', "
            "to_jsonb(%s::text || substr(changes -> 'place_id' ->> 'new', %s))) WHERE status = 'pending' AND "
            "(changes -> 'place_id' ->> 'new' = %s OR changes -> 'place_id' ->> 'new' LIKE %s)",
            (dst, len(src) + 1, *under(src)))
        conn.execute("UPDATE inventory_place SET active = false, updated_at = now() WHERE (id = %s OR id LIKE %s) AND active",
                     under(src))
    _audit("inventory.place_merged", id=src, into=dst, kind=kind_of(src), created=len(create_list),
           devices=len(todo), batch_id=batch, **({"actor": actor} if actor else {}))
    return {**out, "dry_run": False, "batch_id": batch if todo else None}
