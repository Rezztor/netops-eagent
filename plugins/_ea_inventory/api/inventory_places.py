"""The place tree, for the Places tab and every picker. Needs `inventory.view` (`helpers/api_policy.py`)."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import places


class InventoryPlaces(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        return {
            "ok": True,
            "places": places.list_places(include_inactive=input.get("include_inactive", True) is not False),
            "room_types": list(places.ROOM_TYPES),
        }
