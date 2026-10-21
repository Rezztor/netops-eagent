"""Approve or reject a proposal. Gated on `inventory.approve`.

Approval is not a tool: the agent proposes, a person decides. The requesting
engineer may approve (ADR 0039); the audit line carries both names. After an
approval commits, the executor inventory is synced best effort -- a sync failure
is reported in `sync` and never undoes the approval.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import proposals
from plugins._ea_inventory.helpers.normalise import InventoryError


def _sync() -> dict[str, Any]:
    try:
        # Imported here so a missing or broken module cannot stop an approval.
        from plugins._ea_inventory.helpers import executor_sync

        return executor_sync.sync_after_commit()
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error)}


class InventoryDecide(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        decision = str(input.get("decision") or "")
        try:
            proposal, device = proposals.decide(
                str(input.get("id") or ""), decision, input.get("note") or "",
                input.get("edits"), actor=proposals.current_actor(),
            )
        except InventoryError as error:
            return {"ok": False, "error": str(error)}
        out: dict[str, Any] = {"ok": True, "proposal": proposal}
        if device:
            out["device"] = device
            out["sync"] = _sync()
        return out
