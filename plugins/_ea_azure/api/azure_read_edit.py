"""Edit the query on a free-form log query's card, before anyone has approved it.

The requester changes the text; the same record stays `awaiting_approval` with a new hash and a fresh expiry, and the card
is rebuilt from it. Approving afterwards binds to the new hash, so nothing the approver did not see can run. The new text
goes through `logs.guard` exactly like a request, and only a free-form query can be edited: a named log read and every
Resource Manager read are fixed text. The workspace and the time range cannot be edited here (a new request does that).
Audited as `azure.read.edited` with both hashes. Gated on `tools.network` in `helpers/api_policy.py`; the requester only.
"""

from __future__ import annotations

import time
from typing import Any

from helpers import user_scope
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL, EXPIRED
from plugins._ea_azure.helpers import config, logs
from plugins._ea_azure.tools.azure_read import EXPIRY_SECONDS, KIND, _audit, read_card


class AzureReadEdit(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if not config.is_enabled():
            return {"ok": False, "error": "Azure is off, so a read cannot be edited."}
        actor = user_scope.get_current_user_id() or ""
        try:
            # The current user's own records only: someone else's change id is "not found".
            record = change_store.load(str(input.get("change_id") or "").strip())
        except change_store.ChangeNotFound as error:
            return {"ok": False, "error": str(error)}
        if record.kind != KIND or record.requested_by != actor:
            return {"ok": False, "error": "Only the person who requested a read can edit it."}
        if record.playbook_name != logs.FREE:
            return {"ok": False, "error": "Only a free-form log query can be edited."}
        if record.status != AWAITING_APPROVAL:
            return {"ok": False, "error": f"Read {record.id} is '{record.status}'; only a read awaiting approval can be edited."}
        if record.expired():
            record.status = EXPIRED
            change_store.save(record)
            return {"ok": False, "error": f"Read {record.id} expired. Ask for a fresh request."}
        if str(input.get("expected_hash") or "") != record.current_hash:
            return {"ok": False, "error": "What you saw is no longer what would run. Ask for a fresh request."}

        listed = input.get("commands", input.get("kql"))
        text = "\n".join(str(c) for c in listed) if isinstance(listed, list) else str(listed or "")
        final, refusal = logs.guard(text)
        if refusal:
            return {"ok": False, "error": refusal}

        before = record.current_hash
        record.commands = final.splitlines()
        record.expires_at = time.time() + EXPIRY_SECONDS
        change_store.save(record)
        _audit("azure.read.edited", record, hash_before=before, hash_after=record.current_hash)
        return {"ok": True, "card": read_card(record, logs.FREE_ENTRY, editable=True)}
