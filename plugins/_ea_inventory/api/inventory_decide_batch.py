"""Approve or reject every pending proposal of an import batch. Gated on `inventory.approve`.

Each proposal is decided on its own (`proposals.decide`), so a stale one is
superseded and reported while the rest carry on. The executor sync runs once,
after the last approval, and never undoes one.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.api.inventory_decide import _sync
from plugins._ea_inventory.helpers import proposals, staging
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryDecideBatch(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        try:
            out = staging.decide_batch(
                str(input.get("batch_id") or ""), str(input.get("decision") or ""),
                input.get("min_confidence"), str(input.get("note") or ""),
                actor=proposals.current_actor(),
            )
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, **out, "sync": _sync() if out["decided"] and input.get("decision") == "approve" else None}
