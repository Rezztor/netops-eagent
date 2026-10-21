"""A device's observed ports and parts, with the computed summary. Gated on `inventory.view` in `helpers/api_policy.py`."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import ports, store


class InventoryPorts(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        device = store.get_device(str(input.get("device_id") or ""))
        if not device:
            return {"ok": False, "error": "There is no such device."}
        found = ports.device_ports(device["id"])
        found["summary"] = ports.summary(found["interfaces"], found["components"], device["mgmt_ip"])
        return {"ok": True, **found}
