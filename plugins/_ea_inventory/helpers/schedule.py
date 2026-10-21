"""Scheduled refresh: facts, then ports, for every active device on `refresh_interval_hours` (0 = off).

Same code paths as the buttons, as actor `system:schedule`: facts reach devices only through `precedence.plan`
(proposals or conflicts) and ports replace the observed snapshot. One run at a time, recorded as source `schedule`.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any

from plugins._ea_inventory.helpers import ports, store
from plugins._ea_inventory.helpers.facts import collect, collect_ports

SOURCE = "schedule"
ACTOR = "system:schedule"
_task: asyncio.Task | None = None  # here, not in the extension: extension modules are re-executed on every tick


def interval_hours() -> int:
    from helpers import plugins

    merged = {
        **(plugins.get_default_plugin_config("_ea_inventory") or {}),
        **(plugins.get_plugin_config("_ea_inventory") or {}),
    }
    try:
        return max(0, int(merged.get("refresh_interval_hours") or 0))
    except (TypeError, ValueError):
        return 0


def _after(started_at: str | None, hours: int) -> _dt.datetime | None:
    return _dt.datetime.fromisoformat(started_at) + _dt.timedelta(hours=hours) if started_at else None


def status() -> dict[str, Any]:
    hours, last = interval_hours(), store.last_run(SOURCE)
    due = _after(last and last["started_at"], hours) if hours else None
    return {
        "interval_hours": hours, "last_run": last and {k: last[k] for k in ("started_at", "finished_at", "status", "counts")},
        "next_run_at": (due or _dt.datetime.now(_dt.timezone.utc)).isoformat() if hours else None,
    }


def run() -> dict[str, Any]:
    """Blocking. Facts then ports, ten devices at a time (the cap of each collector); returns the run's counts."""
    active = store.list_devices(status="active")
    counts = {"devices": len(active), "facts_ok": 0, "facts_error": 0, "proposals": 0, "conflicts": 0, "ports_refreshed": 0, "ports_failed": 0}
    run_id, error = store.start_run(SOURCE, ACTOR), ""
    try:
        for platforms, step in ((collect.PLATFORMS, "facts"), (collect_ports.PLATFORMS, "ports")):
            names = [d["name"] for d in active if d["platform"] in platforms]
            for i in range(0, len(names), collect.CAP):
                chunk = names[i:i + collect.CAP]
                if step == "facts":
                    got = collect.refresh_facts(chunk, actor=ACTOR).counts
                    counts["facts_ok"] += got["ok"]
                    counts["facts_error"] += got["error"]
                    counts["proposals"] += got["proposals"]
                    counts["conflicts"] += got["conflicts"]
                else:
                    got = collect_ports.refresh_ports(chunk)
                    counts["ports_refreshed"] += got["refreshed"]
                    counts["ports_failed"] += len(got["failed"])
    except Exception as exc:  # noqa: BLE001 - recorded on the run; the loop must go on
        error = str(exc) or type(exc).__name__
    status_ = "failed" if error else "succeeded"
    store.finish_run(run_id, status_, counts, error)
    ports.emit("inventory.scheduled_refresh", requested_by=ACTOR, run_id=run_id, status=status_, **counts)
    return counts


def start_if_due() -> None:
    """Called every job-loop tick: starts a run in the background when one is due and none is in flight."""
    global _task
    hours = interval_hours()
    if not hours or (_task and not _task.done()) or store.running_since(SOURCE, 180):
        return
    last = store.last_run(SOURCE)
    due = _after(last and last["started_at"], hours)
    if due and _dt.datetime.now(_dt.timezone.utc) < due:
        return
    _task = asyncio.create_task(asyncio.to_thread(run))
