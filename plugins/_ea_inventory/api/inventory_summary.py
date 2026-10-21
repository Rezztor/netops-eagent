"""Counts for the Inventory tab badges and the four attention tiles. Gated on `inventory.view`. Read only."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import conflicts, places, proposals, reports, store


class InventorySummary(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        devices = store.list_devices()
        cfg = reports.settings()
        day = reports.today()
        states = [
            reports.computed(
                d, today=day, required=cfg["required"], stale_days=cfg["stale_days"], soon_days=cfg["soon_days"],
                place_level=cfg["place_level"],
            )["lifecycle_state"]
            for d in devices if d["status"] != "decommissioned"
        ]
        quality = reports.quality(devices, cfg["required"], cfg["place_level"])
        stale, _days = store.stale_devices()
        return {
            "ok": True,
            "devices": len(states),
            "sites": sum(p["kind"] == "site" for p in places.list_places(False)),
            "pending": proposals.list_proposals(limit=1)[2],
            "conflicts": conflicts.list_conflicts(limit=1)[2],
            "lifecycle": {"expired": states.count("expired"), "soon": states.count("soon")},
            "incomplete": quality["total"] - quality["complete"],
            "average": quality["average"],
            "stale": {"count": len(stale), "names": [d["name"] for d in stale[:3]]},
        }
