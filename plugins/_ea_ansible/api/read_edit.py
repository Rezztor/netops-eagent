"""Edit the commands on a free-form read's card, before anyone has approved it.

The requester changes the text; the same record stays `awaiting_approval` with a
new hash and a fresh expiry, and the card is rebuilt from it. Approving afterwards
binds to the new hash, so nothing the approver did not see can run. The commands go
through the same catalogue pattern as a request (ADR 0046); a fixed read cannot be
edited. Audited as `network.read.edited` with both hashes (ADR 0048).

Gated on `network.approve`, like the card it belongs to.
"""

from __future__ import annotations

import time
from typing import Any

from helpers import rbac, user_scope
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import bundles, catalogue, change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL, EXPIRED
from plugins._ea_ansible.tools.net_read import EXPIRY_SECONDS, SHOW_RULE, read_card

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]


class ReadEdit(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_NETWORK_APPROVE

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        actor = user_scope.get_current_user_id() or ""
        try:
            # The current user's own records only: someone else's change id is "not found".
            record = change_store.load(str(input.get("change_id") or "").strip())
        except change_store.ChangeNotFound as error:
            return {"ok": False, "error": str(error)}
        if record.kind != "read" or record.requested_by != actor:
            return {"ok": False, "error": "Only the person who requested a read can edit it."}
        if record.status != AWAITING_APPROVAL:
            return {"ok": False, "error": f"Read {record.id} is '{record.status}'; only a read awaiting approval can be edited."}
        if record.expired():
            record.status = EXPIRED
            change_store.save(record)
            return {"ok": False, "error": f"Read {record.id} expired. Ask for a fresh request."}
        if str(input.get("expected_hash") or "") != record.current_hash:
            return {"ok": False, "error": "What you saw is no longer what would run. Ask for a fresh request."}

        entry = catalogue.resolve(record.playbook_name)
        if "command_1" not in getattr(entry, "variables", {}):
            return {"ok": False, "error": "Only a free-form read can be edited."}
        listed = input.get("commands")
        listed = listed.splitlines() if isinstance(listed, str) else list(listed or [])
        try:
            variables = entry.validate_variables(
                {f"command_{i}": c.strip() for i, c in enumerate([c for c in listed if isinstance(c, str) and c.strip()], 1)}
            )
        except catalogue.VariableRejected as error:
            return {"ok": False, "error": f"{error}\n\n{SHOW_RULE}"}
        commands, refusal = bundles.read_lines(entry, record.devices_resolved, variables)
        if refusal:
            return {"ok": False, "error": refusal}

        before = record.current_hash
        record.variables, record.commands = variables, commands
        record.expires_at = time.time() + EXPIRY_SECONDS
        change_store.save(record)
        if audit is not None:
            audit.emit(
                "network.read.edited",
                change_id=record.id,
                requested_by=actor,
                limit=record.limit,
                hash_before=before,
                hash_after=record.current_hash,
            )
        return {"ok": True, "card": read_card(record, editable=True)}
