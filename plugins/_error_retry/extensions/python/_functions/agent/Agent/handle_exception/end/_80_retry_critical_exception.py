import asyncio
from datetime import datetime, timezone
import litellm
from helpers.extension import Extension
from agent import LoopData
from helpers.localization import Localization
from helpers.errors import RepairableException, HandledException
from helpers import errors, plugins
from helpers.print_style import PrintStyle

from plugins._error_retry.extensions.python._functions.agent.Agent.monologue.start._10_reset_critical_exception_counter import DATA_NAME_COUNTER

DEFAULT_MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 3


def normalize_max_retries(value, default: int = DEFAULT_MAX_RETRIES) -> int:
    if value is None or isinstance(value, bool):
        return default

    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


class RetryCriticalException(Extension):
    async def execute(self, data: dict = {}, **kwargs):
        if not self.agent:
            return

        exception = data.get("exception")
        if not exception:
            self.agent.set_data(DATA_NAME_COUNTER, 0) # reset counter if exception has been handled
            return

        if isinstance(exception, (HandledException, RepairableException)):
            self.agent.set_data(DATA_NAME_COUNTER, 0) # reset counter if exception has been handled
            return

        cfg = plugins.get_plugin_config("_error_retry", agent=self.agent) or {}
        max_retries = normalize_max_retries(cfg.get("retries"))

        counter = self.agent.get_data(DATA_NAME_COUNTER) or 0
        if counter >= max_retries:
            self.when_critical(data)
            return

        self.agent.set_data(DATA_NAME_COUNTER, counter + 1)

        # The agent's history is the model's, and the model answers whoever
        # owns the chat: it gets the exception's class and the plain sentence,
        # never the traceback, which names server paths. The traceback goes to
        # the server log here and to an administrator's chat as `details`.
        error_message = f"{type(exception).__name__}: {errors.user_message(exception)}"
        import uuid as _uuid
        msg_id = str(_uuid.uuid4())
        details = errors.details_for(self.agent.context, exception)
        self.agent.context.log.log(
            type="warning",
            heading="Something went wrong; trying again",
            content=errors.user_message(exception),
            kvps={"details": details} if details else None,
            id=msg_id,
        )
        PrintStyle(font_color="orange", padding=True).print(
            "Critical error occurred, retrying...\n" + errors.format_error(exception)
        )
        await asyncio.sleep(RETRY_DELAY_SECONDS)
        await self.agent.handle_intervention()
        agent_facing_error = self.agent.read_prompt(
            "fw.msg_critical_error.md", error_message=error_message
        )
        self.agent.hist_add_warning(message=agent_facing_error, id=msg_id)
        PrintStyle(font_color="orange", padding=True).print(agent_facing_error)

        data["exception"] = None


    def when_critical(self, data: dict = {}):
        if not self.agent:
            return

        self.try_clear_embeds(data)

    def try_clear_embeds(self, data: dict = {}):
        """Try to clear embeds before failing on LiteLLM errors"""
        
        if not self.agent:
            return

        exc = data.get("exception")
        if not isinstance(exc, litellm.exceptions.BadRequestError):
            return

        cfg = plugins.get_plugin_config("_error_retry", agent=self.agent) or {}
        if not cfg.get("try_clear_embeds", False):
            return

        removed = self.agent.history.remove_all_embeds()
        if removed <= 0:
            return

        data["exception"] = None
        self.agent.context.log.log(
            type="warning",
            heading="Cleared embedded media from history",
            content=f"Persistent LiteLLM bad request detected. Removed {removed} embedded media messages from history to help recover.",
        )
        PrintStyle(font_color="orange", padding=True).print(
            f"Cleared {removed} embedded media messages from history after persistent LiteLLM bad request."
        )
