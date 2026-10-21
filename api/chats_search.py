"""Search this user's conversations for something that was said.

Scoped to the caller by `user_scope`, and there is no argument that widens it.
Searching is not a reason to be able to read someone else's chats.

**It searches the conversation, not the file.** A `chat.json` is mostly not the
conversation: each agent's serialised state carries `ctx_window.text`, the whole
rendered system prompt -- behavioural rules, the system manual, every tool
definition. One short chat is 80KB of which the transcript is a few hundred
bytes. A substring search over the file would therefore match the *system
prompt* in every chat that has one, so "linux" would return everything because
the manual says "favor linux commands". Only `log.logs` is read, and only the
entry types that carry what someone actually said.

Bounded on purpose: substring matching, a result cap, one snippet per chat. An
index would be a second store to keep correct, and "did we discuss the runner
egress rule" does not need ranking.
"""

from __future__ import annotations

import json
import re
from typing import Any

from helpers import persist_chat
from helpers.api import ApiHandler, Input, Output, Request

#: What counts as the conversation. `user` is what the person said, `response`
#: is what the agent answered. `util` is housekeeping the transcript already
#: hides, and `agent` is narration -- widening to either is one line here, and
#: should be a deliberate choice rather than a default.
SEARCHED_TYPES = ("user", "response")

MAX_RESULTS = 50
DEFAULT_RESULTS = 20
SNIPPET_RADIUS = 60
MIN_QUERY = 2


class ChatsSearch(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        query = str(input.get("query") or "").strip()
        if len(query) < MIN_QUERY:
            # Not an error: an empty box is the normal state of a search field.
            return {"ok": True, "query": query, "chats": [], "scanned": 0}

        try:
            limit = int(input.get("limit") or DEFAULT_RESULTS)
        except (TypeError, ValueError):
            limit = DEFAULT_RESULTS
        limit = max(1, min(limit, MAX_RESULTS))

        from helpers import db

        needle = query.casefold()
        results: list[dict[str, Any]] = []
        scanned = 0

        # The cheap pre-filter moved into the query. It is the same idea as
        # before -- if the term is nowhere in the document it cannot be in the
        # conversation either, and the reverse does not hold, so a match here is
        # still only a reason to look properly. What changed is that the
        # database does the reading, and the row-level policy decides which
        # documents exist at all.
        #
        # Scoped, not filtered afterwards. The policy is what keeps one user's
        # conversations out of another's search results; this query does not
        # mention owner_id and does not need to.
        with db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT id, name, document, last_message
                  FROM conversations
                 WHERE document::text ILIKE %s
                 ORDER BY last_message DESC
                """,
                (f"%{query}%",),
            ).fetchall()

        for ctxid, name, document, last_message in rows:
            scanned += 1
            hits, snippet = _search_transcript(document, needle)
            if not hits:
                continue

            results.append({
                "id": str(ctxid),
                "name": str(name or "") or "Untitled conversation",
                "hits": hits,
                "snippet": snippet,
                "last_message": (
                    last_message.isoformat() if last_message else None
                ),
            })
            if len(results) >= limit:
                break

        return {"ok": True, "query": query, "chats": results, "scanned": scanned}


def _search_transcript(document: Any, needle: str) -> tuple[int, str]:
    """Count matches in what was said, and quote the first one.

    Every step is checked rather than assumed. When conversations were files, a
    document of the wrong shape usually failed at `json.loads` and was skipped;
    a JSONB column parses anything that is valid JSON, so a document whose `log`
    is a string rather than an object now reaches here -- and used to take the
    whole search down with it rather than skipping one conversation.
    """
    if not isinstance(document, dict):
        return 0, ""
    log = document.get("log")
    if not isinstance(log, dict):
        return 0, ""
    entries = log.get("logs") or []
    if not isinstance(entries, list):
        return 0, ""

    hits = 0
    snippet = ""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "").lower() not in SEARCHED_TYPES:
            continue
        content = str(entry.get("content") or "")
        found = content.casefold().find(needle)
        if found < 0:
            continue
        hits += 1
        if not snippet:
            snippet = _quote(content, found, len(needle))
    return hits, snippet


def _quote(content: str, at: int, length: int) -> str:
    start = max(0, at - SNIPPET_RADIUS)
    end = min(len(content), at + length + SNIPPET_RADIUS)
    text = " ".join(content[start:end].split())
    return f"{'…' if start > 0 else ''}{text}{'…' if end < len(content) else ''}"
