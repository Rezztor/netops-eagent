"""The scheduled refresh: its interval, the last run and when the next is due. Gated on `inventory.view`."""

from __future__ import annotations

from typing import Any

from helpers import rbac
from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import schedule


class InventorySchedule(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_INVENTORY_VIEW

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        return {"ok": True, **schedule.status()}
