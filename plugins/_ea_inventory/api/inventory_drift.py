"""Config that changed, and whether an approved change explains it. Gated on `inventory.view`. Read only.

Built from what is already stored: the executor's backup index (`backups.list_all`, the call the backup report makes)
and the change ledger (every user's device change, rollback and plan records that were approved and applied). No job
runs. A row names the two backups so the page can open the executor's redacted diff, which needs `inventory.manage`.
Each matching change gives its id, what it was, when it was applied and who approved it; never its commands.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any

from helpers import user_scope, users
from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers import backups, store

#: The records that change a device: `net_apply` (a change or its rollback) and `net_plan`.
_KINDS = ("change", "rollback", "plan")


def _targets(record: Any) -> set[str]:
    limits = [s.get("limit", "") for s in record.steps if s.get("kind") == "change"] if record.kind == "plan" else [record.limit]
    return {name.strip() for limit in limits for name in str(limit or "").split(",") if name.strip()}


def applied_changes() -> list[dict[str, Any]]:
    """Every approved device change that was applied (or attempted), across users."""
    from plugins._ea_ansible.helpers import change_store

    names = {u["id"]: u["display_name"] for u in users.list_users()}
    out = []
    for owner in user_scope.list_user_ids():
        for r in change_store.list_records(owner):
            if r.kind in _KINDS and r.approved_by and r.applied_at:
                out.append({
                    "id": r.id, "summary": r.summary or r.playbook_name, "status": r.status,
                    "approved_by": names.get(r.approved_by, r.approved_by), "devices": _targets(r),
                    "applied_at": _dt.datetime.fromtimestamp(r.applied_at, _dt.timezone.utc),
                })
    return out


class InventoryDrift(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        found = await asyncio.to_thread(backups.list_all)
        if found is None:
            return {"ok": False, "error": backups.unknown_reason()}
        items = backups.drift(store.list_devices(status="active"), found, await asyncio.to_thread(applied_changes))
        return {"ok": True, "changed": len(items), "unexplained": sum(not i["explained"] for i in items), "items": items}
