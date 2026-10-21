import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import task_scheduler
from helpers.task_scheduler import AdHocTask, ScheduledTask, TaskSchedule


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 5, 9, 10, 0, tzinfo=timezone.utc)
        if tz is None:
            return value.replace(tzinfo=None)
        return value.astimezone(tz)


def test_scheduled_task_next_run_uses_schedule_timezone(monkeypatch):
    monkeypatch.setattr(task_scheduler, "datetime", FixedDateTime)
    task = ScheduledTask.create(
        name="rome morning",
        system_prompt="",
        prompt="remind me",
        schedule=TaskSchedule(
            minute="30",
            hour="9",
            day="10",
            month="5",
            weekday="*",
            timezone="Europe/Rome",
        ),
        timezone="Europe/Rome",
    )

    assert task.get_next_run() == datetime(2026, 5, 10, 7, 30, tzinfo=timezone.utc)


def test_scheduled_task_normalizes_legacy_local_timezone(monkeypatch):
    monkeypatch.setattr(task_scheduler, "datetime", FixedDateTime)
    monkeypatch.setattr(
        task_scheduler,
        "Localization",
        SimpleNamespace(get=lambda: SimpleNamespace(get_timezone=lambda: "Europe/Rome")),
    )
    task = ScheduledTask.create(
        name="legacy local",
        system_prompt="",
        prompt="remind me",
        schedule=TaskSchedule(
            minute="30",
            hour="9",
            day="10",
            month="5",
            weekday="*",
            timezone="local",
        ),
    )

    assert task.schedule.timezone == "Europe/Rome"
    assert task.get_next_run() == datetime(2026, 5, 10, 7, 30, tzinfo=timezone.utc)


def test_scheduler_missing_dedicated_context_logs_info(monkeypatch):
    calls = []

    class FakePrintStyle:
        @staticmethod
        def info(message):
            calls.append(("info", message))

        @staticmethod
        def warning(message):
            calls.append(("warning", message))

    class FakeAgentContext:
        @staticmethod
        def get(_context_id):
            return None

        def __init__(self, _config, id, name):
            self.id = id
            self.name = name

    monkeypatch.setattr(task_scheduler, "PrintStyle", FakePrintStyle)
    monkeypatch.setattr(task_scheduler, "AgentContext", FakeAgentContext)
    monkeypatch.setattr(task_scheduler, "initialize_agent", lambda: object())
    monkeypatch.setattr(task_scheduler, "save_tmp_chat", lambda _context: None)
    monkeypatch.setattr(
        task_scheduler.projects, "activate_project", lambda *_args, **_kwargs: None
    )

    task = AdHocTask.create(
        name="dedicated",
        system_prompt="",
        prompt="run this",
        token="123",
    )
    scheduler = object.__new__(task_scheduler.TaskScheduler)

    context = asyncio.run(scheduler._get_chat_context(task))

    assert context.id == task.context_id
    assert len(calls) == 1
    level, message = calls[0]
    assert level == "info"
    assert "creating dedicated context" in message


def test_scheduler_missing_shared_context_still_logs_warning(monkeypatch):
    calls = []

    class FakePrintStyle:
        @staticmethod
        def info(message):
            calls.append(("info", message))

        @staticmethod
        def warning(message):
            calls.append(("warning", message))

    class FakeAgentContext:
        @staticmethod
        def get(_context_id):
            return None

        def __init__(self, _config, id, name):
            self.id = id
            self.name = name

    monkeypatch.setattr(task_scheduler, "PrintStyle", FakePrintStyle)
    monkeypatch.setattr(task_scheduler, "AgentContext", FakeAgentContext)
    monkeypatch.setattr(task_scheduler, "initialize_agent", lambda: object())
    monkeypatch.setattr(task_scheduler, "save_tmp_chat", lambda _context: None)
    monkeypatch.setattr(
        task_scheduler.projects, "activate_project", lambda *_args, **_kwargs: None
    )

    task = AdHocTask.create(
        name="shared",
        system_prompt="",
        prompt="run this",
        token="123",
        context_id="shared-context",
    )
    scheduler = object.__new__(task_scheduler.TaskScheduler)

    context = asyncio.run(scheduler._get_chat_context(task))

    assert context.id == task.context_id
    assert len(calls) == 1
    level, message = calls[0]
    assert level == "warning"
    assert "context not found" in message


# --- Task ownership ---------------------------------------------------------


def test_a_task_records_who_created_it():
    """Scheduled runs happen on the job loop, which has no request session.

    Without an owner on the task there is no user scope on that thread: audit
    records a null actor, and anything resolving a per-user path resolves it
    against nothing. The interactive path already carries the owner across the
    thread boundary explicitly (AgentContext.run_task); this is the same idea for
    the scheduler.
    """
    from helpers import task_scheduler, user_scope

    with user_scope.user_scope("alice"):
        task = task_scheduler.AdHocTask.create(
            name="t", system_prompt="s", prompt="p", token="tok"
        )
    assert task.owner_id == "alice"


def test_a_task_created_outside_a_user_scope_has_no_invented_owner():
    """Recording none is better than guessing.

    Tasks created before this field existed also load with no owner, and run
    unscoped exactly as they did before -- the field is optional so that history
    stays readable rather than failing to parse.
    """
    from helpers import task_scheduler

    task = task_scheduler.AdHocTask.create(
        name="t", system_prompt="s", prompt="p", token="tok"
    )
    assert task.owner_id is None


def test_the_scheduled_run_enters_the_owners_scope():
    """The fix is only useful if the job loop actually pushes the scope.

    Asserted against the source because exercising a real scheduled run needs the
    job loop, a context and a model -- and the thing worth protecting is the one
    line that would be quietly dropped in a refactor.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "helpers" / "task_scheduler.py").read_text()
    assert "user_scope.user_scope(owner)" in source
