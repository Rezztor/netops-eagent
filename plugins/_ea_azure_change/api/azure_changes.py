"""The Azure change register, for the Changes screen: newest first, with the CR or SR number, the plan digest and what
documents exist. Gated on `inventory.view`.

A user sees their own records. `{"scope": "all"}` lists every user's and needs `network.approve`: widening is asked for
in the request and checked here, never inferred from the role. `{"change_id": "..."}` answers `{"change": row}` for one of the user's own records (what a chat card asks, so it shows the
change's real state after a reload or on another browser); the row then also names who approved (`approved_by_name`). A change
set's row carries `steps: [{n, title, status}]`.
`{"reference": "CR-12345"}` keeps the records approved
under that number (case-insensitive, exact). No plan, document or key leaves here, only the facts a list needs. `enabled` says whether the plugin is on: a plugin that is
off still answers, and the Changes tab draws nothing for an off plugin with no records.
"""

from __future__ import annotations

from typing import Any

from helpers import login, rbac, user_scope
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import change_store
from plugins._ea_azure_change.helpers import records, sets

LIMIT = 200


def row(record: Any) -> dict[str, Any]:
    return {
        **change_store.summarise(record),
        "plan_digest": record.plan_digest,
        "tier": record.tier,
        "approved_at": record.approved_at,
        "applied_at": record.applied_at,
        "verified": record.verified,
        "downloaded": any(d.get("downloaded_at") for d in record.documents if d.get("version") == "issued"),
        **({"steps": [{"n": s["n"], "title": s["title"], "status": s["status"]} for s in record.steps]} if sets.is_set(record) else {}),
        "documents": [{"version": d.get("version"), "format": d.get("format"), "sha256": d.get("sha256"),
                       "created_at": d.get("created_at"), "downloaded": bool(d.get("downloaded_at"))} for d in record.documents],
    }


class AzureChanges(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if str(input.get("change_id") or "").strip():
            try:
                record = change_store.load(str(input["change_id"]).strip())
            except change_store.ChangeNotFound as error:
                return {"ok": False, "error": str(error)}
            if record.kind not in (*records.KINDS, "azure_suggestion"):
                return {"ok": False, "error": f"{record.id} is not an Azure change."}
            name = login.current_username() if record.approved_by == user_scope.get_current_user_id() else record.approved_by
            return {"ok": True, "enabled": records.enabled(), "change": {**row(record), "approved_by_name": name or ""}}
        if str(input.get("scope") or "mine") == "all":
            if not rbac.has_permission(login.current_role(), rbac.PERM_NETWORK_APPROVE):
                return {"ok": False, "error": "Seeing everyone's changes needs the approve permission."}
            found = [r for uid in user_scope.list_user_ids() for r in change_store.list_records(uid)]
        else:
            found = change_store.list_records()
        wanted = records.normalise_reference(input.get("reference"))
        kinds = (*records.KINDS, "azure_suggestion") if str(input.get("kind") or "") == "all" else records.KINDS
        found = [r for r in found if r.kind in kinds and (not wanted or r.reference.upper() == wanted)]
        found.sort(key=lambda r: r.created_at, reverse=True)
        return {"ok": True, "enabled": records.enabled(), "changes": [row(r) for r in found[:LIMIT]], "truncated": len(found) > LIMIT}
