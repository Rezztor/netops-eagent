import asyncio

from helpers.extension import Extension
from helpers.print_style import PrintStyle
from helpers import errors

from helpers.errors import HandledException


class HandleCriticalException(Extension):
    async def execute(self, data: dict = {}, **kwargs):
        if not self.agent:
            return

        if not (exception:= data.get("exception")):
            return

        # when exception is HandledException, keep it active, no logging here
        if isinstance(exception, HandledException):
            return 

        # asyncio cancel - chat is being terminated, print out and re-raise as handledException
        if isinstance(exception, asyncio.CancelledError):
            PrintStyle(font_color="white", background_color="red", padding=True).print(
                f"Context {self.agent.context.id} terminated during message loop"
            )
            data["exception"] = HandledException(exception)
            return

        # other exceptions should be logged and re-raised as HandledException
        error_text = errors.error_text(exception)
        # The chat gets one sentence; the traceback names server paths, slots and
        # provider URLs, so only an administrator's chat carries it, folded. log_failure prints it
        # to the server's output either way.
        errors.log_failure(self.agent.context, exception)
        PrintStyle(font_color="red", padding=True).print(
            f"{self.agent.agent_name}: {error_text}"
        )

        data["exception"] = HandledException(exception)
