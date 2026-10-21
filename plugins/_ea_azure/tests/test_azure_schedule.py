"""When a pull is due. Pure; no database, no network."""

import datetime as dt

import pytest

from plugins._ea_azure.helpers import collect, config, schedule

UTC = dt.timezone.utc


def at(day, hour=0, minute=0):
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=UTC)  # 3 October 2026 is a Saturday


def cfg(frequency="weekly", weekday="sat", time="02:00", timezone="UTC"):
    return {"schedule": {"frequency": frequency, "weekday": weekday, "time": time, "timezone": timezone}}


def test_off_is_never_due():
    assert schedule.next_due(cfg("off"), None, at(5)) is None and schedule.upcoming(cfg("off"), at(5)) is None


def test_weekly_is_due_after_the_slot_until_a_pull_starts_after_it():
    assert schedule.next_due(cfg(), at(2), at(3, 1, 59)) is None              # last Saturday's slot was served by Friday's pull
    assert schedule.next_due(cfg(), at(2) - dt.timedelta(days=7), at(3, 1, 59)) == at(3, 2) - dt.timedelta(days=7)
    assert schedule.next_due(cfg(), at(2), at(3, 2, 0)) == at(3, 2)
    assert schedule.next_due(cfg(), at(3, 2, 5), at(3, 9)) is None
    assert schedule.next_due(cfg(), at(3, 2, 5), at(9, 1)) is None            # the next Saturday has not come
    assert schedule.next_due(cfg(), at(3, 2, 5), at(10, 2, 1)) == at(10, 2)
    assert schedule.upcoming(cfg(), at(5, 12)) == at(10, 2)


def test_a_missed_slot_is_still_due_days_later():
    assert schedule.next_due(cfg(), at(3, 2, 5) - dt.timedelta(days=7), at(7, 9)) == at(3, 2)   # the server was down on Saturday


def test_never_pulled_is_due():
    assert schedule.next_due(cfg(), None, at(5)) == at(3, 2)


def test_daily():
    assert schedule.next_due(cfg("daily"), at(4, 2, 1), at(5, 1)) is None
    assert schedule.next_due(cfg("daily"), at(4, 2, 1), at(5, 2)) == at(5, 2)
    assert schedule.upcoming(cfg("daily"), at(5, 2)) == at(6, 2)


def test_the_time_zone_moves_the_slot():
    dubai = cfg(timezone="Asia/Dubai")                              # Saturday 02:00 there is Friday 22:00 UTC
    assert schedule.next_due(dubai, None, at(2, 21, 30)) == dt.datetime(2026, 9, 26, 2, tzinfo=dt.timezone(dt.timedelta(hours=4)))
    due = schedule.next_due(dubai, None, at(2, 22, 30))
    assert due.astimezone(UTC) == at(2, 22) and due.tzinfo.key == "Asia/Dubai"
    assert schedule.upcoming(dubai, at(2, 22, 30)).astimezone(UTC) == at(9, 22)


def test_a_clock_change_does_not_break_a_daily_slot():
    berlin = cfg("daily", time="02:30", timezone="Europe/Berlin")   # 02:30 does not exist on 29 March 2026
    now = dt.datetime(2026, 3, 29, 12, tzinfo=UTC)
    assert schedule.next_due(berlin, None, now).date() == dt.date(2026, 3, 29)
    assert schedule.upcoming(berlin, now).date() == dt.date(2026, 3, 30)


@pytest.fixture
def ready(monkeypatch):
    started = []
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    monkeypatch.setattr(config, "is_configured", lambda: True)
    monkeypatch.setattr(config, "get_config", lambda: cfg())
    monkeypatch.setattr(collect, "start", lambda trigger, actor: started.append((trigger, actor)) or "run")
    return started


def last(monkeypatch, when):
    monkeypatch.setattr(schedule.inv, "last_run", lambda source: when and {"started_at": when.isoformat()})


def test_start_if_due_starts_one_as_the_schedule(ready, monkeypatch):
    now = dt.datetime.now(UTC)
    slot = schedule.next_due(cfg("daily"), None, now)
    monkeypatch.setattr(config, "get_config", lambda: cfg("daily", time=now.strftime("%H:%M")))
    last(monkeypatch, None)
    schedule.start_if_due()
    assert ready == []  # never pulled: the first pull is the administrator's
    last(monkeypatch, now - dt.timedelta(days=2))
    schedule.start_if_due()
    assert ready == [("schedule", "system:schedule")] and slot


def test_start_if_due_is_quiet_when_off_recent_busy_or_not_set_up(ready, monkeypatch):
    now = dt.datetime.now(UTC)
    monkeypatch.setattr(config, "get_config", lambda: cfg("daily", time=now.strftime("%H:%M")))
    last(monkeypatch, now + dt.timedelta(seconds=1))
    schedule.start_if_due()
    last(monkeypatch, None)
    monkeypatch.setattr(config, "get_config", lambda: cfg("off"))
    schedule.start_if_due()
    monkeypatch.setattr(config, "get_config", lambda: cfg("daily", time=now.strftime("%H:%M")))
    monkeypatch.setattr(config, "is_configured", lambda: False)
    schedule.start_if_due()
    assert ready == []
    monkeypatch.setattr(config, "is_configured", lambda: True)

    def busy(trigger, actor):
        raise collect.Busy("running")

    monkeypatch.setattr(collect, "start", busy)
    schedule.start_if_due()  # a pull already running is not an error
