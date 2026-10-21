"""Approve or reject an Azure change: the card's decision, which the agent can never make.

Gated on `network.approve`, and only the engineer who requested the change may decide it (ADR 0039: they are accountable;
the audit line carries both ids). Approve refuses, in this order: not awaiting approval; expired; wrong hash; a CR or SR
number that is missing or does not fully match the pattern as it stands now; no issued change document for this plan
digest, or one that was never downloaded or no longer matches its SHA-256. On success the number is stored on the record
(upper-cased) and in the audit. Reject needs neither the hash, the number nor the document. Only `azure_change` and
`azure_action` records: a suggestion authorises nothing, and a read has its own endpoint.
"""

from __future__ import annotations

from typing import Any

from helpers import rbac, user_scope
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL, EXPIRED, REJECTED
from plugins._ea_azure_change.helpers import adopt, drift, records


class AzureApprove(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_NETWORK_APPROVE

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if not records.enabled():
            return {"ok": False, "error": "Azure changes are switched off, so nothing can be approved."}
        decision = str(input.get("decision") or "").strip().lower()
        if decision not in {"approve", "reject"}:
            return {"ok": False, "error": "decision must be 'approve' or 'reject'"}
        try:
            record, owner = change_store.find_across_users(str(input.get("change_id") or "").strip())
        except change_store.ChangeNotFound as error:
            return {"ok": False, "error": str(error)}
        actor = user_scope.get_current_user_id() or ""
        if record.kind not in records.KINDS:
            return {"ok": False, "error": f"{record.id} is not an Azure change, so it cannot be approved here."}
        if not actor or owner != actor:
            return {"ok": False, "error": "Only the engineer who requested this change can approve or reject it."}
        if record.status != AWAITING_APPROVAL:
            return {"ok": False, "error": f"Change {record.id} is '{record.status}', not awaiting approval."}
        if record.expired():
            record.status = EXPIRED
            change_store.save(record, owner)
            return {"ok": False, "error": f"Change {record.id} expired with its document. Ask for a fresh draft."}

        if drift.is_accept(record):  # state only: no CR or SR number and no document, because nothing in Azure changes
            return await drift.decide(record, owner, decision, str(input.get("expected_hash") or "").strip())
        if adopt.is_adopt(record):  # state only, the same: importing changes nothing in Azure
            return await adopt.decide(record, owner, decision, str(input.get("expected_hash") or "").strip())
        if decision == "reject":
            record.status = REJECTED
            change_store.save(record, owner)
        else:
            if str(input.get("expected_hash") or "").strip() != record.current_hash:
                return {"ok": False, "error": "What you saw is no longer what would run. Ask for a fresh draft."}
            problem = records.reference_problem(input.get("reference")) or records.issued_document_problem(record)
            if problem:
                return {"ok": False, "error": problem}
            if not change_store.approve(record, actor, owner, reference=records.normalise_reference(input.get("reference"))):
                return {"ok": False, "error": f"Change {record.id} is no longer awaiting approval, so it was not approved."}
        return {"ok": True, "change": {"id": record.id, "status": record.status, "requested_by": record.requested_by,
                                       "approved_by": record.approved_by, "approved_hash": record.approved_hash,
                                       "reference": record.reference}}
