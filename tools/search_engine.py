from helpers import searxng
from helpers.errors import handle_error
from helpers.tool import Tool, Response

SEARCH_ENGINE_RESULTS = 10


class SearchEngine(Tool):
    """Web search.

    Every failure becomes a `Response`, never an exception. That is not
    politeness: an exception here escapes to `Agent.handle_exception`, which the
    `_error_retry` plugin classes as critical, retries once and then ends the
    conversation. So an unreachable search engine used to destroy the turn
    rather than telling the model to carry on without it -- which is exactly the
    wrong way round, because search is the most optional thing the agent does.

    The shape is `plugins/_ea_ansible/tools/net_read.py`: a configuration guard
    first, then one branch per failure the client can name.
    """

    async def execute(self, query="", **kwargs):
        if not searxng.is_configured():
            return Response(
                message=(
                    "Web search is not configured on this instance, so there are "
                    "no results. Answer from what you already know, or say what "
                    "you would have looked up. Do not retry this tool."
                ),
                break_loop=False,
            )

        try:
            results = await searxng.search(query)
        except searxng.NotConfigured as error:
            return Response(message=str(error), break_loop=False)
        except searxng.SearchUnreachable as error:
            handle_error(error)
            return Response(
                message=f"{error} Answer without web results rather than retrying.",
                break_loop=False,
            )
        except searxng.SearchRefused as error:
            handle_error(error)
            return Response(message=str(error), break_loop=False)

        output = self.format_results(results)
        await self.agent.handle_intervention(output)
        return Response(message=output, break_loop=False)

    def format_results(self, result) -> str:
        # `.get`, not `[...]`. A result without a `content` key -- which some
        # engines return -- used to raise KeyError into the critical path above,
        # turning one odd search hit into a dead conversation.
        outputs = []
        for item in (result or {}).get("results", []):
            outputs.append(
                "\n".join(
                    str(item.get(key) or "")
                    for key in ("title", "url", "content")
                )
            )

        if not outputs:
            return "The search returned no results."
        return "\n\n".join(outputs[:SEARCH_ENGINE_RESULTS]).strip()
