"""The inventory as CSV or JSON. Gated on `inventory.view`."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import store

COLUMNS = (
    "name", "role", "vendor", "platform", "model", "serial", "asset_tag", "status",
    "place_id", "site", "location", "building", "floor", "room", "rack",
    "mgmt_ip", "software_version", "ha_group", "tags", "last_seen_at", "updated_at",
    "device_type", "part_number", "base_mac", "rack_unit", "u_height",
    "department", "custodian", "support_contact", "support_contract",
    "purchase_date", "warranty_expiry", "support_expiry", "eol_date",
)


def _cell(value: str) -> str:
    # A spreadsheet runs a cell that starts with these as a formula.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


class InventoryExport(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        fmt = str(input.get("format") or "")
        if fmt not in ("csv", "json"):
            return {"ok": False, "error": "format must be csv or json."}
        devices: list[dict[str, Any]] = []
        while True:
            page, total = store.search_devices(limit=500, offset=len(devices))
            devices += page
            if not page or len(devices) >= total:
                break
        if fmt == "json":
            return {
                "ok": True, "filename": "inventory.json", "content_type": "application/json",
                "content": json.dumps(devices, indent=2),
            }
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(COLUMNS)
        for d in devices:
            d = {**d, "site": (d["site"] or {}).get("name", ""), "tags": ";".join(d["tags"])}
            writer.writerow([_cell(str(d[c] or "")) for c in COLUMNS])
        return {"ok": True, "filename": "inventory.csv", "content_type": "text/csv", "content": out.getvalue()}
