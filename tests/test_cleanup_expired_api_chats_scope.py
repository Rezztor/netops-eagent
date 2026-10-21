"""Expired API-chat cleanup removes the chat as its owner (audit 6, L10)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import extensions.python.job_loop._20_cleanup_expired_api_chats as cleanup
from helpers import user_scope


class _Context:
    id = "ctx-expired"
    owner_id = "alice"
    last_message = datetime.now(timezone.utc) - timedelta(hours=3)

    def get_data(self, key):
        return 1 if key == cleanup.LIFETIME_KEY else None

    def is_running(self):
        return False

    def reset(self):
        pass


def test_cleanup_removes_the_chat_in_its_owners_scope(monkeypatch):
    seen = []
    monkeypatch.setattr(cleanup.AgentContext, "all", staticmethod(lambda: [_Context()]))
    monkeypatch.setattr(cleanup.AgentContext, "remove", staticmethod(lambda _id: None))
    monkeypatch.setattr(cleanup.persist_chat, "remove_chat", lambda ctxid: seen.append((ctxid, user_scope.get_current_user_id())))
    monkeypatch.setattr(cleanup, "mark_dirty_all", lambda reason: None)
    monkeypatch.setattr(cleanup.CleanupExpiredApiChats, "_last_check", None)

    asyncio.run(cleanup.CleanupExpiredApiChats(agent=None).execute())

    assert seen == [("ctx-expired", "alice")]
    assert user_scope.get_current_user_id() is None
