"""The proposal queue. Gated on `inventory.view`."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import proposals


class InventoryProposals(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        found, total, pending = proposals.list_proposals(
            status=str(input.get("status") or ""), source=str(input.get("source") or ""),
            batch_id=str(input.get("batch_id") or ""),
            limit=int(input.get("limit") or 100), offset=int(input.get("offset") or 0),
        )
        return {"ok": True, "proposals": found, "total": total, "pending_count": pending}
