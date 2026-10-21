"""The real scheduler store, against the database.

`SchedulerTaskList.save()` ended with a file-era check on an undefined `path`,
so every save raised `NameError` after it had committed. The job loop wrote a
task RUNNING and then died before its `try`, so the task stayed RUNNING for
good. `tests/test_scheduler_my_tasks.py` stubs `save()`, which is how that went
unseen; these tests use the real one.
"""

from __future__ import annotations

import asyncio
import os
import time


def _stored_state(uuid: str) -> str | None:
    from helpers import db

    with db.unscoped_transaction() as conn:
        row = conn.execute("SELECT state FROM scheduled_tasks WHERE uuid = %s", (uuid,)).fetchone()
    return row[0] if row else None


def _task(**kwargs):
    from helpers.task_scheduler import AdHocTask

    return AdHocTask.create(name="nightly", system_prompt="", prompt="check", token="1234567890", **kwargs)


def test_save_writes_the_task_and_returns(database):
    from helpers.task_scheduler import SchedulerTaskList

    task = _task()
    asyncio.run(SchedulerTaskList.get().add_task(task))

    assert _stored_state(task.uuid) == "idle"


def test_a_run_that_fails_does_not_stay_running(database, monkeypatch):
    from helpers.task_scheduler import SchedulerTaskList, TaskScheduler

    monkeypatch.setattr(TaskScheduler, "_instance", None)
    scheduler = TaskScheduler.get()
    task = _task()
    asyncio.run(SchedulerTaskList.get().add_task(task))

    async def no_context(_task):
        raise RuntimeError("no context in this test")

    monkeypatch.setattr(scheduler, "_get_chat_context", no_context)
    asyncio.run(scheduler._run_task(task))
    deadline = time.monotonic() + 20
    while scheduler._running_deferred_tasks and time.monotonic() < deadline:
        time.sleep(0.05)

    assert not scheduler._running_deferred_tasks
    assert _stored_state(task.uuid) == "error"


def test_a_new_scheduler_resets_tasks_left_running(database, monkeypatch):
    from helpers.task_scheduler import SchedulerTaskList, TaskScheduler, TaskState

    task = _task()
    task.state = TaskState.RUNNING
    asyncio.run(SchedulerTaskList.get().add_task(task))
    assert _stored_state(task.uuid) == "running"

    monkeypatch.setattr(TaskScheduler, "_instance", None)
    TaskScheduler.get()

    assert _stored_state(task.uuid) == "idle"


def test_attachments_are_confined_to_the_owners_folders(database, tmp_path, monkeypatch):
    from helpers import files, settings, user_scope
    from helpers.task_scheduler import usable_attachments
    from testsupport import build_instance_root

    monkeypatch.setattr(files, "_base_dir", build_instance_root(tmp_path))
    monkeypatch.setattr(settings, "_settings", None)
    own = user_scope.get_user_path(user_scope.UPLOADS_DIR, "report.txt", user_id="alice-id")
    other = user_scope.get_user_path(user_scope.UPLOADS_DIR, "secret.txt", user_id="bob-id")
    for path in (own, other):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        files.write_file(path, "x")
    env = files.get_abs_path("usr/.env")
    files.write_file(env, "KEY=value\n")

    task = _task(attachments=[own, other, env, "https://example.com/a.pdf", own + ".missing"])
    task.owner_id = "alice-id"

    assert usable_attachments(task) == [os.path.realpath(own), "https://example.com/a.pdf"]
    task.owner_id = None
    assert usable_attachments(task) == ["https://example.com/a.pdf"]
