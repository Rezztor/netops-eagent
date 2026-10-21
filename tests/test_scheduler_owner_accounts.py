"""A scheduled task runs as its owner, so the owner has to be an account that may run things.

Disabling an account stopped it signing in. Its scheduled tasks went on running
unattended as that account, with its role's tools -- code execution and the
network tools for a power user -- for as long as nobody deleted them. A deleted
account's tasks ran on as well, as nobody.
"""

from types import SimpleNamespace

import pytest

from helpers import task_scheduler, users


class _Started(list):
    """Stands in for DeferredTask: records what would have been started."""

    def __call__(self, thread_name=""):
        return SimpleNamespace(start_task=lambda *args, **kwargs: self.append(args))


@pytest.fixture
def started(database, monkeypatch):
    # The scheduler reads its task list from the database when it is first made.
    record = _Started()
    monkeypatch.setattr(task_scheduler, "DeferredTask", record)
    return record


def _owners(monkeypatch, **accounts):
    monkeypatch.setattr(users, "get_user_by_id", lambda user_id: accounts.get(user_id))


def _task(owner):
    return SimpleNamespace(uuid=f"task-of-{owner}", name="nightly report", owner_id=owner)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner, runs",
    [
        ("active", True),
        ("disabled", False),
        ("deleted", False),
        (None, True),  # the framework's own task, created outside any account
    ],
)
async def test_a_task_runs_only_for_an_owner_who_may_run_things(monkeypatch, started, owner, runs):
    _owners(monkeypatch, active={"id": "active", "disabled": False}, disabled={"id": "disabled", "disabled": True})

    await task_scheduler.TaskScheduler.get()._run_task(_task(owner))

    assert bool(started) is runs


@pytest.mark.asyncio
async def test_a_task_is_not_run_when_its_owner_cannot_be_looked_up(monkeypatch, started):
    """The database being away is not a reason to run somebody's task as them."""

    def unavailable(user_id):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(users, "get_user_by_id", unavailable)

    await task_scheduler.TaskScheduler.get()._run_task(_task("active"))

    assert not started
