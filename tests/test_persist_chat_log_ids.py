from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_deserialize_log_preserves_item_id() -> None:
    from helpers.log import Log
    from helpers.persist_chat import _deserialize_log, _serialize_log

    log = Log()
    log.log(type="user", heading="User message", content="hello", id="msg-123")
    log.log(type="assistant", heading="Assistant", content="hi")

    serialized = _serialize_log(log)
    restored = _deserialize_log(serialized)

    assert restored.logs[0].type == "user"
    assert restored.logs[0].id == "msg-123"
    assert restored.logs[1].type == "assistant"
    assert restored.logs[1].id is None


def test_indexing_survives_a_row_it_cannot_summarise(database) -> None:
    """One bad conversation must not stop the rest from being listed.

    This used to be "skip a directory with no chat.json". There are no
    directories now, and the hazard moved with the data: a row whose document is
    not the shape the summariser expects.
    """
    from datetime import datetime, timezone

    from psycopg.types.json import Jsonb

    from agent import AgentContext
    from helpers import db, persist_chat

    with db.unscoped_transaction() as conn:
        for ctxid, document in (("fine", {"id": "fine"}), ("odd", "not an object")):
            conn.execute(
                """
                INSERT INTO conversations (id, owner_id, name, type, created_at,
                                           last_message, document)
                VALUES (%s, 'u1', %s, 'user', %s, %s, %s)
                """,
                (ctxid, ctxid, datetime.now(timezone.utc),
                 datetime.now(timezone.utc), Jsonb(document)),
            )

    ids = persist_chat.load_tmp_chats()
    assert "fine" in ids
    assert "fine" in {summary.id for summary in AgentContext.index_for_user("u1")}



def test_a_failed_save_leaves_the_stored_conversation_alone(database) -> None:
    """A torn write used to be the hazard, and atomic rename was the answer.
    The transaction is the answer now, and the property is the same one: an
    interrupted save must leave what was there rather than half of what was
    coming.
    """
    import pytest

    from agent import AgentConfig, AgentContext
    from helpers import db, persist_chat, user_scope

    with user_scope.user_scope("u1"):
        context = AgentContext(
            config=AgentConfig(mcp_servers="", profile="default"),
            id="save-fail",
            name="Before",
            set_current=False,
        )
        persist_chat.save_tmp_chat(context)

        context.name = "After"
        broken = object()  # not serialisable
        context.output_data["boom"] = broken
        with pytest.raises(Exception):
            persist_chat.save_tmp_chat(context)

    with db.unscoped_transaction() as conn:
        stored = conn.execute(
            "SELECT name FROM conversations WHERE id = %s", ("save-fail",)
        ).fetchone()
    assert stored == ("Before",), "a failed save overwrote the stored conversation"

    AgentContext.remove("save-fail")
