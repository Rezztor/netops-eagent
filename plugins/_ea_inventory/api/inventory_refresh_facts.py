"""Refresh device facts through the Ansible executor (check mode, read only) and stage what
differs as requests or conflicts. Gated on `inventory.manage` in `helpers/api_policy.py`."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import proposals
from plugins._ea_inventory.helpers.facts import collect
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryRefreshFacts(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        names = input.get("devices")
        if names is not None and not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
            return {"ok": False, "error": "devices must be a list of device names."}
        try:  # off the event loop: each device is a job the executor runs
            out = await asyncio.to_thread(
                collect.refresh_facts, names, actor=proposals.current_actor(), dry_run=bool(input.get("dry_run")),
            )
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, "results": out.results, "batch_id": out.batch_id, "counts": out.counts}
