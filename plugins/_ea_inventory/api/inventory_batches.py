"""Imported proposals grouped by batch. Gated on `inventory.view`."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import proposals


class InventoryBatches(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        batches, total = proposals.list_batches(
            status=str(input.get("status") or ""),
            limit=int(input.get("limit") or 50), offset=int(input.get("offset") or 0),
        )
        return {"ok": True, "batches": batches, "total": total}
