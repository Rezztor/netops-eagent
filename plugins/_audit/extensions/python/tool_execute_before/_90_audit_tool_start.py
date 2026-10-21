import time

from helpers.extension import Extension
from plugins._audit.helpers import audit

# Where the pending call is parked on the agent between the before and after
# hooks. tool_execute_after does not receive tool_args, so without this the
# record of what was executed cannot be joined to its outcome.
PENDING_KEY = "_audit_pending_tool_call"


class AuditToolStart(Extension):
    """Record that a tool was invoked, and with what."""

    async def execute(self, tool_args: dict | None = None, tool_name: str = "", **kwargs):
        if not self.agent:
            return
        try:
            context = getattr(self.agent, "context", None)
            call = {
                "started": time.monotonic(),
                "tool": tool_name,
                "context_id": getattr(context, "id", None),
                "agent": getattr(self.agent, "agent_name", None),
                "args_digest": audit.digest(tool_args or {}),
                "args_preview": audit.preview(tool_args or {}, context),
            }
            self.agent.set_data(PENDING_KEY, call)

            audit.emit(
                "tool.start",
                tool=call["tool"],
                context_id=call["context_id"],
                agent=call["agent"],
                args_digest=call["args_digest"],
                args_preview=call["args_preview"],
            )
        except Exception:
            pass
