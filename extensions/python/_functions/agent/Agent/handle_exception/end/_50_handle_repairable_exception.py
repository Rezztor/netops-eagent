from datetime import datetime, timezone
from helpers.extension import Extension
from agent import LoopData
from helpers.localization import Localization
from helpers.errors import RepairableException
from helpers import errors, extension
from helpers.print_style import PrintStyle

class HandleRepairableException(Extension):
    async def execute(self, data: dict = {}, **kwargs):
        if not self.agent:
            return

        if not data.get("exception"):
            return

        if isinstance(data["exception"], RepairableException):
            # A repairable error is a sentence written for the model and the reader. The traceback around it cost the
            # model tokens and showed the reader file paths; it stays in the server's own output, where it is useful.
            msg = {"message": str(data["exception"]) or errors.format_error(data["exception"])}
            await extension.call_extensions_async("error_format", agent=self.agent, msg=msg)
            wmsg = self.agent.hist_add_warning(msg["message"])
            PrintStyle(font_color="red", padding=True).print(errors.format_error(data["exception"]))
            self.agent.context.log.log(type="warning", content=msg["message"], id=wmsg.id)
            data["exception"] = None

        
