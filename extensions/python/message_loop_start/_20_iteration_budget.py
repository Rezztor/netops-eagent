import os

from helpers.errors import HandledException
from helpers.extension import Extension
from agent import LoopData

# Model calls one run (one user message, or one delegated assignment) may make. Nothing else ends a run whose model
# keeps calling tools, and a scheduled task has nobody watching to press stop.
MAX_ITERATIONS = int(os.environ.get("REZZTOR_MAX_LOOP_ITERATIONS", "100"))
# Calls allowed after the warning for the model to give its final response.
GRACE_ITERATIONS = 2


class IterationBudget(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent or MAX_ITERATIONS <= 0:
            return
        # loop_data.iteration counts from 0 in each run
        if loop_data.iteration == MAX_ITERATIONS:
            # What the repairable-exception handler does, without raising: here, outside the message loop's own try,
            # a raise would restart the run with a fresh count.
            warning = (
                f"This task has used its budget of {MAX_ITERATIONS} steps. Do not call any more tools: "
                "give your final answer now with the response tool, saying what is done and what is left."
            )
            msg = self.agent.hist_add_warning(warning)
            self.agent.context.log.log(type="warning", content=warning, id=msg.id)
        elif loop_data.iteration >= MAX_ITERATIONS + GRACE_ITERATIONS:
            stop = (
                f"Agent stopped after {loop_data.iteration} steps on one task to prevent further API charges. "
                "Send a new message to continue."
            )
            self.agent.context.log.log(type="warning", content=stop)
            raise HandledException(stop)
