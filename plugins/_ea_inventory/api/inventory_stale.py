"""Active devices no source has seen for a while. Gated on `inventory.view`. Report only."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import store


class InventoryStale(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        try:
            devices, days = store.stale_devices(input.get("days"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "days must be a whole number."}
        return {"ok": True, "devices": devices, "total": len(devices), "stale_days": days}
