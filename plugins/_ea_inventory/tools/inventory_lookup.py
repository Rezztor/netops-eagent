"""Look devices up in the inventory. Read-only by construction: it only calls
`store.search_devices` or `store.list_devices`. People's names (`custodian`,
`support_contact`) and free text (`custom`) are never returned. Gated on
`inventory.view` in `helpers/tool_permissions.py`.
"""

from __future__ import annotations

import datetime as _dt
import json

from helpers.tool import Response, Tool

from plugins._ea_inventory.helpers import places, reports, store

LIMIT = 25
COMPACT = (
    "name", "vendor", "platform", "model", "serial", "status", "mgmt_ip",
    "software_version", "ha_group", "tags",
    "device_type", "role", "place_id", "location", "building", "floor", "room", "rack", "rack_unit", "department",
    "warranty_expiry", "support_expiry", "eol_date",
)
_FILTERS = ("device_type", "role", "department")


def compact(device: dict, listing: dict | None = None) -> dict:
    row = {key: device.get(key) for key in COMPACT}
    row["site"] = (device.get("site") or {}).get("name", "")
    if listing and listing.get("known"):
        # The appliance's own network the address is in, as the approval card names it. Asked which interface a
        # device is reached through, the agent used to look at its sandbox and answer "eth0".
        from plugins._ea_ansible.helpers import network_list

        row["reached_through"] = network_list.label(str(device.get("mgmt_ip") or ""), listing)
    return row


def appliance_networks(agent) -> dict | None:
    """The appliance's network list from the executor; None when there is none to ask or it does not answer."""
    from plugins._ea_ansible.helpers import executor_client

    try:
        return executor_client.networks(agent)
    except executor_client.ExecutorError:
        return None


def matching_expiry(devices: list[dict], *, within_days: int, kind: str | None, today: _dt.date) -> list[dict]:
    """Expired or soon, soonest first, each device once. Decommissioned stay out (same rule as the report)."""
    found = reports.lifecycle(devices, today=today, within_days=within_days, kind=kind)
    by_id = {d["id"]: d for d in devices}
    return [by_id[i] for i in dict.fromkeys(item["device_id"] for item in found["items"]) if i in by_id]


def place_ids(text: str) -> list[str]:
    """Places whose ID or name is `text`, at any level, without any that sits under another match."""
    hits = [
        p["id"] for p in places.list_places()
        if p["id"] == text.upper() or places.name_key(p["name"]) == places.name_key(text)
    ]
    return [i for i in hits if not any(i.startswith(h + "-") for h in hits)]


class InventoryLookup(Tool):
    async def execute(self, **kwargs) -> Response:
        a = {k: str(v or "").strip() for k, v in (self.args or {}).items()}
        kind = a.get("expiring_kind", "").lower()
        if kind and kind not in reports.KINDS:
            return Response(message="expiring_kind must be warranty, support or eol.", break_loop=False)
        raw_days = a.get("expiring_within_days", "")
        if kind and not raw_days:
            return Response(message="expiring_kind needs expiring_within_days.", break_loop=False)
        days = None
        if raw_days:
            if not raw_days.isdigit():
                return Response(message="expiring_within_days must be a whole number of days.", break_loop=False)
            days = int(raw_days)
        filters = dict(
            q=a.get("query", ""), name=a.get("name", ""), vendor=a.get("vendor", ""),
            platform=a.get("platform", ""), status=a.get("status", ""), site_name=a.get("site", ""),
            **{key: a.get(key, "") for key in _FILTERS},
        )
        ids = place_ids(a["place"]) if a.get("place") else [""]
        if not ids:
            return Response(message=f"No place is named or numbered {a['place']!r}.", break_loop=False)
        if days is None:
            found, total = [], 0  # the matches never nest, so their totals add up
            for pid in ids:
                rows, n = store.search_devices(**filters, place=pid, limit=LIMIT)
                found, total = found + rows, total + n
            found = found[:LIMIT]
        else:
            # ponytail: date filter in Python over the matches, same scan as inventory_lifecycle; a SQL predicate if the table gets large
            found = matching_expiry(
                [d for pid in ids for d in store.list_devices(**filters, place=pid)],
                within_days=days, kind=kind or None, today=reports.today(),
            )
            total, found = len(found), found[:LIMIT]
        listing = appliance_networks(getattr(self, "agent", None)) if found else None
        devices = [compact(d, listing) for d in found]
        note = f" Showing the first {LIMIT}; narrow the query." if total > LIMIT else ""
        return Response(message=f"{total} device(s) match.{note}\n\n{json.dumps(devices, indent=1)}", break_loop=False)
