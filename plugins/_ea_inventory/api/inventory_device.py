"""One device with its observations and proposals. Gated on `inventory.view`."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import backups, proposals, reports, store


class InventoryDevice(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        device = store.get_device(str(input.get("id") or ""))
        if not device:
            return {"ok": False, "error": "There is no such device."}
        with store.transaction() as conn:
            store.attach_sources(conn, [device])
        reports.attach([device], await asyncio.to_thread(backups.list_all))
        found, _total, _pending = proposals.list_proposals(device_id=device["id"], limit=50)
        return {
            "ok": True, "device": device,
            "observations": store.device_observations(device["id"]), "proposals": found,
        }
