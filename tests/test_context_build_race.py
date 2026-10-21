"""A conversation that is still being built or loaded is never handed out half-made, and is loaded once."""

import threading
import time

import pytest

from agent import AgentContext, ConversationSummary


@pytest.fixture
def registry(monkeypatch):
    """An empty registry for the test, with whatever was there put back afterwards."""
    from collections import OrderedDict

    monkeypatch.setattr(AgentContext, "_contexts", OrderedDict())
    monkeypatch.setattr(AgentContext, "_index", {})
    monkeypatch.setattr(AgentContext, "_hydrating", {})
    monkeypatch.setattr(AgentContext, "BUILD_WAIT_SECONDS", 5.0)
    return AgentContext


def _half_built(ctxid, builder):
    context = AgentContext.__new__(AgentContext)  # registered, but its agent is not there yet
    context.id, context._ready, context._builder = ctxid, threading.Event(), builder
    return context


def test_another_thread_waits_for_a_conversation_that_is_still_being_built(registry):
    # Seen live: a page asked for a conversation while it was being loaded and read `root_agent` before it existed.
    context = _half_built("c1", builder=-1)
    registry._contexts["c1"] = context
    got = []
    reader = threading.Thread(target=lambda: got.append(registry.get("c1")), daemon=True)
    reader.start()
    reader.join(0.3)
    assert reader.is_alive(), "get() returned a conversation whose builder had not finished"
    context.root_agent = object()
    context._ready.set()
    reader.join(2)
    assert got == [context]


def test_the_thread_that_builds_it_is_not_made_to_wait_for_itself(registry):
    context = _half_built("c1", builder=threading.get_ident())
    registry._contexts["c1"] = context
    started = time.monotonic()
    assert registry.get("c1") is context
    assert time.monotonic() - started < 1


def test_a_conversation_that_is_not_resident_is_loaded_once_however_many_ask(registry, monkeypatch):
    calls = []

    def hydrate(ctxid, owner_id):
        calls.append(ctxid)
        time.sleep(0.3)  # a real load reads the database and builds an agent
        context = _half_built(ctxid, builder=threading.get_ident())
        context._ready.set()
        registry._contexts[ctxid] = context
        return context

    monkeypatch.setattr(AgentContext, "_hydrate", staticmethod(hydrate))
    registry._index["c1"] = ConversationSummary.__new__(ConversationSummary)
    registry._index["c1"].owner_id = "alice"
    got = []
    threads = [threading.Thread(target=lambda: got.append(registry.get("c1")), daemon=True) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert calls == ["c1"]
    assert len(got) == 6 and all(c is got[0] and c is not None for c in got)
    assert registry._hydrating == {}


def test_a_conversation_that_has_gone_from_storage_is_dropped_and_frees_the_waiters(registry, monkeypatch):
    monkeypatch.setattr(AgentContext, "_hydrate", staticmethod(lambda ctxid, owner_id: None))
    registry._index["gone"] = ConversationSummary.__new__(ConversationSummary)
    registry._index["gone"].owner_id = "alice"
    assert registry.get("gone") is None
    assert "gone" not in registry._index and registry._hydrating == {}
