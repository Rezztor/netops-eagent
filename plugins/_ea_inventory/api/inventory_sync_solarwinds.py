"""Fetch SolarWinds nodes and stage them as proposals (or preview with `dry_run`).

Gated on `inventory.approve` in `helpers/api_policy.py`: it makes a batch of requests in the
inbox and reads a monitoring system. It never changes a device itself.
"""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import proposals
from plugins._ea_inventory.helpers.adapters import solarwinds
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventorySyncSolarwinds(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        actor = proposals.current_actor()
        try:  # off the event loop: a full fetch is many paged HTTP calls
            out = await asyncio.to_thread(solarwinds.run_solarwinds_sync, bool(input.get("dry_run")), actor)
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, **out}
