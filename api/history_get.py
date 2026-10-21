from helpers.api import ApiHandler, Request, Response


class GetHistory(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict | Response:
        ctxid = input.get("context", [])
        context = self.use_context(ctxid, create_if_not_exists=False)
        agent = context.streaming_agent or context.root_agent
        history = agent.history.output_text()
        size = agent.history.get_tokens()

        return {
            "history": history,
            "tokens": size
        }