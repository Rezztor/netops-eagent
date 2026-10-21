"""Functional behaviour of the personal-schedule API handlers.

Ownership enforcement itself -- the property that makes it safe to open these
to every signed-in account instead of restricting them to scheduler.manage --
is covered in test_user_isolation.py, alongside the analogous guard for
conversations. These tests stub TaskScheduler.get() so they exercise each
handler's process() against real task objects without touching the real
usr/scheduler/tasks.json.
"""

import threading

import pytest

from helpers import user_scope
from helpers.task_scheduler import (
    AdHocTask,
    TaskAccessDenied,
    TaskState,
    TaskScheduler,
    serialize_task,
)

from api.scheduler_my_tasks import SchedulerMyTasks
from api.scheduler_my_task_run import SchedulerMyTaskRun
from api.scheduler_my_task_pause import SchedulerMyTaskPause
from api.scheduler_my_task_resume import SchedulerMyTaskResume
from api.scheduler_my_task_delete import SchedulerMyTaskDelete


class _FakeScheduler:
    """Stands in for TaskScheduler.get(): same surface the handlers call,
    backed by a plain dict instead of usr/scheduler/tasks.json."""

    def __init__(self, tasks):
        self._tasks = {t.uuid: t for t in tasks}
        self.run_calls: list[str] = []
        self.removed: list[str] = []

    async def reload(self):
        pass

    def get_tasks(self):
        return list(self._tasks.values())

    def get_task_by_uuid(self, uuid):
        return self._tasks.get(uuid)

    async def run_task_by_uuid(self, uuid, task_context=None):
        task = self._tasks[uuid]
        if task.state == TaskState.RUNNING:
            raise ValueError(f"Task '{task.name}' is already running")
        if task.state == TaskState.DISABLED:
            raise ValueError(f"Task '{task.name}' is disabled")
        task.state = TaskState.RUNNING
        self.run_calls.append(uuid)

    async def update_task(self, uuid, **kwargs):
        task = self._tasks.get(uuid)
        if not task:
            return None
        for key, value in kwargs.items():
            setattr(task, key, value)
        return task

    def serialize_task(self, uuid):
        task = self._tasks.get(uuid)
        return serialize_task(task) if task else None

    async def remove_task_by_uuid(self, uuid):
        self._tasks.pop(uuid, None)
        self.removed.append(uuid)

    async def save(self):
        pass

    def cancel_running_task(self, uuid, terminate_thread=False):
        return True


def _adhoc(owner_id, uuid, name="reminder", state=TaskState.IDLE):
    task = AdHocTask(
        uuid=uuid,
        name=name,
        system_prompt="",
        prompt="do a thing",
        owner_id=owner_id,
    )
    task.state = state
    return task


def _install(monkeypatch, tasks):
    fake = _FakeScheduler(tasks)
    monkeypatch.setattr(TaskScheduler, "get", classmethod(lambda cls: fake))
    return fake


def _handler(cls):
    return cls(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]


def _delete_handler():
    """A task always carries a dedicated context_id (BaseTask.__init__ defaults
    it to the task's own uuid), so the real handler always calls
    self.use_context(...). Stubbed to report "not currently live" -- the
    common case for a reminder nobody has opened -- rather than pulling in
    AgentContext/initialize_agent for what these tests are actually checking:
    ownership and removal from the scheduler."""
    handler = SchedulerMyTaskDelete(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]
    handler.use_context = lambda ctxid, create_if_not_exists=True: None  # type: ignore[method-assign]
    return handler


@pytest.mark.asyncio
async def test_list_returns_only_the_callers_own_tasks(monkeypatch):
    _install(
        monkeypatch,
        [_adhoc("alice-id", "mine"), _adhoc("bob-id", "theirs"), _adhoc(None, "unowned")],
    )

    with user_scope.user_scope("alice-id"):
        result = await _handler(SchedulerMyTasks).process({}, None)

    assert result["ok"] is True
    assert {t["uuid"] for t in result["tasks"]} == {"mine"}


@pytest.mark.asyncio
async def test_run_starts_an_owned_task(monkeypatch):
    fake = _install(monkeypatch, [_adhoc("alice-id", "mine")])

    with user_scope.user_scope("alice-id"):
        result = await _handler(SchedulerMyTaskRun).process({"task_id": "mine"}, None)

    assert result.get("success") is True
    assert fake.run_calls == ["mine"]


@pytest.mark.asyncio
async def test_run_refuses_someone_elses_task(monkeypatch):
    fake = _install(monkeypatch, [_adhoc("bob-id", "theirs")])

    with user_scope.user_scope("alice-id"):
        with pytest.raises(TaskAccessDenied):
            await _handler(SchedulerMyTaskRun).process({"task_id": "theirs"}, None)

    assert fake.run_calls == []


@pytest.mark.asyncio
async def test_run_refuses_when_already_running(monkeypatch):
    fake = _install(monkeypatch, [_adhoc("alice-id", "mine", state=TaskState.RUNNING)])

    with user_scope.user_scope("alice-id"):
        result = await _handler(SchedulerMyTaskRun).process({"task_id": "mine"}, None)

    assert "already running" in result["error"]
    assert fake.run_calls == []


@pytest.mark.asyncio
async def test_run_requires_a_task_id():
    result = await _handler(SchedulerMyTaskRun).process({}, None)
    assert "task_id" in result["error"]


