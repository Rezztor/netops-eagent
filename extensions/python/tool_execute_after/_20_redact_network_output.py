from helpers.extension import Extension
from helpers.tool import Response
from plugins._ea_inventory.helpers.redact import redact_text


class RedactNetworkOutput(Extension):
    """One choke point: every network/inventory tool result is scrubbed, so a new tool cannot forget.

    redact_text is idempotent (typed tokens are skipped) and never raises; on an internal
    failure it replaces the whole text rather than passing it through.
    ponytail: matched by name prefix, so a network tool named outside net_*/inventory_*/solarwinds_* is not covered.
    """

    async def execute(self, response: Response | None = None, tool_name: str = "", **kwargs):
        name = (tool_name or "").split(":")[0]
        if response and isinstance(response.message, str) and name.startswith(("net_", "inventory_", "solarwinds_")):
            response.message = redact_text(response.message)[0]
