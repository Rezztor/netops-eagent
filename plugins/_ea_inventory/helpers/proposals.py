"""Every inventory change is a proposal that a person decides (ADR 0040).

`propose` validates and records what would change; `decide` applies it in one
transaction or rejects it. The approver may be the requester (ADR 0039), and the
audit line names both. Nothing here deletes a device: decommissioning is a status.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from plugins._ea_inventory.helpers import places, store
from plugins._ea_inventory.helpers.normalise import (
    DEFAULTS, FIELDS, SOURCES, InventoryError, normalise_fields, normalise_name,
)

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]

KINDS = ("create", "update", "decommission")


class StaleProposal(InventoryError):
    """The device moved on after the proposal was made."""


def _emit(event: str, **fields: Any) -> None:
    if audit is not None:
        audit.emit(event, **fields)


def current_actor() -> str:
    """The account name as the audit system records it."""
    if audit is None:
        return ""
    actor = audit.current_actor()
    return str(actor.get("username") or actor.get("user_id") or "")


def _check_place(conn: Any, fields: dict[str, Any], device: dict[str, Any] | None = None) -> None:
    """`fields` are the values the device would hold. A place the device does not already have must exist and
    be active, itself and every place above it; a rack unit needs a rack (the table checks the same)."""
    pid = fields.get("place_id")
    if pid and pid != (device or {}).get("place_id"):
        by_id = {r["id"]: r for r in store.rows(
            conn, "SELECT id, active FROM inventory_place WHERE id = ANY(%s)", (places.ancestors(pid),))}
        if pid not in by_id:
            raise InventoryError(f"There is no place {pid}.")
        if not places.effective_active(pid, by_id):
            raise InventoryError(f"{pid} is inactive; pick a place that is in use.")
    if fields.get("rack_unit") is not None and not (pid and places.depth(pid) == len(places.KINDS)):
        raise InventoryError("A rack unit goes with a rack: pick a rack, or clear the rack unit.")


def _db_value(field: str, value: Any) -> Any:
    if field in ("serial", "mgmt_ip"):
        return value or None
    return Jsonb(value) if field == "custom" else value


def propose(
    kind: str, device_id: str | None, fields: dict[str, Any] | None, source: str = "manual",
    evidence: dict[str, Any] | None = None, actor: str = "",
) -> dict[str, Any]:
    if kind not in KINDS:
        raise InventoryError(f"kind must be one of: {', '.join(KINDS)}.")
    if source not in SOURCES:
        raise InventoryError(f"source must be one of: {', '.join(SOURCES)}.")
    if evidence is not None and not isinstance(evidence, dict):
        raise InventoryError("evidence must be an object.")
    evidence = evidence or {}
    fields = normalise_fields(fields or {}) if kind != "decommission" else {}

    with store.transaction() as conn:
        if kind == "create":
            if device_id:
                raise InventoryError("A create proposal does not name a device.")
            fields.setdefault("name", "")
            fields["name"] = normalise_name(fields["name"])
            _check_place(conn, fields)
            store.check_unique(conn, fields, fields.get("status", DEFAULTS["status"]), None)
            changes = {f: {"old": None, "new": v} for f, v in fields.items()}
            name, base = fields["name"], None
        else:
            device = store.get_device(device_id or "", conn)
            if not device:
                raise InventoryError("There is no such device.")
            name, base = device["name"], device["version"]
            if kind == "decommission":
                if device["status"] == "decommissioned":
                    raise InventoryError(f"{name} is already decommissioned.")
                fields = {"status": "decommissioned"}
            changes = {
                f: {"old": device[f], "new": v} for f, v in fields.items() if v != device[f]
            }
            moved = changes.get("place_id", {}).get("new")
            if "place_id" in changes and (moved is None or places.depth(moved) < len(places.KINDS)) and device["rack_unit"] is not None:
                changes.setdefault("rack_unit", {"old": device["rack_unit"], "new": None})  # leaving a rack clears its unit
            if not changes:
                raise InventoryError("Nothing would change: every field already has that value.")
            merged = {**device, **{f: c["new"] for f, c in changes.items()}}
            _check_place(conn, merged, device)
            store.check_unique(conn, merged, merged["status"], device["id"])
        row = store.rows(
            conn,
            "INSERT INTO inventory_proposal (kind, device_id, device_name, base_version, changes, "
            "source, evidence, requested_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (kind, device_id or None, name, base, Jsonb(changes), source, Jsonb(evidence), actor),
        )[0]
    proposal = store.proposal_json(row)
    _emit(
        "inventory.proposed", proposal_id=proposal["id"], kind=kind, device_id=proposal["device_id"],
        device_name=name, source=source, fields=sorted(changes), requested_by=actor,
    )
    return proposal


def list_proposals(
    status: str = "", limit: int = 100, offset: int = 0, device_id: str = "",
    source: str = "", batch_id: str = "",
):
    where, args = [], []
    if source:
        where.append("source = %s")
        args.append(source)
    if batch_id:
        where.append("evidence->>'batch_id' = %s")
        args.append(batch_id)
    if status:
        where.append("status = %s")
        args.append(status)
    if device_id:
        where.append("device_id = %s")
        args.append(device_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with store.transaction() as conn:
        total = store.rows(conn, f"SELECT count(*) AS n FROM inventory_proposal{clause}", args)[0]["n"]
        found = store.rows(
            conn,
            f"SELECT * FROM inventory_proposal{clause} ORDER BY requested_at DESC LIMIT %s OFFSET %s",
            [*args, max(1, min(int(limit or 100), 500)), max(0, int(offset or 0))],
        )
        pending = store.rows(
            conn, "SELECT count(*) AS n FROM inventory_proposal WHERE status = 'pending'"
        )[0]["n"]
    return [store.proposal_json(r) for r in found], total, pending


def shown_file(path: str) -> str:
    """The name a person recognises for an imported file: the part under the user's own folder, never a server path
    (an absolute path would show the storage layout and the user's internal ID)."""
    path = str(path or "").strip().replace("\\", "/")
    if not path.startswith("/"):
        return path
    marker = "/usr/users/"
    if marker in path:
        rest = path.split(marker, 1)[1].split("/", 1)
        return rest[1] if len(rest) == 2 else rest[0]
    return path.rsplit("/", 1)[-1]


def list_batches(status: str = "", limit: int = 50, offset: int = 0):
    """Proposals grouped by the `batch_id` an import stamped on their evidence."""
    having = "HAVING count(*) FILTER (WHERE status = %s) > 0" if status else ""
    args = [status] if status else []
    inner = (
        "SELECT evidence->>'batch_id' AS batch_id, min(evidence->>'file') AS file, "
        "min(source) AS source, min(requested_by) AS requested_by, min(requested_at) AS requested_at, "
        "count(*) FILTER (WHERE status = 'pending') AS pending, "
        "count(*) FILTER (WHERE status = 'approved') AS approved, "
        "count(*) FILTER (WHERE status = 'rejected') AS rejected, "
        "count(*) FILTER (WHERE status = 'superseded') AS superseded, count(*) AS total "
        "FROM inventory_proposal WHERE evidence->>'batch_id' IS NOT NULL "
        f"GROUP BY 1 {having}"
    )
    with store.transaction() as conn:
        total = store.rows(conn, f"SELECT count(*) AS n FROM ({inner}) b", args)[0]["n"]
        found = store.rows(
            conn, f"{inner} ORDER BY min(requested_at) DESC LIMIT %s OFFSET %s",
            [*args, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))],
        )
    keys = ("pending", "approved", "rejected", "superseded")
    return [
        {
            "batch_id": r["batch_id"], "file": shown_file(r["file"]), "source": r["source"],
            "requested_by": r["requested_by"], "requested_at": store._plain(r["requested_at"]),
            "counts": {k: r[k] for k in keys}, "total": r["total"],
        }
        for r in found
    ], total


