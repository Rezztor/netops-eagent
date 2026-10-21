"""One backup's text as the executor redacts it. Gated on `inventory.manage`: reading even
a redacted configuration is a person's decision. Never raw text (ADR 0047)."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import backups, store
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryBackupView(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        device = store.get_device(str(input.get("device_id") or ""))
        if not device:
            return {"ok": False, "error": "There is no such device."}
        try:
            return {"ok": True, "text": await asyncio.to_thread(backups.view, device, str(input.get("id") or ""))}
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
