from helpers.api import ApiHandler, Input, Output, Request, Response

from helpers import persist_chat


def _audit(event: str, **fields):
    """Audit without letting a logging failure fail the operation."""
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:
        pass


class ExportChat(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        ctxid = input.get("ctxid", "")
        if not ctxid:
            raise Exception("No context id provided")

        context = self.use_context(ctxid, create_if_not_exists=False)
        content = persist_chat.export_json_chat(context)
        # Who exported which conversation. Never the transcript.
        _audit("chat.export", context_id=context.id)
        return {
            "message": "Chats exported.",
            "ctxid": context.id,
            "content": content,
        }