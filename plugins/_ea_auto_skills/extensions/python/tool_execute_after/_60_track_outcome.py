"""Record what the current turn did, so process_chain_end can judge it.

Joined to the turn by the sequence number of the message that started it, the
same way _audit joins a tool's end to its start. Swallows its own exceptions:
failing to note a statistic must never break the tool call it observed.
"""

from helpers.extension import Extension
from helpers.tool import Response
from plugins._ea_auto_skills.helpers import capture


class TrackTurnOutcome(Extension):
    async def execute(self, response: Response | None = None, tool_name: str = "", **kwargs):
        if not self.agent:
            return
        try:
            capture.record_tool_outcome(self.agent, response)
        except Exception:
            pass
