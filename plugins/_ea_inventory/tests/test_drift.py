"""The drift inbox: devices whose config changed, matched against the approved changes that ran there.

A change explains a device's new backup when it names the device and was applied after the older backup was last
checked and before the newer one was taken, with a few minutes' slack for two clocks.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest

from helpers import api_policy, files, rbac, user_scope, users
from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import APPLIED, ChangeRecord
from plugins._ea_ansible.tests.register import force
from plugins._ea_inventory.api import inventory_drift
from plugins._ea_inventory.helpers import backups

T0 = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc)


def stamp(minutes: int) -> str:
    return (T0 + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def rows(*pairs):
    """Newest first: (id, taken minute, checked minute)."""
    return [{"id": i * 32, "taken_at": stamp(t), "checked_at": stamp(c), "sha256": i, "size": 1} for i, t, c in pairs]


def change(cid: str, minute: int, *names: str) -> dict:
    return {"id": cid, "summary": cid, "devices": set(names), "applied_at": T0 + dt.timedelta(minutes=minute)}


DEVICES = [{"id": f"d-{n}", "name": n} for n in ("sw1", "sw2", "sw3", "sw4")]
BACKUPS = {
    "sw1": rows(("b", 120, 130), ("a", 0, 60)),  # changed between minute 60 and 120
    "sw2": rows(("d", 200, 200), ("c", 0, 100)),  # changed between 100 and 200
    "sw3": rows(("e", 0, 300)),  # one backup: nothing to compare
}


def test_a_change_in_the_window_on_that_device_explains_it_and_others_do_not():
    applied = [
        change("in-window", 90, "sw1"),
        change("wrong-device", 150, "sw9"),
        change("too-early", 30, "sw2"),  # before sw2's older backup was last checked
        change("too-late", 260, "sw2"),  # after sw2's newer backup was taken
    ]
    items = {i["name"]: i for i in backups.drift(DEVICES, BACKUPS, applied)}

    assert set(items) == {"sw1", "sw2"}  # sw3 has one backup, sw4 none
    assert items["sw1"]["explained"] and [c["id"] for c in items["sw1"]["changes"]] == ["in-window"]
    assert not items["sw2"]["explained"] and items["sw2"]["changes"] == []
    assert (items["sw1"]["from"], items["sw1"]["to"]) == ("a" * 32, "b" * 32)  # what the diff view is opened with
    assert "devices" not in items["sw1"]["changes"][0]


def test_the_two_clocks_get_slack_at_both_edges_and_no_more():
    def explained(minute):
        return backups.drift(DEVICES[:1], BACKUPS, [change("c", minute, "sw1")])[0]["explained"]

    assert explained(60 - 4) and explained(120 + 4)  # within five minutes of either edge
    assert not explained(60 - 6) and not explained(120 + 6)


def test_unexplained_come_first_then_the_most_recent():
    items = backups.drift(DEVICES, BACKUPS, [change("c", 90, "sw1")])
    assert [i["name"] for i in items] == ["sw2", "sw1"]
    items = backups.drift(DEVICES, BACKUPS, [])
    assert [i["name"] for i in items] == ["sw2", "sw1"]  # both unexplained: sw2 changed later


@pytest.fixture
def ledger(database, tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setenv(change_store.ENV_KEY, "a-server-secret")
    monkeypatch.setattr(change_store, "audit", None)
    from plugins._ea_ansible.helpers import approvers
    monkeypatch.setattr(approvers, "notify", lambda *a: None)
    return users.create_user("asha", "correcthorsebattery", rbac.ROLE_POWER)["id"]


def record(owner: str, **fields) -> ChangeRecord:
    r = ChangeRecord(requested_by=owner, kind="change", playbook_name="changes/ios_vlan_name", summary="name VLAN 300",
                     limit="sw1, sw2", commands=["vlan 300"], expires_at=time.time() + 600)
    for key, value in fields.items():
        setattr(r, key, value)
    with user_scope.user_scope(owner):
        return force(r, owner)


def test_the_ledger_is_every_users_approved_and_applied_device_change(ledger):
    owner = ledger
    applied_at = time.time() - 60
    record(owner, id="applied1", status=APPLIED, approved_by=owner, approved_hash="h", applied_at=applied_at)
    record(owner, id="notapplied", status="approved", approved_by=owner, approved_hash="h")
    record(owner, id="azure1", kind="azure_change", status=APPLIED, approved_by=owner, approved_hash="h", applied_at=applied_at)
    record(owner, id="plan1", kind="plan", status=APPLIED, approved_by=owner, approved_hash="h", applied_at=applied_at, limit="",
           steps=[{"kind": "read", "limit": "sw7"}, {"kind": "change", "limit": "sw3"}])

    got = {c["id"]: c for c in inventory_drift.applied_changes()}
    assert set(got) == {"applied1", "plan1"}
    assert got["applied1"]["devices"] == {"sw1", "sw2"} and got["applied1"]["approved_by"] == "asha"
    assert got["plan1"]["devices"] == {"sw3"}  # the change step, not the read
    assert got["applied1"]["applied_at"] == dt.datetime.fromtimestamp(applied_at, dt.timezone.utc)


def test_the_inbox_is_an_inventory_read():
    assert api_policy.required_permission_for("plugins/_ea_inventory/inventory_drift") == rbac.PERM_INVENTORY_VIEW


def test_the_inbox_counts_and_says_when_the_executor_is_silent(monkeypatch):
    from plugins._ea_inventory.helpers import store

    monkeypatch.setattr(store, "list_devices", lambda **f: DEVICES)
    monkeypatch.setattr(inventory_drift, "applied_changes", lambda: [change("c", 90, "sw1")])
    monkeypatch.setattr(backups, "list_all", lambda: BACKUPS)
    out = asyncio.run(inventory_drift.InventoryDrift(None, None).process({}, None))
    assert (out["changed"], out["unexplained"]) == (2, 1)

    monkeypatch.setattr(backups, "list_all", lambda: None)
    assert not asyncio.run(inventory_drift.InventoryDrift(None, None).process({}, None))["ok"]


def test_an_executor_that_was_never_set_up_says_so_rather_than_unreachable(monkeypatch):
    from plugins._ea_ansible.helpers import executor_client

    monkeypatch.setattr(backups, "list_all", lambda: None)
    monkeypatch.setattr(executor_client, "setup_problem", lambda agent=None: "Network automation is not configured on this instance.")
    out = asyncio.run(inventory_drift.InventoryDrift(None, None).process({}, None))
    assert not out["ok"] and "not configured" in out["error"]

    monkeypatch.setattr(executor_client, "setup_problem", lambda agent=None: None)
    out = asyncio.run(inventory_drift.InventoryDrift(None, None).process({}, None))
    assert "could not be reached" in out["error"]
