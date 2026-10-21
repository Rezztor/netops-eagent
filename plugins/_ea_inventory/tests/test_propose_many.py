import asyncio

import pytest

from plugins._ea_inventory.api.inventory_propose_many import InventoryProposeMany
from plugins._ea_inventory.helpers import places, proposals, store


def call(payload):
    handler = InventoryProposeMany(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process(payload, None))


@pytest.fixture(autouse=True)
def who(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")


def test_one_batch_nothing_applied_until_approved(make, events):
    a, b = make("sw-a", role="core"), make("sw-b")
    out = call({"device_ids": [a["id"], b["id"]], "fields": {"role": "core"}, "note": "n"})
    assert (out["ok"], out["created"], out["unchanged"], out["failed"]) == (True, 1, 1, [])  # a already core
    pending, total, _ = proposals.list_proposals(status="pending", batch_id=out["batch_id"])
    assert total == 1 and pending[0]["evidence"]["file"] == "Bulk edit" and pending[0]["source"] == "manual"
    assert store.get_device(b["id"])["role"] == ""
    assert events[-1] == ("inventory.bulk_proposed", {
        "batch_id": out["batch_id"], "created": 1, "unchanged": 1, "failed": 0, "requested_by": "alice"})


def test_refused_fields_cap_and_failures(make, events):
    a = make("sw-a")
    for field in ("name", "serial", "mgmt_ip", "asset_tag"):
        assert call({"device_ids": [a["id"]], "fields": {field: "x"}})["ok"] is False
    assert call({"device_ids": [a["id"]], "fields": {"nope": "x"}})["ok"] is False
    assert call({"device_ids": [a["id"]], "fields": {}})["ok"] is False
    assert call({"device_ids": [], "fields": {"role": "x"}})["ok"] is False
    assert call({"device_ids": [a["id"]] * 501, "fields": {"role": "x"}})["ok"] is False
    out = call({"device_ids": [a["id"], "missing"], "fields": {"role": "edge"}})
    assert out["created"] == 1 and [f["device_id"] for f in out["failed"]] == ["missing"]


def test_bulk_move_sends_one_place_and_clears_the_units_it_leaves(make, events):
    places.create(None, "HQ", "HQ", chain=[("Main", "M"), ("Main", "M")])
    places.create("HQ-M-M", "Level 1", "L1", chain=[("Room 1", "R01"), ("Rack 1", "RK01")])
    rack, room = "HQ-M-M-L1-R01-RK01", "HQ-M-M-L1-R01"
    a, b = make("sw-a", place_id=rack, rack_unit=3), make("sw-b", place_id=room)
    out = call({"device_ids": [a["id"], b["id"]], "fields": {"place_id": room.lower()}})
    assert (out["created"], out["unchanged"], out["failed"]) == (1, 1, [])
    (prop,), _, _ = proposals.list_proposals(status="pending", batch_id=out["batch_id"])
    assert prop["changes"] == {"place_id": {"old": rack, "new": room}, "rack_unit": {"old": 3, "new": None}}
    assert call({"device_ids": [a["id"]], "fields": {"place_id": "HQ-NOPE"}})["failed"][0]["error"].startswith("There is no place")
