"""Staging: what the agent read from a file becomes proposals, with evidence (ADR 0041).

Pure of file reading: callers hand in a redacted `SourceDoc` (anything with
`.text`, `.source_id`) and the records the agent mapped. Nothing here touches a
device; `proposals.propose` records what would change and a person decides.
"""

from __future__ import annotations

import uuid
from typing import Any

from plugins._ea_inventory.helpers import places, proposals, store
from plugins._ea_inventory.helpers.normalise import InventoryError, normalise_fields

MAX_RECORDS = 200
MAX_QUOTE = 500
KEYS = ("name", "serial", "mgmt_ip")  # must appear in the source; a repeat in a batch is dropped


def _squash(text: Any) -> str:
    return " ".join(str(text).lower().split())


def _empty(value: Any) -> bool:
    return value in ("", None, [], {})


def _match(fields: dict[str, Any], devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devices matching by serial, management IP or name. Each key is unique, so more
    than one device means the keys point at different devices: ambiguous."""
    return [
        d for d in devices
        if any(
            fields.get(key) and d[key] == fields[key] and not (key == "mgmt_ip" and d["status"] == "decommissioned")
            for key in ("serial", "mgmt_ip", "name")
        )
    ]


def _one(record: Any, hay: str, devices: list[dict[str, Any]], seen: dict, warnings: list[str], index: int):
    """(kind, device_id, fields, evidence); None = unchanged, "dup" = repeat; InventoryError rejects."""
    if not isinstance(record, dict) or set(record) - {"fields", "evidence", "confidence"}:
        raise InventoryError("A record is {fields, evidence: {quote, location}, confidence?}.")
    ev = record.get("evidence")
    quote = str((ev or {}).get("quote") or "").strip() if isinstance(ev, dict) else ""
    location = str((ev or {}).get("location") or "").strip() if isinstance(ev, dict) else ""
    if not quote or not location:
        raise InventoryError("evidence needs a verbatim quote and a location.")
    if len(quote) > MAX_QUOTE:
        raise InventoryError(f"The quote is longer than {MAX_QUOTE} characters; quote only the row or line.")
    if _squash(quote) not in hay:
        raise InventoryError("The quote does not appear in the (redacted) source.")
    confidence = record.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1
    ):
        raise InventoryError("confidence must be a number from 0 to 1.")

    raw = record.get("fields")
    fields = normalise_fields(raw)
    fields = {k: v for k, v in fields.items() if not _empty(v)}  # an import never clears a field
    for key in KEYS:
        if key in fields and not (_squash(raw[key]) in hay or _squash(fields[key]) in hay):
            if key == "name":
                raise InventoryError(f"name {fields['name']!r} does not appear in the source.")
            warnings.append(f"Record {index}: {key} {fields.pop(key)!r} is not in the source; dropped.")
    if not fields.get("name"):
        raise InventoryError("name is required.")

    clash = next((seen[(k, fields[k])] for k in KEYS if (k, fields.get(k)) in seen), None)
    if clash is not None:
        warnings.append(f"Record {index} repeats record {clash}; kept the first.")
        return "dup"
    for k in KEYS:
        if k in fields:
            seen[(k, fields[k])] = index

    found = _match(fields, devices)
    if len(found) > 1:
        raise InventoryError("ambiguous: " + ", ".join(sorted(d["name"] for d in found)) + " all match.")
    evidence = {"quote": quote, "location": location, "confidence": confidence}
    if not found:
        return "create", None, fields, evidence
    device = found[0]
    diff = {f: v for f, v in fields.items() if v != device[f]}
    return ("update", device["id"], diff, evidence) if diff else None


def _resolve_place(record: Any, found: dict, missing: dict, warnings: list[str], index: int) -> Any:
    """`place_id`, or any of the six level names or codes as the template and the export write them, becomes a
    `place_id`. Only an existing place is used: a miss is staged without one and reported once in `missing`."""
    raw = record.get("fields") if isinstance(record, dict) else None
    keys = ("place_id", *places.KINDS)
    if not isinstance(raw, dict) or not set(keys) & set(raw):
        return record
    raw = dict(raw)
    given = {k: str(raw.pop(k) or "").strip() for k in keys if k in raw}
    if any(given.values()):
        key = tuple(sorted(given.items()))
        if key not in found:
            found[key] = places.resolve(**given)
        place, reason = found[key]
        if place and not reason:
            raw["place_id"] = place
        else:
            level = reason.split()[0]
            path = " / ".join(given[k] for k in places.KINDS if given.get(k)) or given.get("place_id", "")
            missing.setdefault(key, {"path": path, "level": level if level in places.KINDS else "place", "records": []})
            missing[key]["records"].append(index)
            warnings.append(f"Record {index}: {reason}; staged without a place. Add it under Places first.")
    return {**record, "fields": raw}


def stage(doc: Any, records: Any, file: str, actor: str, note: str = "") -> dict[str, Any]:
    if not isinstance(records, list) or not records:
        raise InventoryError("records must be a non-empty list.")
    if len(records) > MAX_RECORDS:
        raise InventoryError(f"At most {MAX_RECORDS} records per call; send the rest in another call.")
    batch_id = str(uuid.uuid4())
    hay, devices, seen = _squash(doc.text), store.list_devices(), {}
    found: dict = {}
    missing: dict = {}
    out: dict[str, Any] = {
        "batch_id": batch_id, "created": 0, "updates": 0, "unchanged": 0, "rejected": [], "warnings": [],
    }
    for i, record in enumerate(records):
        try:
            step = _one(_resolve_place(record, found, missing, out["warnings"], i), hay, devices, seen, out["warnings"], i)
            if step in (None, "dup"):
                out["unchanged"] += step is None
                continue
            kind, device_id, fields, ev = step
            evidence = {"file": proposals.shown_file(file), "source_id": doc.source_id, **ev, "batch_id": batch_id}
            if note:
                evidence["note"] = note
            proposals.propose(kind, device_id, fields, "extraction", evidence, actor)
            out["created" if kind == "create" else "updates"] += 1
        except InventoryError as error:
            out["rejected"].append({"index": i, "reason": str(error)})
    out["missing_places"] = list(missing.values())
    proposals._emit(
        "inventory.staged", batch_id=batch_id, created=out["created"], updates=out["updates"],
        unchanged=out["unchanged"], rejected=len(out["rejected"]), requested_by=actor,
    )
    return out


def decide_batch(
    batch_id: str, decision: str, min_confidence: Any = None, note: str = "", actor: str = ""
) -> dict[str, Any]:
    """Decide every pending proposal of a batch, each in its own transaction.

    A stale one is superseded by `proposals.decide` and reported; the rest carry on.
    `min_confidence` only holds back approvals. The caller runs the executor sync once.
    """
    if decision not in ("approve", "reject"):
        raise InventoryError("decision must be 'approve' or 'reject'.")
    try:
        uuid.UUID(str(batch_id))
    except ValueError:
        raise InventoryError("batch_id is not a valid id.") from None
    if min_confidence is not None:
        if isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float)) or not 0 <= min_confidence <= 1:
            raise InventoryError("min_confidence must be a number from 0 to 1.")
    with store.transaction() as conn:
        pending = store.rows(
            conn,
            "SELECT id, evidence->>'confidence' AS confidence FROM inventory_proposal "
            "WHERE evidence->>'batch_id' = %s AND status = 'pending' ORDER BY requested_at, id",
            (str(batch_id),),
        )
    if not pending:
        raise InventoryError("There are no pending proposals in that batch.")
    out: dict[str, Any] = {"decided": 0, "failed": [], "skipped_low_confidence": 0}
    for row in pending:
        if decision == "approve" and min_confidence is not None and (
            row["confidence"] is None or float(row["confidence"]) < min_confidence
        ):
            out["skipped_low_confidence"] += 1
            continue
        try:
            proposals.decide(str(row["id"]), decision, note, None, actor=actor)
            out["decided"] += 1
        except InventoryError as error:  # includes StaleProposal
            out["failed"].append({"id": str(row["id"]), "error": str(error)})
    return out
