"""Warranty, support and end-of-life dates running out. Gated on `inventory.view`. Report only."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import reports, store


class InventoryLifecycle(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        kind = input.get("kind") or None
        if kind is not None and kind not in reports.KINDS:
            return {"ok": False, "error": "kind must be warranty, support or eol."}
        try:
            within = int(input.get("within_days") or reports.settings()["soon_days"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "within_days must be a whole number."}
        devices = store.list_devices(place=str(input.get("place") or "").strip())
        return {"ok": True, **reports.lifecycle(devices, today=reports.today(), within_days=max(1, within), kind=kind)}
