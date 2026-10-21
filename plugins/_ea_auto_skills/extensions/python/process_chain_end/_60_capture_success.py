"""Start a skill review when a user finishes a substantial task.

Guarded three ways, because this hook is less precise than it looks:

  - it fires once per level of the superior chain, so only agent 0 counts;
  - it does not fire at all when the monologue raised, so completion is read
    from the flag the tool_execute_after extension recorded, never assumed;
  - it can fire again for a turn already handled, so the turn is stamped.

The review itself is a model call. It is detached and never awaited, so a slow
or failing reviewer cannot delay the reply the user is waiting for.
"""

import asyncio

from agent import AgentContextType, LoopData
from helpers.extension import Extension
from plugins._ea_auto_skills.helpers import capture, config, review


class CaptureSuccess(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        agent = self.agent
        if not agent or agent.number != 0:
            return

        # Scheduled and background runs have an owner but no one present to
        # notice a bad skill appearing in their list.
        if agent.context.type != AgentContextType.USER:
            return

        try:
            settings = config.get_config(agent)
            if settings["mode"] == config.MODE_OFF:
                return

            # Recorded before the capture decision and independently of it. A
            # turn too short to review still read whatever it read, and that is
            # the fact worth counting; tying it to the review would only ever
            # count the turns that produced a skill.
            self._note_usage(agent)

            candidate = capture.find_candidate(agent, settings["min_tool_calls"])
            if not candidate:
                return

            # Stamp before dispatching: the hook may fire again for this same
            # turn while the review is still in flight.
            capture.mark_captured(agent, candidate.sequence)
            asyncio.create_task(review.run(agent, candidate, settings))
        except Exception:
            pass

    def _note_usage(self, agent) -> None:
        """Count the skills this turn loaded, for the owner who loaded them."""
        try:
            from helpers import user_scope
            from plugins._ea_auto_skills.helpers import store

            owner = user_scope.get_current_user_id()
            if not user_scope.is_safe_user_id(owner):
                return

            usage = capture.find_turn_usage(agent)
            if not usage:
                return

            store.note_usage(owner, usage.loaded, completed=usage.completed)
            capture.mark_usage_recorded(agent, usage.sequence)
        except Exception:
            # Counting must never break the turn it is counting.
            pass
