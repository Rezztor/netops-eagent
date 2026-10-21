"""Approve or reject the commands a change would push.

This is the second of the three conditions, and it is deliberately not a tool.
The agent drafts; a person approves. There is no path by which the agent reaches
this endpoint. The engineer who requested a change may approve it and is
accountable for it (ADR 0039); the audit line carries both ids.

Gated on `network.approve`, which the administrator and power (network
engineer) roles hold.
"""

from __future__ import annotations

from typing import Any

from helpers import rbac, user_scope
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import (
    AWAITING_APPROVAL,
    EXPIRED,
    REJECTED,
)

#: What `net_read`, `net_change`, `net_apply` and `net_plan` record.
DEVICE_KINDS = ("read", "change", "rollback", "plan")


class ChangeApprove(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_NETWORK_APPROVE

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        change_id = str(input.get("change_id") or "").strip()
        decision = str(input.get("decision") or "").strip().lower()
        if decision not in {"approve", "reject"}:
            return {"ok": False, "error": "decision must be 'approve' or 'reject'"}

        try:
            record, owner = change_store.find_across_users(change_id)
        except change_store.ChangeNotFound as error:
            return {"ok": False, "error": str(error)}

        # An Azure record is decided by its own endpoint, which also demands the requester, a CR or SR number and
        # the change document. Approving it here would mark it approved with none of those.
        if record.kind not in DEVICE_KINDS:
            return {"ok": False, "error": f"{record.id} is not a device read or change, so it cannot be decided here."}

        if record.status != AWAITING_APPROVAL:
            return {
                "ok": False,
                "error": f"Change {record.id} is '{record.status}', not awaiting approval.",
            }

        if record.expired():
            record.status = EXPIRED
            change_store.save(record, owner)
            return {"ok": False, "error": f"Change {record.id} expired before it was answered. Ask for a fresh request."}

        # What the card showed must be what would run. Required for a read, a
        # rollback and a change that carries a tier (every one drafted since
        # ADR 0050); an older change without one keeps working, but one that
        # sends the hash is held to it. Rejecting needs no hash: declining is
        # always safe.
        expected = str(input.get("expected_hash") or "").strip()
        needs_hash = record.kind != "change" or bool(record.tier)
        if decision == "approve" and (expected or needs_hash) and expected != record.current_hash:
            return {
                "ok": False,
                "error": "What you saw is no longer what would run. Ask for a fresh request.",
            }
        # A change-high card makes the approver type what they are applying to
        # (the device name, or `apply to N devices`). The card disables its button
        # until they do, but the card is only a convenience: this is the check.
        if decision == "approve" and record.confirm and str(input.get("confirmation") or "").strip() != record.confirm:
            return {
                "ok": False,
                "error": f"This change needs confirming: type `{record.confirm}` on the card to approve it.",
            }

        approver = user_scope.get_current_user_id() or ""

        # The approver is agreeing to a specific text. Binding the hash here is
        # what lets the gate tell "approved" from "still what was approved". The approval goes to the register
        # (`change_store.approve`), which is what the gate reads: the file is not believed.
        if decision == "reject":
            record.status = REJECTED
            change_store.save(record, owner)
        elif not change_store.approve(record, approver, owner):
            return {"ok": False, "error": f"Change {record.id} is no longer awaiting approval, so it was not approved."}
        return {
            "ok": True,
            "change": {
                "id": record.id,
                "status": record.status,
                "requested_by": record.requested_by,
                "approved_by": record.approved_by,
                "approved_hash": record.approved_hash,
            },
        }
