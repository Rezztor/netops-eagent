"""Conflicts between an automatic source and a human's value, and pins (ADR 0043).

A conflict is a record, never a change: accepting one files an ordinary `update` proposal
under the conflict's own source and approves it as the deciding engineer, so provenance
names that source and that engineer. Keeping suppresses the same observed value; pinning
suppresses the field for that source until it is unpinned.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from psycopg.types.json import Jsonb

from plugins._ea_inventory.helpers import precedence, proposals, store
from plugins._ea_inventory.helpers.normalise import InventoryError

DECISIONS = {"accept": "accepted", "keep": "kept", "pin": "pinned"}
_KEYS = (
    "id", "device_id", "device_name", "field", "current_value", "current_source", "proposed_value",
    "source", "observed_at", "status", "decided_by", "decided_at", "note", "created_at",
)


def _json(row: dict[str, Any]) -> dict[str, Any]:
    return {k: store._plain(row[k]) for k in _KEYS}


_SELECT = (
    "SELECT c.*, d.name AS device_name FROM inventory_conflict c "
    "JOIN inventory_device d ON d.id = c.device_id"
)


def upsert_conflicts(
    device_id: str, source: str, conflicts: list[dict[str, Any]], evidence: dict[str, Any],
    observed_at: _dt.datetime | None = None,
) -> dict[str, int]:
    """One open conflict per device, field and source: an identical one is refreshed, one whose
    observed value changed is superseded by a new one."""
    made = {"created": 0, "refreshed": 0, "superseded": 0}
    at = observed_at or _dt.datetime.now(_dt.timezone.utc)
    with store.transaction() as conn:
        for c in conflicts:
            open_ = store.rows(
                conn, "SELECT id, proposed_value FROM inventory_conflict WHERE device_id = %s AND field = %s "
                "AND source = %s AND status = 'open' FOR UPDATE", (device_id, c["field"], source))
            if open_ and open_[0]["proposed_value"] == c["proposed"]:
                conn.execute(
                    "UPDATE inventory_conflict SET current_value = %s, current_source = %s, evidence = %s, "
                    "observed_at = %s WHERE id = %s",
                    (Jsonb(c["current"]), c["current_source"], Jsonb(evidence), at, open_[0]["id"]))
                made["refreshed"] += 1
                continue
            if open_:
                conn.execute(
                    "UPDATE inventory_conflict SET status = 'superseded', decided_at = now(), "
                    "note = 'The observed value changed.' WHERE id = %s", (open_[0]["id"],))
                made["superseded"] += 1
            conn.execute(
                "INSERT INTO inventory_conflict (device_id, field, current_value, current_source, "
                "proposed_value, source, evidence, observed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (device_id, c["field"], Jsonb(c["current"]), c["current_source"], Jsonb(c["proposed"]),
                 source, Jsonb(evidence), at))
            made["created"] += 1
    return made


def list_conflicts(status: str = "open", device_id: str = "", limit: int = 100, offset: int = 0):
    where, args = [], []
    if status:
        where.append("c.status = %s")
        args.append(status)
    if device_id:
        where.append("c.device_id::text = %s")
        args.append(device_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with store.transaction() as conn:
        total = store.rows(conn, f"SELECT count(*) AS n FROM inventory_conflict c{clause}", args)[0]["n"]
        found = store.rows(
            conn, f"{_SELECT}{clause} ORDER BY c.created_at DESC LIMIT %s OFFSET %s",
            [*args, max(1, min(int(limit or 100), 500)), max(0, int(offset or 0))])
        open_count = store.rows(conn, "SELECT count(*) AS n FROM inventory_conflict WHERE status = 'open'")[0]["n"]
    return [_json(r) for r in found], total, open_count


def _get(conflict_id: str) -> dict[str, Any]:
    with store.transaction() as conn:
        found = store.rows(conn, f"{_SELECT} WHERE c.id::text = %s", (str(conflict_id),)) if proposals._is_uuid(conflict_id) else []
    if not found:
        raise InventoryError("There is no such conflict.")
    return found[0]


def _close(conflict_id: str, status: str, note: str, actor: str) -> dict[str, Any]:
    with store.transaction() as conn:
        done = conn.execute(
            "UPDATE inventory_conflict SET status = %s, decided_by = %s, decided_at = now(), note = %s "
            "WHERE id = %s AND status = 'open'", (status, actor, note, conflict_id))
        if not done.rowcount:
            raise InventoryError("This conflict is already decided.")
    return _json(_get(conflict_id))


def decide_conflict(
    conflict_id: str, decision: str, note: str = "", actor: str = "",
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Returns (conflict, proposal, device); proposal and device only for `accept`."""
    if decision not in DECISIONS:
        raise InventoryError(f"decision must be one of: {', '.join(DECISIONS)}.")
    note = str(note or "").strip()
    c = _get(conflict_id)
    if c["status"] != "open":
        raise InventoryError(f"This conflict is already {c['status']}.")
    proposal = device = None
    status = DECISIONS[decision]
    if decision == "accept":
        current = store.get_device(str(c["device_id"]))
        evidence = {
            **{k: v for k, v in (c["evidence"] or {}).items() if k != "batch_id"},  # not a facts batch
            "source": c["source"], "conflict_id": str(c["id"]), "observed_at": c["observed_at"].isoformat(),
        }
        try:
            made = proposals.propose(
                "update", str(c["device_id"]), precedence.to_fields(current, {c["field"]: c["proposed_value"]}),
                c["source"], evidence, actor)
            proposal, device = proposals.decide(made["id"], "approve", note, actor=actor)
        except proposals.StaleProposal:
            status = "superseded"
        except InventoryError as error:
            if not str(error).startswith("Nothing would change"):
                raise
            status = "superseded"
    elif decision == "pin":
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO inventory_pin (device_id, field, source, pinned_by) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING", (c["device_id"], c["field"], c["source"], actor))
        proposals._emit("inventory.pinned", device_id=str(c["device_id"]), field=c["field"], source=c["source"], pinned_by=actor)
    conflict = _close(conflict_id, status, note, actor)
    proposals._emit(
        "inventory.conflict_decided", conflict_id=conflict["id"], device_id=conflict["device_id"],
        field=conflict["field"], source=conflict["source"], decision=decision, status=status, decided_by=actor)
    return conflict, proposal, device