@pytest.mark.asyncio
async def test_pause_disables_an_owned_task(monkeypatch):
    _install(monkeypatch, [_adhoc("alice-id", "mine")])

    with user_scope.user_scope("alice-id"):
        result = await _handler(SchedulerMyTaskPause).process({"task_id": "mine"}, None)

    assert result["ok"] is True
    assert result["task"]["state"] == TaskState.DISABLED


@pytest.mark.asyncio
async def test_pause_refuses_someone_elses_task(monkeypatch):
    _install(monkeypatch, [_adhoc("bob-id", "theirs")])

    with user_scope.user_scope("alice-id"):
        with pytest.raises(TaskAccessDenied):
            await _handler(SchedulerMyTaskPause).process({"task_id": "theirs"}, None)


@pytest.mark.asyncio
async def test_resume_clears_a_paused_task(monkeypatch):
    _install(monkeypatch, [_adhoc("alice-id", "mine", state=TaskState.DISABLED)])

    with user_scope.user_scope("alice-id"):
        result = await _handler(SchedulerMyTaskResume).process({"task_id": "mine"}, None)

    assert result["ok"] is True
    assert result["task"]["state"] == TaskState.IDLE


@pytest.mark.asyncio
async def test_resume_refuses_a_task_that_is_not_paused(monkeypatch):
    fake = _install(monkeypatch, [_adhoc("alice-id", "mine", state=TaskState.IDLE)])

    with user_scope.user_scope("alice-id"):
        result = await _handler(SchedulerMyTaskResume).process({"task_id": "mine"}, None)

    assert "not paused" in result["error"]
    assert fake._tasks["mine"].state == TaskState.IDLE


@pytest.mark.asyncio
async def test_resume_refuses_someone_elses_task(monkeypatch):
    _install(monkeypatch, [_adhoc("bob-id", "theirs", state=TaskState.DISABLED)])

    with user_scope.user_scope("alice-id"):
        with pytest.raises(TaskAccessDenied):
            await _handler(SchedulerMyTaskResume).process({"task_id": "theirs"}, None)


@pytest.mark.asyncio
async def test_delete_removes_an_owned_task(monkeypatch):
    fake = _install(monkeypatch, [_adhoc("alice-id", "mine")])

    with user_scope.user_scope("alice-id"):
        result = await _delete_handler().process({"task_id": "mine"}, None)

    assert result["success"] is True
    assert fake.removed == ["mine"]


@pytest.mark.asyncio
async def test_delete_refuses_someone_elses_task(monkeypatch):
    fake = _install(monkeypatch, [_adhoc("bob-id", "theirs")])

    with user_scope.user_scope("alice-id"):
        with pytest.raises(TaskAccessDenied):
            await _delete_handler().process({"task_id": "theirs"}, None)

    assert fake.removed == []


# --- The scheduler.manage screen: a task is its owner's, or an administrator's ---


def _roles(monkeypatch, roles):
    from helpers import users

    monkeypatch.setattr(
        users, "get_user_by_id", lambda uid: {"id": uid, "role": roles.get(uid, "user")}
    )


@pytest.mark.asyncio
async def test_power_user_cannot_edit_run_or_delete_an_administrators_task(monkeypatch):
    """A task runs as its owner, so editing someone else's is running as them."""
    from api.scheduler_task_delete import SchedulerTaskDelete
    from api.scheduler_task_run import SchedulerTaskRun
    from api.scheduler_task_update import SchedulerTaskUpdate

    fake = _install(monkeypatch, [_adhoc("admin-id", "theirs")])
    _roles(monkeypatch, {"admin-id": "admin", "power-id": "power"})

    with user_scope.user_scope("power-id"):
        for cls, body in (
            (SchedulerTaskUpdate, {"task_id": "theirs", "prompt": "as the admin"}),
            (SchedulerTaskRun, {"task_id": "theirs"}),
            (SchedulerTaskDelete, {"task_id": "theirs"}),
        ):
            with pytest.raises(TaskAccessDenied):
                await _handler(cls).process(body, None)

    assert fake.get_task_by_uuid("theirs").prompt == "do a thing"
    assert fake.run_calls == [] and fake.removed == []


@pytest.mark.asyncio
async def test_manage_list_shows_own_tasks_and_everything_to_an_administrator(monkeypatch):
    from api.scheduler_tasks_list import SchedulerTasksList

    _install(
        monkeypatch,
        [_adhoc("admin-id", "a"), _adhoc("power-id", "p"), _adhoc(None, "ownerless")],
    )
    _roles(monkeypatch, {"admin-id": "admin", "power-id": "power"})

    with user_scope.user_scope("power-id"):
        mine = await _handler(SchedulerTasksList).process({}, None)
    with user_scope.user_scope("admin-id"):
        everything = await _handler(SchedulerTasksList).process({}, None)

    assert [t["uuid"] for t in mine["tasks"]] == ["p"]
    assert sorted(t["uuid"] for t in everything["tasks"]) == ["a", "ownerless", "p"]


@pytest.mark.asyncio
async def test_agent_scheduler_tool_sees_only_the_users_own_tasks(monkeypatch):
    from tools import scheduler as scheduler_tool

    _install(monkeypatch, [_adhoc("admin-id", "theirs"), _adhoc("power-id", "mine")])
    _roles(monkeypatch, {"admin-id": "admin", "power-id": "power"})

    with user_scope.user_scope("power-id"):
        assert scheduler_tool._task("theirs") is None
        assert scheduler_tool._task("mine").uuid == "mine"
        assert [t.uuid for t in scheduler_tool._visible(TaskScheduler.get().get_tasks())] == ["mine"]
    with user_scope.user_scope("admin-id"):
        assert scheduler_tool._task("mine").uuid == "mine"
