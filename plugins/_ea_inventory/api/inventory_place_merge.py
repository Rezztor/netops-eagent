"""Fold one place and everything under it into another place of the same kind. Needs `inventory.places`.
A dry run changes nothing and says what would be created and moved; an apply creates the missing places,
stages one batch of `place_id` proposals for Review (a device is never written here, ADR 0040) and marks
the old place inactive."""

from __future__ import annotations

from typing import Any

from helpers import rbac
from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import places, proposals
from plugins._ea_inventory.helpers.normalise import InventoryError


class InventoryPlaceMerge(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_INVENTORY_PLACES

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        try:
            return {"ok": True, **places.merge(
                input.get("from_id"), input.get("into_id"), dry_run=input.get("dry_run", True) is not False,
                actor=proposals.current_actor())}
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
