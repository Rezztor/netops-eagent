"""Open (or decided) conflicts between an observed value and a person's. Gated on `inventory.view`."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import conflicts


class InventoryConflicts(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        found, total, open_count = conflicts.list_conflicts(
            str(input.get("status") or "open"), str(input.get("device_id") or ""),
            int(input.get("limit") or 100), int(input.get("offset") or 0),
        )
        return {"ok": True, "conflicts": found, "total": total, "open_count": open_count}
