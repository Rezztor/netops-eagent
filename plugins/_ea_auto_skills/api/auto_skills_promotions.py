"""Administrator review of skills the agent induced from users' work.

Gated on plugins.manage in helpers/api_policy.py, by full dispatch path. That
is not a formality: promoting a proposal writes into usr/skills, which is read
into every account's system prompt, so this endpoint decides what instructions
other people's agents will follow.

Handler resolution finds a plugin's api/ directory whether or not the plugin is
enabled, so the toggle is checked here rather than assumed.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response
from plugins._ea_auto_skills.helpers import config, promote


class AutoSkillsPromotions(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if not config.is_enabled():
            return {"ok": False, "error": "Auto Skills is disabled"}

        action = str(input.get("action") or "list").strip().lower()
        promotion_id = str(input.get("promotion_id") or "").strip()

        try:
            if action == "list":
                return {"ok": True, "promotions": promote.list_all()}

            if action == "get":
                proposal = promote.get(promotion_id)
                if not proposal:
                    return {"ok": False, "error": "Proposal not found"}
                return {"ok": True, "promotion": proposal}

            if action == "promote":
                ok, message = promote.promote(promotion_id)
                return {"ok": ok, "message": message} if ok else {"ok": False, "error": message}

            if action == "reject":
                ok, message = promote.reject(promotion_id, str(input.get("reason") or ""))
                return {"ok": ok, "message": message} if ok else {"ok": False, "error": message}

            if action == "discard":
                if not promote.discard(promotion_id):
                    return {"ok": False, "error": "Only a resolved proposal can be discarded"}
                return {"ok": True, "message": "Discarded"}

            return {"ok": False, "error": f"Unknown action: {action}"}
        except Exception as exception:
            return {"ok": False, "error": str(exception)}
