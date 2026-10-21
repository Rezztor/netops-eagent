"""How complete the records are against `required_fields`. Gated on `inventory.view`. Report only."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import reports, store


class InventoryQuality(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        cfg = reports.settings()
        return {"ok": True, **reports.quality(store.list_devices(), cfg["required"], cfg["place_level"])}
