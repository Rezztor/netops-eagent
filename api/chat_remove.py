from helpers.api import ApiHandler, Input, Output, Request, Response
from helpers.context_utils import assert_context_access
from helpers.chat_removal import remove_chat_state, remove_chat_tasks


class RemoveChat(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        ctxid = input.get("context", "")

        # Removal reaches AgentContext directly rather than through
        # use_context, so the ownership check is made explicitly here.
        assert_context_access(ctxid)

        remove_chat_state(ctxid)
        await remove_chat_tasks([ctxid])

        # Context removal affects global chat/task lists in all tabs.
        from helpers.state_monitor_integration import mark_dirty_all
        mark_dirty_all(reason="api.chat_remove.RemoveChat")

        return {
            "message": "Context removed.",
        }
