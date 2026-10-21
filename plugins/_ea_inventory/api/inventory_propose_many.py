"""Propose the same field values for many devices, one batch. Gated on `inventory.manage`.

Proposals only (ADR 0040): one `update` per device, grouped by a shared `batch_id` so Pending
changes shows them together, and nothing changes until a person approves. Fields that must be
unique per device are refused here.
"""

from __future__ import annotations

import uuid
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import proposals, store
from plugins._ea_inventory.helpers.normalise import InventoryError, normalise_fields

MAX_DEVICES = 500
UNIQUE_FIELDS = ("name", "serial", "mgmt_ip", "asset_tag")


class InventoryProposeMany(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        ids = input.get("device_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_DEVICES:
            return {"ok": False, "error": f"device_ids must list 1 to {MAX_DEVICES} devices."}
        fields = input.get("fields")
        if not isinstance(fields, dict) or not fields:
            return {"ok": False, "error": "fields must name at least one field."}
        unique = sorted(set(fields) & set(UNIQUE_FIELDS))
        if unique:
            return {"ok": False, "error": f"{', '.join(unique)} must differ per device and cannot be bulk edited."}
        try:
            clean = normalise_fields(fields)
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        batch_id, actor = str(uuid.uuid4()), proposals.current_actor()
        evidence = {"batch_id": batch_id, "file": "Bulk edit", "note": str(input.get("note") or "")[:500]}
        created, unchanged, failed = 0, 0, []
        for device_id in dict.fromkeys(map(str, ids)):
            device = store.get_device(device_id)
            if device and all(device[f] == v for f, v in clean.items()):
                unchanged += 1
                continue
            try:
                proposals.propose("update", device_id, clean, "manual", evidence, actor)
                created += 1
            except InventoryError as error:
                failed.append({"device_id": device_id, "error": str(error)})
        proposals._emit(
            "inventory.bulk_proposed", batch_id=batch_id, created=created, unchanged=unchanged,
            failed=len(failed), requested_by=actor,
        )
        return {"ok": True, "batch_id": batch_id, "created": created, "unchanged": unchanged, "failed": failed}
