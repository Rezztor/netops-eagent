"""Bulk and single conversation removal (api/chat_remove*.py, helpers/chat_removal.py)."""

import asyncio
from types import SimpleNamespace

import pytest

from agent import AgentConfig, AgentContext
from helpers import chat_removal, files, user_scope


class FakeScheduler:
    def __init__(self, tasks=()):
        self.tasks = list(tasks)
        self.cancelled, self.removed, self.reloads = [], [], 0

    def cancel_tasks_by_context(self, ctxid, terminate_thread=False):
        self.cancelled.append((ctxid, terminate_thread))

    async def reload(self):
        self.reloads += 1

    def get_tasks_by_context_id(self, ctxid):
        return [t for t in self.tasks if t.context_id == ctxid]

    async def remove_task_by_uuid(self, uuid):
        self.removed.append(uuid)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from testsupport import build_instance_root

    monkeypatch.setattr(files, "_base_dir", build_instance_root(tmp_path))
    scheduler = FakeScheduler()
    persisted, dirty = [], []
    monkeypatch.setattr(chat_removal.TaskScheduler, "get", staticmethod(lambda: scheduler))
    monkeypatch.setattr(chat_removal.persist_chat, "remove_chat", persisted.append)
    monkeypatch.setattr(
        "helpers.state_monitor_integration.mark_dirty_all", lambda **kw: "api.chat_remove" in kw.get("reason", "") and dirty.append(kw)
    )
    made = []
    yield SimpleNamespace(scheduler=scheduler, persisted=persisted, dirty=dirty, made=made)
    for ctxid in made:
        AgentContext.remove(ctxid)


def make(env, owner, ctxid, parent=None, kind="subordinate"):
    AgentContext.remove(ctxid)
    with user_scope.user_scope(owner):
        context = AgentContext(
            config=AgentConfig(mcp_servers="", profile="default"), id=ctxid, set_current=False
        )
    if parent:
        context.output_data["parent_context_id"] = parent
        context.output_data["parent_context_kind"] = kind
    AgentContext.remember(context.summary())
    env.made.append(ctxid)
    return context


def call(handler_cls, user, **body):
    from api.chat_remove import RemoveChat  # noqa: F401
    from api.chat_remove_many import RemoveChats  # noqa: F401

    async def run():
        with user_scope.user_scope(user):
            return await handler_cls(None, None).process(body, None)

    return asyncio.run(run())


def many(user, contexts, include_subagents=False):
    from api.chat_remove_many import RemoveChats

    return call(RemoveChats, user, contexts=contexts, include_subagents=include_subagents)


def test_single_delete_unchanged(env):
    from api.chat_remove import RemoveChat

    make(env, "alice", "rm-one")
    env.scheduler.tasks.append(SimpleNamespace(context_id="rm-one", uuid="t1"))
    assert call(RemoveChat, "alice", context="rm-one") == {"message": "Context removed."}
    assert AgentContext.get("rm-one") is None
    assert env.persisted == ["rm-one"]
    assert env.scheduler.cancelled == [("rm-one", True)]
    assert env.scheduler.removed == ["t1"] and env.scheduler.reloads == 1
    assert len(env.dirty) == 1


def test_other_users_chat_fails_and_the_rest_still_go(env):
    make(env, "alice", "rm-a1")
    make(env, "bob", "rm-b1")
    make(env, "alice", "rm-a2")
    result = many("alice", ["rm-a1", "rm-b1", "rm-a2"])
    assert result["removed"] == ["rm-a1", "rm-a2"]
    assert [f["id"] for f in result["failed"]] == ["rm-b1"]
    assert result["failed"][0]["error"]
    assert AgentContext.get("rm-b1") is not None
    assert env.persisted == ["rm-a1", "rm-a2"]


