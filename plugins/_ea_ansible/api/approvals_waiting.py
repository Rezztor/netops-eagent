"""The Approvals page: every change waiting for a signature that the caller may give, oldest first.

Gated on `network.approve`. Across users for a device change or plan (`change_approve` lets any holder decide one); the
caller's own for an Azure change (`azure_approve` lets only the requester decide). Expired items are left out: they can
no longer be approved. A row is a summary with the tier, the typed confirmation a change-high card asks for, the
expiry and the requester's name; the commands are read from `changes_review`, and the decision goes to the endpoint
that owns it. `context_id` is given for the caller's own records only, so the page can open the card in their chat.
"""

from __future__ import annotations

from typing import Any

from helpers import rbac, user_scope, users
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import approvers, change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL


class ApprovalsWaiting(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_NETWORK_APPROVE

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        viewer_id = user_scope.get_current_user_id() or ""
        everyone = {user["id"]: user for user in users.list_users()}
        viewer = everyone.get(viewer_id)
        waiting: list[dict[str, Any]] = []
        for owner in user_scope.list_user_ids():
            for record in change_store.list_records(owner):
                if (
                    record.status == AWAITING_APPROVAL
                    and not record.expired()
                    and approvers.wants_signature(record)
                    and approvers.may_decide(record, owner, viewer)
                ):
                    requester = everyone.get(owner) or {}
                    waiting.append({
                        **change_store.summarise(record),
                        "tier": record.tier,
                        "confirm": record.confirm,
                        "azure": record.kind in approvers.AZURE_KINDS,
                        "requested_by_name": requester.get("display_name") or requester.get("username") or owner,
                        "context_id": record.context_id if owner == viewer_id else "",
                    })
        waiting.sort(key=lambda item: item["created_at"])
        return {"ok": True, "changes": waiting}
