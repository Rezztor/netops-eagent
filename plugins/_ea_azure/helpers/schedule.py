"""When an Azure pull is due: `off`, `daily` or `weekly` at a time in a time zone, as `system:schedule`.

`next_due` and `upcoming` are pure. The job-loop extension only calls `start_if_due()`; extension modules are
re-executed each tick, so all state lives in the pull record, not here.
"""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

from plugins._ea_azure.helpers import collect, config
from plugins._ea_inventory.helpers import store as inv

ACTOR = "system:schedule"
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _at(day: _dt.date, hhmm: str, zone: ZoneInfo) -> _dt.datetime:
    return _dt.datetime.combine(day, _dt.time.fromisoformat(hhmm), tzinfo=zone)


def _slots(cfg: dict, now: _dt.datetime) -> tuple[_dt.datetime, _dt.datetime] | None:
    """(the latest scheduled instant not after `now`, the first one after it), or None when the schedule is off."""
    s = cfg["schedule"]
    if s["frequency"] not in ("daily", "weekly"):
        return None
    zone = ZoneInfo(s["timezone"])
    local = now.astimezone(zone)
    step = 1 if s["frequency"] == "daily" else 7
    back = 0 if step == 1 else (local.weekday() - _WEEKDAYS.index(s["weekday"])) % 7
    day = local.date() - _dt.timedelta(days=back)
    if _at(day, s["time"], zone) > local:
        day -= _dt.timedelta(days=step)
    return _at(day, s["time"], zone), _at(day + _dt.timedelta(days=step), s["time"], zone)


def next_due(cfg: dict, last_started: _dt.datetime | None, now: _dt.datetime) -> _dt.datetime | None:
    """The most recent scheduled instant not after `now`, when a pull is due for it (the last one started before
    it); otherwise None. Off is None."""
    slots = _slots(cfg, now)
    if slots and (last_started is None or last_started < slots[0]):
        return slots[0]
    return None


def upcoming(cfg: dict, now: _dt.datetime) -> _dt.datetime | None:
    """The next scheduled instant after `now`, or None when the schedule is off."""
    slots = _slots(cfg, now)
    return slots[1] if slots else None


def start_if_due() -> None:
    """Called every job-loop tick: starts a pull in the background when one is due and none is running."""
    if not (config.is_enabled() and config.is_configured()):
        return
    last = inv.last_run(collect.SOURCE)
    if not last:  # the first pull is the administrator's, after Test connection; the schedule takes over from it
        return
    started = _dt.datetime.fromisoformat(last["started_at"])
    if next_due(config.get_config(), started, _dt.datetime.now(_dt.timezone.utc)) is None:
        return
    try:
        collect.start("schedule", ACTOR)
    except (collect.Busy, collect.TooSoon):
        pass
