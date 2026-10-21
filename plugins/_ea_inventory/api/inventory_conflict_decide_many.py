"""Decide several conflicts the same way. Gated on `inventory.approve`.

Each is decided on its own, so one that fails is reported and the rest carry on. The
executor sync runs once, after the last accepted one.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.api.inventory_decide import _sync
from plugins._ea_inventory.helpers import conflicts, proposals
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryConflictDecideMany(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        ids = input.get("ids")
        if not isinstance(ids, list) or not ids:
            return {"ok": False, "error": "ids must be a non-empty list."}
        decision, actor = str(input.get("decision") or ""), proposals.current_actor()
        decided, failed, changed = 0, [], False
        for conflict_id in ids:
            try:
                _c, _p, device = conflicts.decide_conflict(str(conflict_id), decision, input.get("note") or "", actor=actor)
                decided, changed = decided + 1, changed or bool(device)
            except InventoryError as error:
                failed.append({"id": str(conflict_id), "error": str(error)})
        return {"ok": True, "decided": decided, "failed": failed, "sync": _sync() if changed else None}
