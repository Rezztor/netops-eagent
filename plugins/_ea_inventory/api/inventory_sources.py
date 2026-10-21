"""The feeds that propose inventory changes, with the state of the last run. Gated on `inventory.view`."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import store
from plugins._ea_inventory.helpers.adapters.solarwinds import SOURCE


class InventorySources(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        settings = store.solarwinds_settings()
        try:
            from plugins._ea_solarwinds.helpers import config as sw_config

            configured = bool(sw_config.is_configured())
        except Exception:  # noqa: BLE001
            configured = False
        return {"ok": True, "sources": [{
            "source": SOURCE, "enabled": settings["enabled"], "configured": configured,
            "last_run": store.last_run(SOURCE), **store.source_stats(SOURCE, settings["stale_days"]),
        }]}
