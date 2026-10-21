from agent import AgentContext
from helpers import user_scope
from helpers.api import ApiHandler, Input, Output, Request, Response
from helpers.chat_removal import remove_chat_state, remove_chat_tasks
from helpers.context_utils import assert_context_access

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]

MAX_CHATS = 200


def _subagents(owner: str | None, roots: list[str]) -> list[str]:
    """Transitive sub-agent chats of `roots`, among the owner's own chats."""
    children: dict[str, list[str]] = {}
    for s in AgentContext.index_for_user(owner):
        data = s.output_data or {}
        if data.get("parent_context_kind") == "subordinate" and data.get("parent_context_id"):
            children.setdefault(data["parent_context_id"], []).append(s.id)
    found, queue = [], list(roots)
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in roots and child not in found:
                found.append(child)
                queue.append(child)
    return found


class RemoveChats(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        ids = input.get("contexts", [])
        if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
            raise ValueError("contexts must be a list of conversation ids")
        ids = list(dict.fromkeys(ids))
        if len(ids) > MAX_CHATS:
            raise ValueError(f"Too many conversations: at most {MAX_CHATS} per request")

        owner = user_scope.get_current_user_id()
        removed: list[str] = []
        failed: list[dict] = []

        def remove(ctxid: str) -> bool:
            try:
                # assert_context_access lets an unknown id through; here it is a failure.
                if AgentContext.get(ctxid) is None:
                    raise LookupError(f"Context {ctxid} not found")
                assert_context_access(ctxid)
                remove_chat_state(ctxid)
            except Exception as error:  # noqa: BLE001 - one bad id never aborts the batch
                failed.append({"id": ctxid, "error": str(error)})
                return False
            removed.append(ctxid)
            return True

        for ctxid in ids:
            remove(ctxid)
        # Only from chats actually removed: a refused id must not cascade into the caller's own chats.
        extra = _subagents(owner, list(removed)) if input.get("include_subagents") else []
        subagents_removed = sum(remove(c) for c in extra)

        await remove_chat_tasks(removed)

        from helpers.state_monitor_integration import mark_dirty_all
        mark_dirty_all(reason="api.chat_remove_many.RemoveChats")

        if audit is not None:
            audit.emit(
                "chat.remove_many",
                requested=len(ids),
                removed_count=len(removed),
                failed_count=len(failed),
                subagents_removed=subagents_removed,
                removed=removed,
                failed=[f["id"] for f in failed],
            )

        return {
            "removed": removed,
            "failed": failed,
            "subagents_removed": subagents_removed,
        }
