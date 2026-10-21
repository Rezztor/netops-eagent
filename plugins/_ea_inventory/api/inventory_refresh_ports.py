"""Read ports and parts from devices through the Ansible executor (check mode, read only) and replace their
stored snapshot. The click is the approval (ADR 0045), so it needs `inventory.manage` in `helpers/api_policy.py`."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import ports
from plugins._ea_inventory.helpers.facts import collect_ports
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryRefreshPorts(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        names = input.get("devices")
        if names is not None and not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
            return {"ok": False, "error": "devices must be a list of device names."}
        dry_run = bool(input.get("dry_run"))
        try:  # off the event loop: each device is a job the executor runs
            out = await asyncio.to_thread(collect_ports.refresh_ports, names, dry_run=dry_run)
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        if not dry_run:
            ports.emit(
                "inventory.ports_refreshed", refreshed=out["refreshed"], failed=len(out["failed"]),
                skipped=out["skipped"], interfaces=out["interfaces"], components=out["components"],
            )
        return {"ok": True, **out}