def unpin(device_id: str, field: str, source: str, actor: str = "") -> None:
    if not proposals._is_uuid(device_id):
        raise InventoryError("There is no such pin.")
    with store.transaction() as conn:
        done = conn.execute(
            "DELETE FROM inventory_pin WHERE device_id = %s AND field = %s AND source = %s", (device_id, field, source))
        if not done.rowcount:
            raise InventoryError("There is no such pin.")
    proposals._emit("inventory.unpinned", device_id=device_id, field=field, source=source, unpinned_by=actor)


def list_pins(device_id: str = "") -> list[dict[str, Any]]:
    with store.transaction() as conn:
        found = store.rows(
            conn, "SELECT p.*, d.name AS device_name FROM inventory_pin p JOIN inventory_device d ON d.id = p.device_id "
            "WHERE (%s = '' OR p.device_id::text = %s) ORDER BY d.name, p.field, p.source", (device_id, device_id))
    keys = ("device_id", "device_name", "field", "source", "pinned_by", "pinned_at")
    return [{k: store._plain(r[k]) for k in keys} for r in found]


def load_all() -> dict[str, tuple[set[tuple[str, str]], set[tuple[str, str, str]]]]:
    """`{device_id: (pins, kept)}` for every device that has either (the SolarWinds run asks for all)."""
    out: dict[str, tuple[set, set]] = {}
    with store.transaction() as conn:
        for r in store.rows(conn, "SELECT device_id, field, source FROM inventory_pin"):
            out.setdefault(str(r["device_id"]), (set(), set()))[0].add((r["field"], r["source"]))
        for r in store.rows(conn, "SELECT device_id, field, source, proposed_value FROM inventory_conflict WHERE status = 'kept'"):
            out.setdefault(str(r["device_id"]), (set(), set()))[1].add((r["field"], r["source"], str(r["proposed_value"]).strip()))
    return out


def load_pins_and_kept(device_id: str) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
    return load_all().get(str(device_id), (set(), set()))
