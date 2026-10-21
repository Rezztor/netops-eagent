"""Tidying up one user's vanished conversations must never touch another user's."""

from types import SimpleNamespace

import pytest

from helpers import persist_chat, state_snapshot, user_scope


def _context(ctxid, owner, saved=True):
    return SimpleNamespace(
        id=ctxid, owner_id=owner, type=state_snapshot.AgentContextType.USER, is_running=lambda: False,
        data={persist_chat.SAVED_CHAT_CONTEXT_DATA_KEY: True} if saved else {},
    )


@pytest.fixture
def registry(monkeypatch):
    resident = [_context("a1", "alice"), _context("a2", "alice"), _context("b1", "bob")]
    removed = []
    monkeypatch.setattr(state_snapshot.AgentContext, "all", staticmethod(lambda: list(resident)))
    monkeypatch.setattr(state_snapshot.AgentContext, "resident_for_user",
                        staticmethod(lambda user: [c for c in resident if user and c.owner_id == user]))
    monkeypatch.setattr(state_snapshot.AgentContext, "remove", staticmethod(removed.append))
    stored = {"alice": {"a1"}, "bob": {"b1"}}
    monkeypatch.setattr(persist_chat, "saved_chat_ids", lambda user_id=None: set(stored.get(user_id, set())))
    return removed


def test_one_users_refresh_removes_only_their_own_vanished_conversation(registry):
    # Seen live: a conversation that was in the database disappeared from its owner's list because another
    # user's screen refreshed. Alice's a2 has gone from storage; Bob's b1 has not and is not hers to judge.
    with user_scope.user_scope("alice"):
        state_snapshot._prune_missing_saved_contexts()
    assert registry == ["a2"]


def test_with_nobody_in_scope_nothing_is_removed(registry):
    # A push scheduled by a timer has no user: the stored set is empty, and everything open used to go.
    with user_scope.user_scope(None):
        state_snapshot._prune_missing_saved_contexts()
    assert registry == []
