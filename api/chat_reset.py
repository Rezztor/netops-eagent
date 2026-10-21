from helpers.api import ApiHandler, Input, Output, Request, Response


from helpers import persist_chat
from helpers.task_scheduler import TaskScheduler


class Reset(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        ctxid = input.get("context", "")

        # context instance - get or create. First, because this is the
        # ownership check: cancelling before it stopped another account's
        # running task for anyone who sent its context id.
        context = self.use_context(ctxid)

        # attempt to stop any scheduler tasks bound to this context
        TaskScheduler.get().cancel_tasks_by_context(ctxid, terminate_thread=True)
        context.reset()
        persist_chat.save_tmp_chat(context)
        persist_chat.remove_msg_files(ctxid)

        # Reset updates context metadata (log guid/version) and must refresh other tabs' lists.
        from helpers.state_monitor_integration import mark_dirty_all
        mark_dirty_all(reason="api.chat_reset.Reset")

        return {
            "message": "Agent restarted.",
        }
