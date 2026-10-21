"""An administrator can delete another account's task after it has run.

The task's conversation belongs to the task's owner. The handler resolved it as
the session user, so for an administrator it answered "Context not found" for
every task that had run, and the delete button did nothing.
"""

import threading
from types import SimpleNamespace

import pytest

from agent import AgentContext
from api.scheduler_task_delete import SchedulerTaskDelete
from helpers import persist_chat, user_scope, users
from helpers.task_scheduler import AdHocTask, TaskScheduler, TaskState


class _Scheduler:
    def __init__(self, task):
        self.task, self.removed = task, []

    async def reload(self):
        pass

    def get_task_by_uuid(self, uuid):
        return self.task if uuid == self.task.uuid else None

    def cancel_running_task(self, uuid, terminate_thread=False):
        return True

    async def update_task(self, uuid, **kwargs):
        for key, value in kwargs.items():
            setattr(self.task, key, value)

    async def save(self):
        pass

    async def remove_task_by_uuid(self, uuid):
        self.removed.append(uuid)


@pytest.mark.asyncio
async def test_an_administrator_deletes_a_task_another_account_has_run(monkeypatch):
    task = AdHocTask(uuid="t1", name="nightly", system_prompt="", prompt="p", owner_id="power-id")
    task.state = TaskState.RUNNING
    scheduler = _Scheduler(task)
    monkeypatch.setattr(TaskScheduler, "get", classmethod(lambda cls: scheduler))
    monkeypatch.setattr(users, "holds_permission", lambda uid, perm: uid == "admin-id")

    reset, looked_up_as, removed_chats = [], [], []
    context = SimpleNamespace(
        id="t1", owner_id="power-id", reset=lambda: reset.append(True),
        is_owned_by=lambda uid: uid == "power-id",
    )

    def get(ctxid):
        looked_up_as.append(user_scope.get_current_user_id())
        return context if ctxid == "t1" else None

    monkeypatch.setattr(AgentContext, "get", staticmethod(get))
    monkeypatch.setattr(AgentContext, "remove", staticmethod(lambda ctxid: None))
    monkeypatch.setattr(
        persist_chat, "remove_chat", lambda ctxid: removed_chats.append((ctxid, user_scope.get_current_user_id()))
    )

    handler = SchedulerTaskDelete(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]
    with user_scope.user_scope("admin-id"):
        result = await handler.process({"task_id": "t1"}, None)

    assert result["success"] is True
    assert scheduler.removed == ["t1"]
    assert looked_up_as and set(looked_up_as) == {"power-id"}  # always as the owner
    assert reset == [True]
    assert removed_chats == [("t1", "power-id")]
