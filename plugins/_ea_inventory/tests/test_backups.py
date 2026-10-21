import asyncio
import datetime as dt

import pytest

from helpers import api_policy, rbac
from plugins._ea_ansible.helpers import executor_client
from plugins._ea_inventory.api.inventory_backup_diff import InventoryBackupDiff
from plugins._ea_inventory.api.inventory_backup_report import InventoryBackupReport
from plugins._ea_inventory.api.inventory_backup_run import InventoryBackupRun
from plugins._ea_inventory.api.inventory_backup_view import InventoryBackupView
from plugins._ea_inventory.api.inventory_backups import InventoryBackups
from plugins._ea_inventory.api.inventory_device import InventoryDevice
from plugins._ea_inventory.helpers import backups, proposals, reports

# The handlers read the real clock, so a fixed date here turns every backup stale a week later.
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
ID = "a" * 32


def row(days_ago, taken_days_ago=None):
    at = lambda d: (NOW - dt.timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    return {"id": ID, "taken_at": at(taken_days_ago if taken_days_ago is not None else days_ago), "checked_at": at(days_ago), "sha256": "x", "size": 3}


def call(handler, payload):
    return asyncio.new_event_loop().run_until_complete(handler(app=None, thread_lock=None).process(payload, None))


class Executor:
    """Stands in for executor_client's backup calls."""

    def __init__(self, monkeypatch):
        self.down, self.calls, self.stored = False, [], {}
        for name in ("backup_run", "backup_list", "backup_text", "backup_diff"):
            monkeypatch.setattr(executor_client, name, self._wrap(name))

    def _wrap(self, name):
        def call(agent, *args):
            self.calls.append((name, *args))
            if self.down:
                raise executor_client.ExecutorUnreachable("down")
            if name == "backup_run":
                return {h: {"ok": h != "bad", "id": ID, "changed": True, "error": None if h != "bad" else "unreachable"} for h in args[0]}
            if name == "backup_list":
                return {h: v for h, v in self.stored.items() if not args or h == args[0]}
            if name == "backup_text":
                return "hostname sw1\n"
            return {"diff": "-a\n+b", "changed_lines": 2}
        return call

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def executor(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")
    return Executor(monkeypatch)


def test_state_counts_from_the_last_check_not_the_last_change():
    assert backups.state([], NOW, 7) == {"taken_at": None, "checked_at": None, "age_days": None, "state": "none"}
    assert backups.state([row(1, taken_days_ago=30)], NOW, 7)["state"] == "ok"
    assert backups.state([row(8)], NOW, 7)["state"] == "stale" and backups.state([row(7)], NOW, 7)["state"] == "ok"
    assert backups.state([row(0)], NOW, 7)["age_days"] == 0


def test_report_lists_active_devices_without_a_fresh_backup_worst_first():
    devs = [{"id": n, "name": n, "status": s} for n, s in (("old", "active"), ("new", "active"), ("never", "active"), ("fresh", "active"), ("spare", "spare"))]
    found = {"old": [row(20)], "new": [row(9)], "fresh": [row(1)], "spare": [row(99)]}
    assert [(i["name"], i["state"]) for i in backups.report(devs, found, NOW, 7)] == [("never", "none"), ("old", "stale"), ("new", "stale")]


def test_permissions():
    view, manage = rbac.PERM_INVENTORY_VIEW, rbac.PERM_INVENTORY_MANAGE
    expect = {"backups": view, "backup_report": view, "backup_run": manage, "backup_view": manage, "backup_diff": manage}
    assert {k: api_policy.required_permission_for(f"plugins/_ea_inventory/inventory_{k}") for k in expect} == expect


def test_run_backs_up_up_to_ten_and_audits_names_and_counts_only(make, executor, events):
    a, b = make("sw1"), make("bad")
    out = call(InventoryBackupRun, {"device_ids": [a["id"], b["id"], a["id"]]})
    assert out["ok"] and [(r["name"], r["ok"], r["error"]) for r in out["results"]] == [("sw1", True, None), ("bad", False, "unreachable")]
    assert executor.calls == [("backup_run", ["bad", "sw1"])]
    assert ("inventory.backup_run", {"devices": ["sw1", "bad"], "ok": 1, "failed": 1, "changed": 2, "requested_by": "alice"}) in events
    assert call(InventoryBackupRun, {"device_ids": []})["ok"] is False
    assert call(InventoryBackupRun, {"device_ids": [str(i) for i in range(11)]})["ok"] is False
    assert call(InventoryBackupRun, {"device_ids": ["not-a-device"]})["ok"] is False
    executor.down = True
    assert call(InventoryBackupRun, {"device_ids": [a["id"]]}) == {"ok": False, "error": "down"}


def test_view_diff_and_listing_ask_for_the_redacted_form_and_refuse_a_bad_id(make, executor):
    a = make("sw1")
    executor.stored = {"sw1": [row(1)]}
    assert call(InventoryBackups, {"device_id": a["id"]}) == {"ok": True, "backups": [row(1)]}
    assert call(InventoryBackupView, {"device_id": a["id"], "id": ID}) == {"ok": True, "text": "hostname sw1\n"}
    assert call(InventoryBackupDiff, {"device_id": a["id"], "from": ID, "to": "b" * 32}) == {"ok": True, "diff": "-a\n+b", "changed_lines": 2}
    before = len(executor.calls)
    for bad in ("../x", "", "A" * 32):
        assert call(InventoryBackupView, {"device_id": a["id"], "id": bad})["ok"] is False
    assert call(InventoryBackupDiff, {"device_id": a["id"], "from": ID, "to": "../x"})["ok"] is False
    assert call(InventoryBackups, {"device_id": "nope"})["ok"] is False and len(executor.calls) == before


def test_computed_carries_backup_and_goes_null_when_the_executor_is_silent(make, executor):
    a = make("sw1")
    executor.stored = {"sw1": [row(1)]}
    assert call(InventoryDevice, {"id": a["id"]})["device"]["computed"]["backup"]["state"] == "ok"
    executor.down = True
    assert call(InventoryDevice, {"id": a["id"]})["device"]["computed"]["backup"] is None
    assert reports.computed({"platform": "", "sources": [], "status": "active", "name": "x"}, today=NOW.date(), required=[], stale_days=7)["backup"] is None


def test_report_endpoint(make, executor):
    make("sw1", status="active"), make("sw2", status="active")
    executor.stored = {"sw1": [row(1)]}
    out = call(InventoryBackupReport, {})
    assert out["stale_days"] == 7 and [(i["name"], i["state"]) for i in out["items"]] == [("sw2", "none")]
    executor.down = True
    assert call(InventoryBackupReport, {})["ok"] is False


def test_client_sends_the_routes_and_never_asks_for_raw_text(monkeypatch):
    sent = []

    class Reply:
        status_code = 200
        def json(self): return {"results": {"h": {"ok": True}}, "backups": {"h": []}, "text": "t", "diff": "d", "changed_lines": 1}

    monkeypatch.setattr(executor_client, "_base", lambda agent: "http://x")
    monkeypatch.setattr(executor_client, "_headers", lambda agent: {})
    monkeypatch.setattr(executor_client.requests, "request", lambda method, url, **kw: sent.append((method, url, kw.get("params"), kw.get("json"))) or Reply())
    assert executor_client.backup_run(None, ["h"]) == {"h": {"ok": True}}
    assert executor_client.backup_list(None, "h") == {"h": []} and executor_client.backup_list(None) == {"h": []}
    assert executor_client.backup_text(None, "h", ID) == "t"
    assert executor_client.backup_diff(None, "h", ID, ID)["changed_lines"] == 1
    assert sent == [
        ("POST", "http://x/v1/backups", None, {"hosts": ["h"]}), ("GET", "http://x/v1/backups", {"host": "h"}, None),
        ("GET", "http://x/v1/backups", None, None), ("GET", f"http://x/v1/backups/h/{ID}", None, None),
        ("GET", "http://x/v1/backups/h/diff", {"from": ID, "to": ID}, None),
    ]
