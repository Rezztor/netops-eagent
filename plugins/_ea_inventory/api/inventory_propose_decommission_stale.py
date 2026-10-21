"""Propose decommissioning stale devices, one batch. Gated on `inventory.manage`.

Proposals only (ADR 0043): a person still approves each one, and nothing is deleted.
"""

from __future__ import annotations

import uuid
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import proposals, store
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryProposeDecommissionStale(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        ids = input.get("device_ids")
        if not isinstance(ids, list) or not ids:
            return {"ok": False, "error": "device_ids must be a non-empty list."}
        stale = {d["id"]: d for d in store.stale_devices()[0]}
        batch_id, actor, created, rejected = str(uuid.uuid4()), proposals.current_actor(), 0, []
        for device_id in map(str, ids):
            if device_id not in stale:
                rejected.append({"id": device_id, "reason": "not an active, stale device"})
                continue
            evidence = {
                "reason": "stale", "days_unseen": stale[device_id]["days_unseen"], "batch_id": batch_id,
                "note": str(input.get("note") or "")[:500],
            }
            try:
                proposals.propose("decommission", device_id, None, "manual", evidence, actor)
                created += 1
            except InventoryError as error:
                rejected.append({"id": device_id, "reason": str(error)})
        return {"ok": True, "batch_id": batch_id if created else None, "created": created, "rejected": rejected}
