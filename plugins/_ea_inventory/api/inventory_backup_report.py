"""Active devices with no backup or a stale one. Gated on `inventory.view`. Report only."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import backups, reports, store


class InventoryBackupReport(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        stale = reports.settings()["backup_stale_days"]
        found = await asyncio.to_thread(backups.list_all)
        if found is None:
            return {"ok": False, "error": backups.unknown_reason()}
        items = backups.report(store.list_devices(status="active"), found, reports.now(), stale)
        return {"ok": True, "stale_days": stale, "items": items}
