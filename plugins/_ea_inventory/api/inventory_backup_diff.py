"""A diff of two backups, every line redacted by the executor. Gated on `inventory.manage`."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import backups, store
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryBackupDiff(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        device = store.get_device(str(input.get("device_id") or ""))
        if not device:
            return {"ok": False, "error": "There is no such device."}
        try:
            out = await asyncio.to_thread(backups.diff, device, str(input.get("from") or ""), str(input.get("to") or ""))
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, "diff": out.get("diff", ""), "changed_lines": out.get("changed_lines", 0)}
