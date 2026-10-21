from helpers.api import ApiHandler, Request, Response
from helpers import message_queue as mq
from helpers.state_monitor_integration import mark_dirty_for_context

class MessageQueueSend(ApiHandler):
    """Send queued message(s) immediately."""

    async def process(self, input: dict, request: Request) -> dict | Response:
        # Through use_context, which is where ownership is checked. Looking
        # the conversation up directly let one account queue a message in
        # another's and send it -- and a run executes as the owner.
        ctxid = input.get("context", "")
        context = self.use_context(ctxid, create_if_not_exists=False) if ctxid else None
        if not context:
            return Response("Context not found", status=404)

        if not mq.has_queue(context):
            return {"ok": True, "message": "Queue empty"}

        item_id = input.get("item_id")
        send_all = input.get("send_all", False)

        if send_all:
            count = mq.send_all_aggregated(context)
            if count:
                mark_dirty_for_context(context.id, reason="message_queue_send_all")
            return {"ok": True, "sent_count": count}

        # Send single item
        item = mq.pop_item(context, item_id) if item_id else mq.pop_first(context)
        if not item:
            return Response("Item not found", status=404)

        mq.send_message(context, item)
        mark_dirty_for_context(context.id, reason="message_queue_send")
        return {"ok": True, "sent_item_id": item["id"]}
