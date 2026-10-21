"""A device's backups: dates, sizes and hashes, never text. Gated on `inventory.view`."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import backups, store
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryBackups(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        device = store.get_device(str(input.get("device_id") or ""))
        if not device:
            return {"ok": False, "error": "There is no such device."}
        try:
            return {"ok": True, "backups": await asyncio.to_thread(backups.listing, device)}
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
