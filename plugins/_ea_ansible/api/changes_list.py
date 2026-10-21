"""A user's own change register.

Own changes only. Seeing everyone's is a different endpoint with a different
permission (`changes_review`), because one endpoint that widens its own scope
based on the caller's role is one whose behaviour cannot be read off its
signature.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import change_store


class ChangesList(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        change_id = str(input.get("change_id") or "").strip()
        if change_id:
            try:
                record = change_store.load(change_id)
            except change_store.ChangeNotFound as error:
                return {"ok": False, "error": str(error)}
            return {"ok": True, "change": record.to_dict()}

        # Changes by default: reads are a card in the chat, not a register entry.
        kind = str(input.get("kind") or "change").strip().lower()
        records = [r for r in change_store.list_records() if kind == "all" or r.kind == kind]
        return {"ok": True, "changes": [change_store.summarise(r) for r in records]}
