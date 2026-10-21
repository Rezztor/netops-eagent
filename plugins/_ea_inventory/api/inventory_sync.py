"""Push the active devices to the Ansible executor's inventory.

Gated on `inventory.approve`: it changes which devices jobs can reach.
"""

from __future__ import annotations

from typing import Any

from helpers import rbac
from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import executor_sync


class InventorySync(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_INVENTORY_APPROVE

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        return executor_sync.sync(dry_run=bool(input.get("dry_run")))