def test_unknown_id_is_a_failure_not_an_abort(env):
    make(env, "alice", "rm-ok")
    result = many("alice", ["nope", "rm-ok"])
    assert result["removed"] == ["rm-ok"]
    assert result["failed"][0]["id"] == "nope"
    assert set(result) == {"removed", "failed", "subagents_removed"}


def test_cascade_on_is_transitive(env):
    make(env, "alice", "rm-p")
    make(env, "alice", "rm-c", parent="rm-p")
    make(env, "alice", "rm-g", parent="rm-c")
    make(env, "alice", "rm-other")
    result = many("alice", ["rm-p"], include_subagents=True)
    assert result["removed"] == ["rm-p", "rm-c", "rm-g"]
    assert result["subagents_removed"] == 2
    assert AgentContext.get("rm-other") is not None


def test_cascade_off_leaves_children(env):
    make(env, "alice", "rm-p")
    make(env, "alice", "rm-c", parent="rm-p")
    result = many("alice", ["rm-p"], include_subagents=False)
    assert result["removed"] == ["rm-p"] and result["subagents_removed"] == 0
    assert AgentContext.get("rm-c") is not None


def test_cascade_ignores_non_subordinate_and_other_owner_children(env):
    make(env, "alice", "rm-p")
    make(env, "alice", "rm-branch", parent="rm-p", kind="branch")
    make(env, "bob", "rm-bobchild", parent="rm-p")
    result = many("alice", ["rm-p"], include_subagents=True)
    assert result["removed"] == ["rm-p"]
    assert AgentContext.get("rm-branch") and AgentContext.get("rm-bobchild")


def test_refused_parent_does_not_cascade_into_own_chats(env):
    make(env, "bob", "rm-bobp")
    make(env, "alice", "rm-c", parent="rm-bobp")
    result = many("alice", ["rm-bobp"], include_subagents=True)
    assert result["removed"] == [] and AgentContext.get("rm-c") is not None


def test_running_context_is_stopped(env, monkeypatch):
    context = make(env, "alice", "rm-run")
    stopped = []
    monkeypatch.setattr(context, "reset", lambda: stopped.append(1))
    many("alice", ["rm-run"])
    assert stopped == [1]
    assert env.scheduler.cancelled == [("rm-run", True)]


def test_duplicates_removed_once(env):
    make(env, "alice", "rm-d")
    result = many("alice", ["rm-d", "rm-d"])
    assert result["removed"] == ["rm-d"] and result["failed"] == []
    assert env.persisted == ["rm-d"]


def test_cap_of_200(env):
    ids = [f"x{i}" for i in range(201)]
    with pytest.raises(ValueError, match="at most 200"):
        many("alice", ids)
    assert many("alice", ids[:200])["removed"] == []  # at the cap: accepted, all unknown


def test_bad_payload_rejected(env):
    with pytest.raises(ValueError):
        many("alice", "rm-a")


def test_scheduled_chat_task_removed_and_scheduler_reloaded_once(env):
    make(env, "alice", "rm-s1")
    make(env, "alice", "rm-s2")
    env.scheduler.tasks += [
        SimpleNamespace(context_id="rm-s1", uuid="t1"),
        SimpleNamespace(context_id="rm-keep", uuid="t9"),
    ]
    many("alice", ["rm-s1", "rm-s2"])
    assert env.scheduler.removed == ["t1"]
    assert env.scheduler.reloads == 1 and len(env.dirty) == 1


def test_audit_event_carries_ids_and_counts_only(env, monkeypatch):
    from api import chat_remove_many

    events = []
    monkeypatch.setattr(
        chat_remove_many, "audit", SimpleNamespace(emit=lambda e, **f: events.append((e, f)))
    )
    make(env, "alice", "rm-au")
    many("alice", ["rm-au", "nope"])
    event, fields = events[0]
    assert event == "chat.remove_many"
    assert fields["removed"] == ["rm-au"] and fields["failed"] == ["nope"]
    assert fields["removed_count"] == 1 and fields["failed_count"] == 1
