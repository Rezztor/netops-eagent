"""Conversations load when they are used, not when the process starts.

Before this, `load_tmp_chats()` deserialised every user's every conversation
into a process-global dict on the blocking boot path, with no eviction and no
cap. Measured, that is ~16 ms and ~0.45 MB each: a 2Gi pod holds roughly 2,300
conversations and spends forty seconds booting before it reaches them
(`docs/internal/storage-plan.md`).

The tests here are about the properties that make lazy loading safe rather than
merely faster. The dangerous ones are eviction — dropping a conversation someone
is still using, or dropping one with unsaved work — and the index, which is a new
process-global cache in front of per-user data and therefore exactly the shape
ADR 0003 records a cross-user leak from.
"""

from __future__ import annotations

import os

import pytest

from agent import AgentConfig, AgentContext, AgentContextType
from helpers import files, persist_chat, user_scope


@pytest.fixture
def instance(database, tmp_path, monkeypatch):
    from testsupport import build_instance_root

    monkeypatch.setattr(files, "_base_dir", build_instance_root(tmp_path))
    persist_chat.install_context_hooks()
    created: list[str] = []
    try:
        yield created
    finally:
        for ctxid in created:
            AgentContext.remove(ctxid)


def _config():
    return AgentConfig(mcp_servers="", profile="default")


def _write_chat(owner: str, ctxid: str, name: str, created: list[str]) -> None:
    """Create a conversation, save it, then drop it from memory.

    That last step is the point: what is left is a conversation that exists on
    disk and is not resident, which is the state every conversation is in after
    boot now.
    """
    with user_scope.user_scope(owner):
        context = AgentContext(config=_config(), id=ctxid, name=name, set_current=False)
        persist_chat.save_tmp_chat(context)
    created.append(ctxid)
    with AgentContext._contexts_lock:
        AgentContext._contexts.pop(ctxid, None)


# --- loading on use ----------------------------------------------------------

def test_a_conversation_that_is_not_resident_still_loads(database, instance):
    _write_chat("asha", "lazy-one", "Nightly backup", instance)
    assert "lazy-one" not in AgentContext._contexts

    context = AgentContext.get("lazy-one")
    assert context is not None
    assert context.name == "Nightly backup"
    assert "lazy-one" in AgentContext._contexts


def test_loading_the_same_conversation_twice_returns_the_same_object(database, instance):
    """Otherwise two callers would hold different copies of one conversation and
    the second save would overwrite the first one's work."""
    _write_chat("asha", "lazy-same", "Same", instance)
    assert AgentContext.get("lazy-same") is AgentContext.get("lazy-same")


def test_a_conversation_that_is_not_anywhere_is_not_found(instance):
    assert AgentContext.get("never-existed") is None


def test_an_index_entry_whose_row_has_gone_stops_being_listed(instance):
    """A conversation deleted from under us should stop being listed, not fail
    to load forever. It used to be a file; it is a row now, and the behaviour
    has to survive the change."""
    from helpers import db

    _write_chat("asha", "lazy-gone", "Gone", instance)
    with db.unscoped_transaction() as conn:
        conn.execute("DELETE FROM conversations WHERE id = %s", ("lazy-gone",))

    assert AgentContext.get("lazy-gone") is None
    assert "lazy-gone" not in {s.id for s in AgentContext.index_for_user("asha")}


# --- the index ---------------------------------------------------------------

def test_the_sidebar_lists_conversations_that_are_not_loaded(instance):
    for index in range(3):
        _write_chat("asha", f"lazy-list-{index}", f"Chat {index}", instance)

    listed = {summary.id for summary in AgentContext.index_for_user("asha")}
    assert {"lazy-list-0", "lazy-list-1", "lazy-list-2"} <= listed
    assert not any(ctxid in AgentContext._contexts for ctxid in listed)


