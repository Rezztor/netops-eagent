"""Recent audit events, for the administration interface.

**This endpoint does not serve the audit trail.** The audit trail is the JSON
line stream on stdout, shipped off-box to the SIEM, and its value comes from
living somewhere this application cannot rewrite (ADR 0005). What this returns is
a capped in-memory window of the most recent events, lost on restart.

It exists so an administrator can answer "what just happened" without opening a
log aggregator. Anything that needs to be complete, retained, or trusted has to
come from the SIEM instead, and the interface says so on the page.

Requires `audit.view_all`: these events name other users and what they ran.
"""

from typing import Any

from helpers import rbac
from helpers.api import ApiHandler, Input, Output, Request
from plugins._audit.helpers import audit

MAX_LIMIT = 200


class AuditRecent(ApiHandler):
    """A capped, in-memory tail of recent audit events. Not the audit record."""

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_AUDIT_VIEW_ALL

    async def process(self, input: Input, request: Request) -> Output:
        try:
            limit = int(input.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, MAX_LIMIT))

        events: list[dict[str, Any]] = audit.recent_events(limit)
        return {
            "ok": True,
            "events": events,
            "limit": limit,
            "buffered": len(events),
            "capacity": audit.RECENT_EVENT_LIMIT,
            # Stated in the payload, not only in the interface, so any consumer
            # sees it -- including one written later by someone who did not read
            # this docstring.
            "is_complete_record": False,
            "note": (
                "In-memory tail, capped and cleared on restart. The retained audit "
                "record is the stdout event stream shipped to the SIEM."
            ),
        }
