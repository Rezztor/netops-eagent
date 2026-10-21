"""Searching conversations finds what was said, and nothing else.

The trap this guards is specific. A `chat.json` is mostly not the conversation:
each agent's serialised state carries `ctx_window.text`, the whole rendered
system prompt. One short chat is 80KB of which the transcript is a few hundred
bytes. Search the file and every chat matches the system manual -- so "linux"
returns everything, because the manual says "favor linux commands".
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from api.chats_search import ChatsSearch, _quote, _search_transcript


def chat(ctxid: str, name: str, said: list[tuple[str, str]], system_prompt: str = "") -> dict:
    """A chat.json shaped like the real thing, system prompt and all."""
    return {
        "id": ctxid,
        "name": name,
        "last_message": "2026-09-10T10:00:00+00:00",
        "agents": [{"number": 0, "data": {"ctx_window": {"text": system_prompt}}}],
        "log": {
            "logs": [
                {"no": i, "type": kind, "content": content, "heading": ""}
                for i, (kind, content) in enumerate(said)
            ]
        },
    }


def write(owner: str, document: dict) -> None:
    """Store a conversation for `owner`.

    Inserted as the owner role rather than through save_tmp_chat, because these
    tests are about the search and building a real AgentContext for each one
    would make them about agent construction instead.
    """
    from datetime import datetime, timezone

    from psycopg.types.json import Jsonb

    from helpers import db

    with db.unscoped_transaction() as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, owner_id, name, type, created_at,
                                       last_message, document)
            VALUES (%s, %s, %s, 'user', %s, %s, %s)
            """,
            (
                document["id"], owner, document.get("name"),
                datetime.now(timezone.utc), datetime.now(timezone.utc),
                Jsonb(document),
            ),
        )


@pytest.fixture
def chats(database, monkeypatch):
    """A signed-in user with an empty history.

    Scoping is the policy's job now, not a directory's, so the fixture supplies
    a current user rather than a folder.
    """
    from helpers import user_scope

    monkeypatch.setattr(user_scope, "get_current_user_id", lambda: "asha")
    return "asha"


def search(query: str, limit: int | None = None) -> dict:
    handler = ChatsSearch.__new__(ChatsSearch)
    body = {"query": query}
    if limit is not None:
        body["limit"] = limit
    return asyncio.run(handler.process(body, None))


# --- the trap ----------------------------------------------------------------

def test_the_system_prompt_is_not_searched(chats):
    """The whole reason this reads log.logs rather than the file."""
    write(chats, chat(
        "aaa", "Greeting",
        said=[("user", "hello there"), ("response", "Hi.")],
        system_prompt="favor linux commands for simple tasks where possible",
    ))
    result = search("linux")
    assert result["chats"] == [], "a hit in the system prompt is not a hit in the conversation"
    # It was read and rejected, not skipped: the cheap filter matched the file.
    assert result["scanned"] == 1


def test_housekeeping_entries_are_not_the_conversation(chats):
    write(chats, chat("bbb", "Chat", said=[("util", "No memories or solutions found")]))
    assert search("memories")["chats"] == []


# --- finding things ----------------------------------------------------------

def test_it_finds_what_the_user_said(chats):
    write(chats, chat("ccc", "Deploy review", said=[("user", "check the networkpolicy rule")]))
    hits = search("networkpolicy")["chats"]
    assert [c["id"] for c in hits] == ["ccc"]
    assert hits[0]["name"] == "Deploy review"
    assert hits[0]["hits"] == 1
    assert "networkpolicy" in hits[0]["snippet"]


def test_it_finds_what_the_agent_answered(chats):
    write(chats, chat("ddd", "Chat", said=[("response", "The runner's egress is denied.")]))
    assert search("egress")["chats"][0]["id"] == "ddd"


def test_matching_is_case_insensitive_and_counted(chats):
    write(chats, chat("eee", "Chat", said=[
        ("user", "VLAN please"), ("response", "vlan 300 added"), ("user", "and Vlan 400"),
    ]))
    assert search("vlan")["chats"][0]["hits"] == 3


