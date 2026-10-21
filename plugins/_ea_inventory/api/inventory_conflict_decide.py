"""Accept, keep or pin one conflict. Gated on `inventory.approve`.

Accepting files an update proposal under the conflict's source and approves it as the
deciding engineer (ADR 0043); the executor inventory is then synced best effort.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.api.inventory_decide import _sync
from plugins._ea_inventory.helpers import conflicts, proposals
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryConflictDecide(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        try:
            conflict, proposal, device = conflicts.decide_conflict(
                str(input.get("id") or ""), str(input.get("decision") or ""), input.get("note") or "",
                actor=proposals.current_actor(),
            )
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        out: dict[str, Any] = {"ok": True, "conflict": conflict}
        if proposal:
            out["proposal"] = proposal
        if device:
            out["device"] = device
            out["sync"] = _sync()
        return out
