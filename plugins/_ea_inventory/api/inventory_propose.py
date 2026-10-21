"""Ask for a change to the inventory. Gated on `inventory.manage`; nothing changes
until `inventory_decide` approves it."""

from __future__ import annotations

import json
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import proposals
from plugins._ea_inventory.helpers.normalise import InventoryError


MAX_CLAIM_CHARS = 4000


class InventoryPropose(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        actor = proposals.current_actor()
        claim = input.get("evidence")
        # A person in the screen is always `manual`; the other sources are set by the code that runs them. What the
        # caller says about it is kept under `claimed`, apart from the keys Review reads as quote, location,
        # confidence or batch, so it is never shown as observed or verified.
        if claim is not None and (not isinstance(claim, dict) or len(json.dumps(claim, default=str)) > MAX_CLAIM_CHARS):
            return {"ok": False, "error": f"evidence must be an object under {MAX_CLAIM_CHARS} characters."}
        try:
            proposal = proposals.propose(
                str(input.get("kind") or ""),
                str(input.get("device_id") or "") or None,
                input.get("fields"),
                source="manual",
                evidence={"claimed": claim, "claimed_by": actor} if claim else None,
                actor=actor,
            )
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, "proposal": proposal}
