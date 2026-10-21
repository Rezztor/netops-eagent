import json
import re

from helpers.api import ApiHandler, Input, Output, Request, Response
from helpers.localization import Localization
from helpers.persist_chat import (
    _serialize_context,
    _deserialize_context,
    save_tmp_chat,
)
from agent import AgentContext
from helpers.context_utils import assert_context_access
from helpers import user_scope


# " (branch)" or " (branch 4)", only at the end.
_BRANCH_SUFFIX = re.compile(r"\s*\(branch(?:\s+(\d+))?\)\s*$")


def _split_branch(name: str) -> tuple[str, int | None]:
    """A conversation's subject, and which branch of it this is.

    Strips the *whole* trailing chain, not one link of it. Names already in the
    wild carry six suffixes, and peeling one off would leave five: they would
    stop growing without ever getting shorter.

    The number returned is the outermost one, which after this change is the
    only one any new name has.
    """
    base = str(name or "").strip()
    outermost: int | None = None
    while True:
        found = _BRANCH_SUFFIX.search(base)
        if not found:
            return (base, outermost)
        if outermost is None:
            # A bare "(branch)" is number one.
            outermost = int(found.group(1) or 1)
        base = base[: found.start()].strip()


def _branch_name(src_name: str, existing: list[str]) -> str:
    """Name a branch for its subject, not for its ancestry.

    Appending "(branch)" to whatever the parent happened to be called compounds:
    branch a branch and you get "Chat (branch) (branch)", and after a few rounds
    the sidebar shows six copies of a word that distinguishes nothing while the
    part naming the actual conversation is pushed out of the column.

    So strip back to the subject and number the branches of it. Which branch you
    branched from stops mattering, which is right -- they are all branches of the
    same conversation, and the parent is no more the original than its siblings.

    Numbering is by sibling rather than by depth, so the names stay dense: with
    "Deploy (branch)" taken the next is "Deploy (branch 2)", whether it came from
    the parent or from the branch.
    """
    base, _ = _split_branch(src_name)
    if not base:
        base = "Chat"

    taken: set[int] = set()
    for name in existing:
        other_base, number = _split_branch(name)
        if number is not None and (other_base or "Chat") == base:
            taken.add(number)

    number = 1
    while number in taken:
        number += 1
    return f"{base} (branch)" if number == 1 else f"{base} (branch {number})"


def _trim_history_json(history_json: str, kept_ids: set[str], after_cut_ids: set[str]) -> str:
    """Trim a serialized history JSON to keep only messages that appear
    before the branch cut point.

    *kept_ids*: IDs of log items up to and including the cut point.
    *after_cut_ids*: IDs of log items after the cut point.

    Walk messages in order.  A message is kept while the running state
    is "keep".  The state flips to "drop" the first time we encounter
    a message whose id is in *after_cut_ids*.  Messages whose id does
    not appear in any log set (unpaired) inherit the current state.
    Summarized topics/bulks are always preserved.
    """
    if not history_json:
        return history_json

    hist = json.loads(history_json)
    keep = True  # running state

    def filter_messages(messages: list[dict]) -> list[dict]:
        nonlocal keep
        result = []
        for msg in messages:
            mid = msg.get("id", "")
            if mid and mid in after_cut_ids:
                keep = False
            elif mid and mid in kept_ids:
                keep = True
            # else: unpaired – inherit current state
            if keep:
                result.append(msg)
        return result

    # Bulks are already summarized old history – always keep
    # Topics: keep summarized ones; filter unsummarized
    trimmed_topics = []
    for topic in hist.get("topics", []):
        if topic.get("summary"):
            trimmed_topics.append(topic)
            continue
        msgs = filter_messages(topic.get("messages", []))
        if msgs:
            topic["messages"] = msgs
            trimmed_topics.append(topic)
        if not keep:
            break
    hist["topics"] = trimmed_topics

    # Current topic
    current = hist.get("current", {})
    if not current.get("summary") and keep:
        current["messages"] = filter_messages(current.get("messages", []))
    elif not keep:
        current["messages"] = []
    hist["current"] = current

    # Recount
    total = sum(
        len(t.get("messages", [])) for t in hist["topics"] if not t.get("summary")
    ) + len(hist.get("current", {}).get("messages", []))
    hist["counter"] = total

    return json.dumps(hist, ensure_ascii=False)


class BranchChat(ApiHandler):
    """Create a new chat branched from an existing chat at a specific log message."""

    async def process(self, input: Input, request: Request) -> Output:
        ctxid = input.get("context", "")
        log_no = input.get("log_no")  # LogItem.no from frontend

        if not ctxid:
            return Response("Missing context id", 400)
        if log_no is None:
            return Response("Missing log_no", 400)

        # AgentContext.get answers for any account's conversation; this is
        # the ownership check that use_context would have made.
        assert_context_access(ctxid)
        context = AgentContext.get(ctxid)
        if not context:
            return Response("Context not found", 404)

        # Serialize the source context
        data = _serialize_context(context)

        # Remove id so _deserialize_context generates a new one
        del data["id"]

        # Trim log entries: keep only items up to and including log_no.
        src_logs = data["log"]["logs"]
        cut_idx = None
        for i, item in enumerate(src_logs):
            if item["no"] == log_no:
                cut_idx = i
                break

        if cut_idx is None:
            if 0 <= log_no < len(src_logs):
                cut_idx = log_no
            else:
                return Response("log_no not found in chat log", 400)

        kept_logs = src_logs[: cut_idx + 1]
        after_logs = src_logs[cut_idx + 1 :]
        data["log"]["logs"] = kept_logs

        # Build ID sets for history trimming
        kept_ids = {item["id"] for item in kept_logs if item.get("id")}
        after_cut_ids = {item["id"] for item in after_logs if item.get("id")}

        # Trim each agent's history using ID matching
        for ag in data.get("agents", []):
            ag["history"] = _trim_history_json(
                ag.get("history", ""), kept_ids, after_cut_ids
            )

        # Name it for the conversation, not for the chain that produced it.
        # Scoped to the owner: sibling names are the user's own chat titles, and
        # counting anyone else's would leak that they exist.
        siblings = [
            summary.name
            for summary in AgentContext.index_for_user(user_scope.get_current_user_id())
        ]
        data["name"] = _branch_name(data.get("name") or "Chat", siblings)
        data["created_at"] = Localization.get().now_iso()

        # Deserialize into a brand-new context (new id, fresh agent config)
        new_context = _deserialize_context(data)

        # Persist immediately
        save_tmp_chat(new_context)

        # Notify all tabs
        from helpers.state_monitor_integration import mark_dirty_all
        mark_dirty_all(reason="plugins.chat_branching.BranchChat")

        return {
            "ok": True,
            "ctxid": new_context.id,
            "message": "Chat branched successfully.",
        }
