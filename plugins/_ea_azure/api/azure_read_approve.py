"""Approve or reject an Azure read: the card's decision, which the agent can never make.

The same rules as `change_approve` for `kind="azure_read"` (ADR 0039: the engineer who asked may approve and is
accountable; the audit line carries both ids), and no change reference: a read changes nothing in Azure. The hash on the
card must still equal the record's, so what is approved is the exact request that was shown. Gated on `network.approve`.
Refused while the plugin is off. It refuses every other kind of record: Azure changes have their own endpoint.
"""

from __future__ import annotations

from typing import Any

from helpers import rbac, user_scope
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL, EXPIRED, REJECTED
from plugins._ea_azure.helpers import config
from plugins._ea_azure.tools.azure_read import KIND


class AzureReadApprove(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_NETWORK_APPROVE

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if not config.is_enabled():
            return {"ok": False, "error": "Azure is off, so a read cannot be approved."}
        change_id = str(input.get("change_id") or "").strip()
        decision = str(input.get("decision") or "").strip().lower()
        if decision not in {"approve", "reject"}:
            return {"ok": False, "error": "decision must be 'approve' or 'reject'"}
        try:
            record, owner = change_store.find_across_users(change_id)
        except change_store.ChangeNotFound as error:
            return {"ok": False, "error": str(error)}
        if record.kind != KIND:
            return {"ok": False, "error": f"{record.id} is not an Azure read."}
        if record.status != AWAITING_APPROVAL:
            return {"ok": False, "error": f"Read {record.id} is '{record.status}', not awaiting approval."}
        if record.expired():
            record.status = EXPIRED
            change_store.save(record, owner)
            return {"ok": False, "error": f"Read {record.id} expired before it was answered. Ask for a fresh request."}
        if decision == "approve" and str(input.get("expected_hash") or "").strip() != record.current_hash:
            return {"ok": False, "error": "What you saw is no longer what would be sent. Ask for a fresh request."}

        if decision == "reject":
            record.status = REJECTED
            change_store.save(record, owner)
        elif not change_store.approve(record, user_scope.get_current_user_id() or "", owner):
            return {"ok": False, "error": f"Read {record.id} is no longer awaiting approval, so it was not approved."}
        return {"ok": True, "change": {"id": record.id, "status": record.status, "requested_by": record.requested_by,
                                       "approved_by": record.approved_by, "approved_hash": record.approved_hash}}
