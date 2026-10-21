from helpers.tool import Response, Tool

REFUSED_KEY = "_tool_refused"


class Refused(Tool):
    """Returned in place of a tool the conversation's owner may not invoke.

    Distinct from Unknown on purpose. Unknown means "no such tool" and replies
    with the full tool list, which would be both wrong and a disclosure here:
    the tool does exist, the account simply may not use it, and re-listing every
    tool invites the model to hunt for a substitute.
    """

    async def execute(self, **kwargs):
        # The audit's end-of-tool record reads this, so a refused call is not written down as one that ran.
        self.agent.set_data(REFUSED_KEY, True)
        message = getattr(self, "refusal_message", None) or (
            f"The tool '{self.name}' is not available to this account."
        )
        return Response(message=message, break_loop=False)