def test_an_untitled_conversation_still_has_a_name(chats):
    write(chats, chat("fff", "", said=[("user", "networkpolicy")]))
    assert search("networkpolicy")["chats"][0]["name"] == "Untitled conversation"


# --- bounds ------------------------------------------------------------------

def test_a_short_query_returns_nothing_rather_than_everything(chats):
    write(chats, chat("ggg", "Chat", said=[("user", "a b c")]))
    assert search("a")["chats"] == []
    assert search("")["chats"] == []


def test_the_result_cap_is_honoured(chats):
    for i in range(6):
        write(chats, chat(f"c{i}", f"Chat {i}", said=[("user", "networkpolicy")]))
    assert len(search("networkpolicy", limit=3)["chats"]) == 3


def test_a_document_of_an_unexpected_shape_is_skipped_not_fatal(chats):
    """A chat.json could be truncated or half-written; a JSONB column cannot.
    What survives the move is the other half of the hazard -- a document whose
    shape is not what the transcript reader expects -- and one bad conversation
    must not take the whole search down with it.
    """
    from psycopg.types.json import Jsonb

    from helpers import db

    write(chats, chat("good", "Good", said=[("user", "networkpolicy")]))
    with db.unscoped_transaction() as conn:
        from datetime import datetime, timezone

        conn.execute(
            """
            INSERT INTO conversations (id, owner_id, name, type, created_at,
                                       last_message, document)
            VALUES ('broken', %s, 'Broken', 'user', %s, %s, %s)
            """,
            (
                chats, datetime.now(timezone.utc), datetime.now(timezone.utc),
                Jsonb({"log": "networkpolicy but not the shape this expects"}),
            ),
        )
    assert [c["id"] for c in search("networkpolicy")["chats"]] == ["good"]


# --- snippets ----------------------------------------------------------------

def test_a_snippet_is_trimmed_around_the_match():
    content = "x" * 200 + "needle" + "y" * 200
    quoted = _quote(content, 200, len("needle"))
    assert quoted.startswith("…") and quoted.endswith("…")
    assert "needle" in quoted
    assert len(quoted) < len(content)


def test_a_snippet_collapses_whitespace():
    assert _quote("a\n\n  b needle c", 9, 6) == "a b needle c"


def test_no_transcript_is_no_hits():
    assert _search_transcript({}, "anything") == (0, "")
    assert _search_transcript({"log": {"logs": "not a list"}}, "x") == (0, "")


# --- whose conversations -----------------------------------------------------

def test_it_searches_only_the_callers_own_chats(database, monkeypatch):
    """Scoping used to be "which directory did we open". It is the row-level
    policy now, and this is the first test that exercises a real one.

    The query in `chats_search` does not mention owner_id at all -- that is the
    point. If the policy were dropped, this test would fail and nothing else
    would.
    """
    from helpers import user_scope

    write("asha", chat("aaaa1111", "Asha's chat", [("user", "the marmoset plan")]))
    write("bruno", chat("bbbb2222", "Bruno's chat", [("user", "the marmoset plan")]))

    monkeypatch.setattr(user_scope, "get_current_user_id", lambda: "asha")
    result = search("marmoset")

    assert [hit["id"] for hit in result["chats"]] == ["aaaa1111"]
    assert "bbbb2222" not in json.dumps(result)


def test_no_argument_widens_the_scope(database, monkeypatch):
    """A `user_id` in the request body must be inert. An endpoint that read one
    would let any signed-in account name someone else's -- and with the policy in
    place it could not honour it even if it tried."""
    from helpers import user_scope

    write("bruno", chat("bbbb2222", "Bruno's chat", [("user", "the marmoset plan")]))

    monkeypatch.setattr(user_scope, "get_current_user_id", lambda: "asha")
    handler = ChatsSearch.__new__(ChatsSearch)
    result = asyncio.run(
        handler.process({"query": "marmoset", "user_id": "bruno", "owner": "bruno"}, None)
    )
    assert result["chats"] == []
