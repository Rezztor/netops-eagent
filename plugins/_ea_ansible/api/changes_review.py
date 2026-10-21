"""Everything waiting for an approver, across every user.

Gated on `network.approve`. Returns the full plan for a single change rather
than a summary, because approving means reading the commands -- a register entry
saying "12 commands" is not something anyone can approve.
"""

from __future__ import annotations

from typing import Any

from helpers import rbac, user_scope
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL


class ChangesReview(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_NETWORK_APPROVE

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        change_id = str(input.get("change_id") or "").strip()
        if change_id:
            try:
                record, _owner = change_store.find_across_users(change_id)
            except change_store.ChangeNotFound as error:
                return {"ok": False, "error": str(error)}
            return {"ok": True, "change": record.to_dict()}

        pending: list[dict[str, Any]] = []
        for user_id in user_scope.list_user_ids():
            for record in change_store.list_records(user_id):
                if record.status == AWAITING_APPROVAL:
                    pending.append(change_store.summarise(record))
        pending.sort(key=lambda item: item["created_at"], reverse=True)
        return {"ok": True, "changes": pending}
