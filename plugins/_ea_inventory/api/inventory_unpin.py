"""Remove a pin so the source may propose that field again. Gated on `inventory.approve`."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import conflicts, proposals
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryUnpin(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        try:
            conflicts.unpin(
                str(input.get("device_id") or ""), str(input.get("field") or ""), str(input.get("source") or ""),
                actor=proposals.current_actor(),
            )
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True}
