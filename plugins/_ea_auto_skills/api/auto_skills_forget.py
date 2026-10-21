"""Removing a skill the agent wrote for you.

The counterpart to automatic capture. A skill induced from someone's own work is
applied without asking, listed in their system prompt on every turn, and read by
nobody else -- so removing it is theirs to do, not an administrator's.

That is why this is a separate endpoint rather than a relaxation of api/skills.py.
That handler carries action=delete alongside action=list and is gated on
plugins.manage because it can reach usr/skills, where a deletion changes what
every agent on the instance does. It cannot tell a shared skill from a private
one. This handler can, because it can only ever reach one directory.

The owner comes from the session, never from the request. There is no user id in
the payload to forget to check: the endpoint is structurally incapable of being
pointed at somebody else's skills.
"""

from __future__ import annotations

from typing import Any

from helpers import user_scope
from helpers.api import ApiHandler, Request, Response
from plugins._ea_auto_skills.helpers import config, store


class AutoSkillsForget(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if not config.is_enabled():
            return {"ok": False, "error": "Auto Skills is disabled"}

        action = str(input.get("action") or "forget").strip().lower()
        if action != "forget":
            return {"ok": False, "error": f"Unknown action: {action}"}

        user_id = user_scope.get_current_user_id()
        if not user_scope.is_safe_user_id(user_id):
            return {"ok": False, "error": "No signed-in user"}

        name = str(input.get("name") or "").strip().lower()
        if not name:
            return {"ok": False, "error": "name is required"}

        try:
            # store.forget refuses anything absent from this user's ledger, which
            # covers both a skill they wrote themselves and a name that is not a
            # plain skill name at all.
            if not store.forget(user_id, name):
                return {
                    "ok": False,
                    "error": f"{name} is not a skill the agent created for you",
                }
            return {"ok": True, "message": f"Removed {name}"}
        except Exception as exception:
            return {"ok": False, "error": str(exception)}
