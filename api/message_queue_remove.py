from helpers.api import ApiHandler, Request, Response
from helpers import message_queue as mq
from helpers.state_monitor_integration import mark_dirty_for_context

class MessageQueueRemove(ApiHandler):
    """Remove message(s) from queue."""

    async def process(self, input: dict, request: Request) -> dict | Response:
        # Through use_context, which is where ownership is checked. Looking
        # the conversation up directly let one account queue a message in
        # another's and send it -- and a run executes as the owner.
        ctxid = input.get("context", "")
        context = self.use_context(ctxid, create_if_not_exists=False) if ctxid else None
        if not context:
            return Response("Context not found", status=404)

        item_id = input.get("item_id")  # None means clear all
        remaining = mq.remove(context, item_id)
        mark_dirty_for_context(context.id, reason="message_queue_remove")

        return {"ok": True, "remaining": remaining}
