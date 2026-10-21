"""Back up up to ten devices now. The click is the approval, so it needs `inventory.manage`
(`helpers/api_policy.py`). Audit carries names and counts, never text."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import backups, proposals, store
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryBackupRun(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        ids = input.get("device_ids")
        if not isinstance(ids, list) or not ids or len(ids) > backups.CAP or not all(isinstance(i, str) for i in ids):
            return {"ok": False, "error": f"device_ids must be a list of 1 to {backups.CAP} device ids."}
        devices = [store.get_device(i) for i in dict.fromkeys(ids)]
        if not all(devices):
            return {"ok": False, "error": "There is no such device."}
        try:
            results = await asyncio.to_thread(backups.run, devices)
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        proposals._emit(
            "inventory.backup_run", devices=[r["name"] for r in results], ok=sum(r["ok"] for r in results),
            failed=sum(not r["ok"] for r in results), changed=sum(bool(r["changed"]) for r in results),
            requested_by=proposals.current_actor(),
        )
        return {"ok": True, "results": results}
