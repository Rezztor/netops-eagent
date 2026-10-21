"""Devices, filtered, with the values the filters offer. Gated on `inventory.view`
in `helpers/api_policy.py`."""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import backups, reports, store


class InventoryList(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        s = lambda key: str(input.get(key) or "").strip()  # noqa: E731
        devices, total = store.search_devices(
            q=s("q"), place=s("place"), vendor=s("vendor"), platform=s("platform"),
            status=s("status"), tag=s("tag"), stale=input.get("stale") is True,
            limit=int(input.get("limit") or 100), offset=int(input.get("offset") or 0),
            **{key: s(key) for key in ("device_type", "role", "department")},
        )
        reports.attach(devices, await asyncio.to_thread(backups.list_all) if devices else None)
        return {"ok": True, "devices": devices, "total": total, "facets": store.facets()}
