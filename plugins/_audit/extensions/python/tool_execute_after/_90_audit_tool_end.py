import time

from helpers.extension import Extension
from helpers.tool import Response
from plugins._audit.helpers import audit
from plugins._audit.extensions.python.tool_execute_before._90_audit_tool_start import (
    PENDING_KEY,
)


class AuditToolEnd(Extension):
    """Record how a tool call ended, joined to its start by the parked call."""

    async def execute(self, response: Response | None = None, tool_name: str = "", **kwargs):
        if not self.agent:
            return
        try:
            call = self.agent.get_data(PENDING_KEY) or {}
            self.agent.set_data(PENDING_KEY, None)
            # Set by tools/refused.py: the name on this record is the tool that was asked for, which did not run.
            refused = bool(self.agent.get_data("_tool_refused"))
            self.agent.set_data("_tool_refused", None)

            duration_ms = None
            if call.get("started"):
                duration_ms = round((time.monotonic() - call["started"]) * 1000)

            context = getattr(self.agent, "context", None)
            message = getattr(response, "message", "") or ""

            audit.emit(
                "tool.end",
                tool=tool_name or call.get("tool"),
                context_id=call.get("context_id") or getattr(context, "id", None),
                agent=call.get("agent"),
                # Carried from the start event so a single line is enough to see
                # what ran and what came back.
                args_digest=call.get("args_digest"),
                duration_ms=duration_ms,
                outcome="refused" if refused else "ok" if response is not None else "no_response",
                result_digest=audit.digest(message),
                result_preview=audit.preview(message, context),
                ends_loop=bool(getattr(response, "break_loop", False)),
            )
        except Exception:
            pass