def decide(
    proposal_id: str, decision: str, note: str = "", edits: dict[str, Any] | None = None,
    actor: str = "",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Approve (apply) or reject a pending proposal. Returns (proposal, device)."""
    if decision not in ("approve", "reject"):
        raise InventoryError("decision must be 'approve' or 'reject'.")
    if edits is not None and not isinstance(edits, dict):
        raise InventoryError("edits must be an object.")
    note = str(note or "").strip()
    stale = False
    device = None
    try:
        with store.transaction() as conn:
            found = store.rows(
                conn, "SELECT * FROM inventory_proposal WHERE id = %s FOR UPDATE", (proposal_id,)
            ) if _is_uuid(proposal_id) else []
            if not found:
                raise InventoryError("There is no such proposal.")
            prop = found[0]
            if prop["status"] != "pending":
                raise InventoryError(f"This proposal is already {prop['status']}.")
            new_status = "rejected"
            changes = prop["changes"]
            if decision == "approve":
                device, stale = _apply(conn, prop, edits or {}, actor)
                if stale:
                    new_status = "superseded"
                else:
                    new_status, changes = "approved", device.pop("_changes")
            done = store.rows(
                conn,
                "UPDATE inventory_proposal SET status=%s, decided_by=%s, decided_at=now(), "
                "decision_note=%s, changes=%s, device_id=COALESCE(%s, device_id) WHERE id=%s RETURNING *",
                (new_status, actor, note, Jsonb(changes), device["id"] if device and not stale else None, proposal_id),
            )[0]
    except UniqueViolation:
        raise InventoryError(
            "That conflicts with another device's name, serial or management IP."
        ) from None
    proposal = store.proposal_json(done)
    _emit(
        f"inventory.{new_status}", proposal_id=proposal["id"], kind=proposal["kind"],
        device_id=proposal["device_id"], device_name=proposal["device_name"],
        requested_by=proposal["requested_by"], decided_by=actor, note=note,
    )
    if stale:
        raise StaleProposal("The device changed since this proposal was made; propose again.")
    return proposal, device


def _is_uuid(value: Any) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _apply(conn: Any, prop: dict[str, Any], edits: dict[str, Any], actor: str):
    """Write the approved values. Returns (device, stale); device carries `_changes`."""
    changes = {f: dict(c) for f, c in prop["changes"].items()}
    if edits and prop["kind"] == "decommission":
        raise InventoryError("A decommission cannot be edited; reject it and propose again.")
    stray = sorted(set(edits) - set(changes))
    if stray:
        raise InventoryError(f"edits may only change proposed fields, not: {', '.join(stray)}.")
    if edits:
        for field, value in normalise_fields(edits).items():
            changes[field]["new"] = value
    new = {f: c["new"] for f, c in changes.items()}
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    observed = (prop["evidence"] or {}).get("observed_at") or prop["requested_at"].isoformat()
    sources = {
        # A value the approver typed is theirs, not the source's: an automatic run must meet it as a manual value.
        f: {"source": "manual" if f in edits else prop["source"], "approved_by": actor, "approved_at": now, "observed_at": observed}
        for f in new
    }

    if prop["kind"] == "create":
        full = {**DEFAULTS, **new}
        _check_place(conn, full)
        store.check_unique(conn, full, full["status"], None)
        cols = [f for f in FIELDS]
        row = store.rows(
            conn,
            f"INSERT INTO inventory_device ({', '.join(cols)}, field_sources) "
            f"VALUES ({', '.join(['%s'] * len(cols))}, %s) RETURNING id",
            [_db_value(f, full[f]) for f in cols] + [Jsonb(sources)],
        )[0]
        device = store.get_device(str(row["id"]), conn)
        device["_changes"] = changes
        return device, False

    locked = store.rows(
        conn, "SELECT version FROM inventory_device WHERE id = %s FOR UPDATE", (prop["device_id"],)
    )
    if not locked or locked[0]["version"] != prop["base_version"]:
        return {"id": prop["device_id"]}, True
    device = store.get_device(str(prop["device_id"]), conn)
    merged = {**device, **new}
    _check_place(conn, merged, device)
    store.check_unique(conn, merged, merged["status"], device["id"])
    sets = ", ".join(f"{f} = %s" for f in new)
    conn.execute(
        f"UPDATE inventory_device SET {sets}, field_sources = field_sources || %s, "
        "version = version + 1, updated_at = now() WHERE id = %s",
        [*(_db_value(f, v) for f, v in new.items()), Jsonb(sources), prop["device_id"]],
    )
    device = store.get_device(str(prop["device_id"]), conn)
    device["_changes"] = changes
    return device, False
