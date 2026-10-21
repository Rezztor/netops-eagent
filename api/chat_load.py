from helpers.api import ApiHandler, Input, Output, Request, Response


from helpers import login, persist_chat, rbac

class LoadChats(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        chats = input.get("chats", [])
        if not chats:
            raise Exception("No chats provided")

        # A free-form model override in a chat holds `api_base`; see load_json_chats.
        ctxids = persist_chat.load_json_chats(
            chats, may_set_connection=login.has_permission(rbac.PERM_SETTINGS_MANAGE)
        )

        return {
            "message": "Chats loaded.",
            "ctxids": ctxids,
        }
