"""Edit the values on an Azure change card, before anyone has approved it.

The requester changes the template values (`variables`, or the card's `name = value` lines in `commands`), or the `code` of
a free-code change; the executor plans them again. A different plan makes a **new record with a new digest and a new change document**; the old record
becomes `superseded` and its document is void, so an approval can never carry over to what the engineer did not read.
The same plan (the edit changed nothing that matters) returns the same card. The values go through the template's own
types in the executor and the plan through the same checks as a draft: an edit is a draft, not a way round one. Audited
as `azure.change.edited` with both hashes. Gated on `tools.network`; the requester's own records only. A change set is never
edited (its card has no Edit): the agent drafts it again with `replaces`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from helpers import rbac, user_scope
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL, EXPIRED
from plugins._ea_azure_change.helpers import card, drift, executor_client, records, sets

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]


class AzureEdit(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_TOOL_NETWORK

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if not records.enabled():
            return {"ok": False, "error": "Azure changes are switched off."}
        actor = user_scope.get_current_user_id() or ""
        try:  # the current user's own records only: someone else's change id is "not found"
            record = change_store.load(str(input.get("change_id") or "").strip())
        except change_store.ChangeNotFound as error:
            return {"ok": False, "error": str(error)}
        if record.kind != "azure_change" or record.requested_by != actor or drift.is_accept(record):  # an acceptance has no values to edit
            return {"ok": False, "error": "Only the person who requested a template change can edit it."}
        if sets.is_set(record):
            return {"ok": False, "error": "A change set has no Edit: ask the assistant to draft it again."}
        if record.status != AWAITING_APPROVAL:
            return {"ok": False, "error": f"Change {record.id} is '{record.status}'; only a change awaiting approval can be edited."}
        if record.expired():
            record.status = EXPIRED
            change_store.save(record)
            return {"ok": False, "error": f"Change {record.id} expired. Ask for a fresh draft."}
        free = record.playbook_name == "free"
        if free != (input.get("code") is not None):
            return {"ok": False, "error": "A free-code change is edited with `code`; a template change with `variables`."}
        code = input.get("code") if free else None
        if free and (problem := records.free_code_problem(code)):
            return {"ok": False, "error": problem}
        try:
            variables = {} if free else input["variables"] if isinstance(input.get("variables"), dict) else records.lines_to_variables(input.get("commands"))
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        if not variables and not free:
            return {"ok": False, "error": "Nothing to plan: send `variables`, or the card's `name = value` lines."}
        scope = records.workspace_of(record.limit)
        purpose = str(record.preflight.get("purpose") or record.summary)
        new_id = records.new_id()
        try:
            answer, commit = await asyncio.to_thread(records.ask_plan, None, template=record.playbook_name, variables=variables,
                                                    subscription=scope["subscription"], resource_group=scope["resource_group"],
                                                    change_id=new_id, requester=actor, code=code)
            if str(answer.get("plan_digest") or "") == record.plan_digest:
                return {"ok": True, "unchanged": True, "card": card.change_card(record)}
            fresh = records.build_record(None, answer, commit, change_id=new_id, requester=actor, template=record.playbook_name,
                                         variables=variables, subscription=scope["subscription"], resource_group=scope["resource_group"],
                                         purpose=purpose, code=code)
        except (records.PlanRefused, executor_client.ExecutorError) as error:
            return {"ok": False, "error": str(error)}
        records.store_new(fresh, replaces=record)
        if audit is not None:
            audit.emit("azure.change.edited", change_id=fresh.id, replaces=record.id, requested_by=actor, hash_before=record.current_hash,
                       hash_after=fresh.current_hash, plan_digest=fresh.plan_digest, previous_plan_digest=record.plan_digest)
        return {"ok": True, "change_id": fresh.id, "card": card.change_card(fresh)}
