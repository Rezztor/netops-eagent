"""Fields an engineer pinned against a source. Gated on `inventory.view`."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import conflicts


class InventoryPins(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        return {"ok": True, "pins": conflicts.list_pins(str(input.get("device_id") or ""))}
