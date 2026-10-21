"""Which ports carry a VLAN, across the estate. Gated on `inventory.view` in `helpers/api_policy.py`."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import ports


class InventoryVlan(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        raw = str(input.get("vlan") or "").strip()
        if not (raw.isascii() and raw.isdigit()) or not 1 <= int(raw) <= 4094:
            return {"ok": False, "error": "vlan must be a number from 1 to 4094."}
        return {"ok": True, "vlan": int(raw), "ports": ports.vlan_ports(int(raw))}
