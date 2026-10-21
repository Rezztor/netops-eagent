"""Mark or unmark an observed port as an uplink. An annotation on an observed row, not a device field (ADR 0049),
so it is a direct audited write. Gated on `inventory.manage` in `helpers/api_policy.py`."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import ports, store


class InventoryPortUplink(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        device = store.get_device(str(input.get("device_id") or ""))
        name, flag = str(input.get("name") or ""), input.get("is_uplink")
        if not device or not name or not isinstance(flag, bool):
            return {"ok": False, "error": "device_id, name and is_uplink (true or false) are required."}
        if not ports.set_uplink(device["id"], name, flag):
            return {"ok": False, "error": f"{device['name']} has no observed port {name}; read the device first."}
        ports.emit("inventory.uplink_marked", device_id=device["id"], device=device["name"], port=name, is_uplink=flag)
        return {"ok": True}
