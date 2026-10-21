import asyncio

from helpers import rbac
from plugins._ea_inventory.api.inventory_schedule import InventorySchedule
from plugins._ea_inventory.helpers import schedule, store
from plugins._ea_inventory.helpers.facts import collect, collect_ports


def stub(monkeypatch, fail=False):
    seen = []

    def facts(names, *, actor, **kw):
        seen.append(("facts", list(names), actor))
        if fail:
            raise RuntimeError("executor down")
        return collect.RefreshResult(counts={"ok": len(names), "error": 0, "proposals": 1, "conflicts": 0})

    monkeypatch.setattr(collect, "refresh_facts", facts)
    monkeypatch.setattr(collect_ports, "refresh_ports", lambda names, **kw: seen.append(("ports", list(names))) or {"refreshed": len(names), "failed": []})
    return seen


def test_run_covers_every_active_device_in_chunks_and_is_recorded(make, monkeypatch):
    for i in range(12):
        make(f"sw{i:02}", status="active", platform="ios")
    make("off", status="spare", platform="ios")
    seen = stub(monkeypatch)
    counts = schedule.run()
    assert [(s[0], len(s[1])) for s in seen] == [("facts", 10), ("facts", 2), ("ports", 10), ("ports", 2)]
    assert {s[2] for s in seen if s[0] == "facts"} == {"system:schedule"}
    assert counts["facts_ok"] == 12 and counts["ports_refreshed"] == 12 and counts["proposals"] == 2
    last = store.last_run("schedule")
    assert last["status"] == "succeeded" and last["counts"]["devices"] == 12


def test_a_failure_is_recorded_not_raised(make, monkeypatch):
    make("sw1", status="active", platform="ios")
    stub(monkeypatch, fail=True)
    schedule.run()
    last = store.last_run("schedule")
    assert last["status"] == "failed" and "executor down" in last["error"]


def test_status_off_and_due(database, monkeypatch):
    monkeypatch.setattr(schedule, "interval_hours", lambda: 0)
    assert schedule.status() == {"interval_hours": 0, "last_run": None, "next_run_at": None}
    monkeypatch.setattr(schedule, "interval_hours", lambda: 6)
    ran = []
    monkeypatch.setattr(schedule, "run", lambda: ran.append(1))

    async def tick():
        schedule.start_if_due()
        await schedule._task

    asyncio.run(tick())  # never ran: due now
    assert ran == [1]
    sid = store.start_run("schedule", "system:schedule")
    store.finish_run(sid, "succeeded", {})
    assert schedule.status()["last_run"]["status"] == "succeeded"
    schedule.start_if_due()  # just ran: not due for 6 hours (would raise without a loop if it tried)
    assert ran == [1]


def test_endpoint_needs_view_only():
    assert InventorySchedule.required_permission() == rbac.PERM_INVENTORY_VIEW