def test_a_loaded_conversation_is_summarised_live_not_from_the_snapshot(database, instance):
    """The stored entry is a snapshot from the last write. A conversation in
    memory moves underneath it -- its name changes, a tool writes into its output
    data -- and listing it from the snapshot would show yesterday's answer."""
    _write_chat("asha", "lazy-live", "Before", instance)
    context = AgentContext.get("lazy-live")
    assert context is not None
    context.name = "After"
    context.output_data["parent_context_id"] = "somewhere"

    (summary,) = [s for s in AgentContext.index_for_user("asha") if s.id == "lazy-live"]
    assert summary.name == "After"
    assert summary.output().get("parent_context_id") == "somewhere"


def test_a_new_id_cannot_collide_with_a_conversation_that_is_merely_unloaded(instance):
    """Generated ids used to be checked against what was loaded. Once most
    conversations are not loaded, that check would hand out an id already in use
    and the new conversation would overwrite it on save."""
    _write_chat("asha", "lazy-collide", "Taken", instance)
    with AgentContext._contexts_lock:
        assert "lazy-collide" not in AgentContext._contexts
        assert "lazy-collide" in AgentContext._index

    for _ in range(200):
        assert AgentContext.generate_id() != "lazy-collide"


# --- eviction ----------------------------------------------------------------

def test_going_over_the_cap_evicts_the_least_recently_used(database, instance, monkeypatch):
    monkeypatch.setattr(AgentContext, "MAX_RESIDENT", 3)
    for index in range(5):
        _write_chat("asha", f"lazy-evict-{index}", f"Chat {index}", instance)

    for index in range(5):
        AgentContext.get(f"lazy-evict-{index}")

    assert len(AgentContext._contexts) <= 3
    # The first ones touched are the ones gone.
    assert "lazy-evict-0" not in AgentContext._contexts
    assert "lazy-evict-4" in AgentContext._contexts


def test_eviction_writes_the_conversation_back_before_dropping_it(database, instance, monkeypatch):
    """An evicted conversation with unsaved changes is lost work, and it would be
    lost silently."""
    monkeypatch.setattr(AgentContext, "MAX_RESIDENT", 1)
    _write_chat("asha", "lazy-dirty", "Original", instance)

    with user_scope.user_scope("asha"):
        dirty = AgentContext.get("lazy-dirty")
        assert dirty is not None
        dirty.name = "Changed after loading"

        # Load another, which pushes the first out.
        _write_chat("asha", "lazy-pusher", "Pusher", instance)
        AgentContext.get("lazy-pusher")

        assert "lazy-dirty" not in AgentContext._contexts
        reloaded = AgentContext.get("lazy-dirty")
        assert reloaded is not None
        assert reloaded.name == "Changed after loading"


def test_a_running_conversation_is_not_evicted(database, instance, monkeypatch):
    """Going over the cap is the right answer when everything resident is busy.
    Killing running work to honour a memory cap is not."""
    monkeypatch.setattr(AgentContext, "MAX_RESIDENT", 1)
    _write_chat("asha", "lazy-busy", "Busy", instance)
    busy = AgentContext.get("lazy-busy")
    assert busy is not None

    class Alive:
        def is_alive(self):
            return True

        def kill(self):
            raise AssertionError("a running conversation was killed to free memory")

    busy.task = Alive()  # type: ignore[assignment]
    try:
        for index in range(4):
            _write_chat("asha", f"lazy-crowd-{index}", f"Crowd {index}", instance)
            AgentContext.get(f"lazy-crowd-{index}")
        assert "lazy-busy" in AgentContext._contexts
    finally:
        busy.task = None


def test_background_conversations_are_not_indexed_as_chats(instance):
    """They are ephemeral by design and `save_tmp_chat` refuses them, so an
    index entry would list a conversation with no file behind it."""
    with user_scope.user_scope("asha"):
        context = AgentContext(
            config=_config(),
            id="lazy-background",
            type=AgentContextType.BACKGROUND,
            set_current=False,
        )
        instance.append("lazy-background")
        persist_chat.save_tmp_chat(context)

    from helpers import db

    with db.unscoped_transaction() as conn:
        assert conn.execute(
            "SELECT count(*) FROM conversations WHERE id = 'lazy-background'"
        ).fetchone() == (0,)
