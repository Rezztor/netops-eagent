"""Look at the ports and parts the inventory last observed on a device, or which ports carry a VLAN.
Read-only by construction: it only calls `store` and `ports` reads. Descriptions are device text, so they go
through `redact.redact_text` before the model sees them. Gated on `inventory.view` in `helpers/tool_permissions.py`."""

from __future__ import annotations

import json

from helpers.tool import Response, Tool

from plugins._ea_inventory.helpers import ports, redact, store

CAP = 60


def _row(i: dict) -> dict:
    n = i["neighbor"]
    row = {
        "port": i["name"], "oper": {True: "up", False: "down"}.get(i["oper_up"], "unknown"), "speed": i["speed"],
        "mode": i["mode"], "vlans": i["vlans"], "ip": i["ip_prefixes"], "poe_watts": i["poe_watts"],
        "neighbor": f"{n['name']} {n['port']}".strip() if n else "", "uplink": i["is_uplink"],
        "description": redact.redact_text(i["description"])[0],
    }
    return {k: v for k, v in row.items() if v not in ("", [], None, False)}


class InventoryPorts(Tool):
    async def execute(self, **kwargs) -> Response:
        name, vlan = (str((self.args or {}).get(k) or "").strip() for k in ("device", "vlan"))
        if bool(name) == bool(vlan):
            return Response(message="Give either device (a device name) or vlan (a number).", break_loop=False)
        if vlan:
            if not (vlan.isascii() and vlan.isdigit() and 1 <= int(vlan) <= 4094):
                return Response(message="vlan must be a number from 1 to 4094.", break_loop=False)
            found = ports.vlan_ports(int(vlan))
            body = {"vlan": int(vlan), "ports_total": len(found), "ports": found[:CAP]}
        else:
            found, _total = store.search_devices(name=name, limit=1)
            device = found[0] if found else None
            if not device:
                return Response(message=f"No device named {name} in the inventory.", break_loop=False)
            data = ports.device_ports(device["id"])
            if not data["observed_at"]:
                return Response(message=f"{device['name']} has no observed ports yet; an engineer reads it from the Inventory screen.", break_loop=False)
            rows = [_row(i) for i in data["interfaces"]]
            summary = ports.summary(data["interfaces"], data["components"], device["mgmt_ip"])
            summary.pop("uplinks")  # already marked per port
            body = {
                "device": device["name"], "observed_at": data["observed_at"],
                "summary": summary,
                "ports_total": len(rows), "ports": rows[:CAP],
                "parts": [{k: v for k, v in {**c, "description": redact.redact_text(c["description"])[0]}.items() if v} for c in data["components"][:CAP]],
            }
        note = f" Showing the first {CAP}; narrow the question." if len(body["ports"]) < body["ports_total"] else ""
        return Response(message=f"Observed data, as of the last read.{note}\n\n{json.dumps(body, indent=1)}", break_loop=False)
