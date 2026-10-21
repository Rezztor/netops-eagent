"""Every rack under a place (all when none is given), with the devices and the U they occupy. Gated on `inventory.view`. Report only."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import places, reports, store


class InventoryRacks(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        place = str(input.get("place") or "").strip()
        return {"ok": True, **reports.racks(store.list_devices(place=place), places.list_places(False), place.upper())}
