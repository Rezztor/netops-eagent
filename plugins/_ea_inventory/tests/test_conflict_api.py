import asyncio

import pytest

from plugins._ea_inventory.api.inventory_conflict_decide import InventoryConflictDecide
from plugins._ea_inventory.api.inventory_conflict_decide_many import InventoryConflictDecideMany
from plugins._ea_inventory.api.inventory_conflicts import InventoryConflicts
from plugins._ea_inventory.api.inventory_pins import InventoryPins
from plugins._ea_inventory.api.inventory_unpin import InventoryUnpin
from plugins._ea_inventory.helpers import conflicts, proposals, store

C = {"field": "model", "current": "Human", "proposed": "C9300", "current_source": "manual"}


def call(handler_class, payload=None):
    handler = handler_class(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process(payload or {}, None))


@pytest.fixture(autouse=True)
def who(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "bob")


@pytest.fixture
def two(make):
    a, b = make("sw1", model="Human"), make("sw2", vendor="Human")
    conflicts.upsert_conflicts(a["id"], "facts", [C], {"job_id": "j"})
    conflicts.upsert_conflicts(b["id"], "facts", [{"field": "vendor", "current": "Human", "proposed": "Cisco", "current_source": "manual"}], {})
    return a, b


def test_list_shape_filters_and_paging(two):
    a, _ = two
    out = call(InventoryConflicts)
    assert out["ok"] and out["total"] == out["open_count"] == 2
    assert set(out["conflicts"][0]) == {"id", "device_id", "device_name", "field", "current_value", "current_source",
                                        "proposed_value", "source", "observed_at", "status", "decided_by", "decided_at", "note", "created_at"}
    assert [c["device_name"] for c in call(InventoryConflicts, {"device_id": a["id"]})["conflicts"]] == ["sw1"]
    assert len(call(InventoryConflicts, {"limit": 1, "offset": 1})["conflicts"]) == 1
    assert call(InventoryConflicts, {"status": "accepted"})["total"] == 0


def test_decide_accept_keep_pin_and_errors(two):
    cs = call(InventoryConflicts)["conflicts"]
    by = {c["field"]: c["id"] for c in cs}
    out = call(InventoryConflictDecide, {"id": by["model"], "decision": "accept", "note": "ok"})
    assert out["ok"] and out["conflict"]["status"] == "accepted" and out["proposal"]["status"] == "approved" and out["device"]["model"] == "C9300"
    out = call(InventoryConflictDecide, {"id": by["vendor"], "decision": "pin"})
    assert out["conflict"]["status"] == "pinned" and "proposal" not in out and "device" not in out
    assert call(InventoryConflictDecide, {"id": by["vendor"], "decision": "keep"})["ok"] is False
    assert call(InventoryConflictDecide, {"id": "nope", "decision": "keep"})["ok"] is False
    assert call(InventoryConflictDecide, {"id": by["vendor"], "decision": "burn"})["ok"] is False
    assert call(InventoryConflicts)["open_count"] == 0


def test_decide_many_reports_failures_and_carries_on(two):
    ids = [c["id"] for c in call(InventoryConflicts)["conflicts"]]
    out = call(InventoryConflictDecideMany, {"ids": [*ids, "bogus"], "decision": "keep"})
    assert out["ok"] and out["decided"] == 2 and [f["id"] for f in out["failed"]] == ["bogus"] and out["failed"][0]["error"]
    assert call(InventoryConflictDecideMany, {"ids": [], "decision": "keep"})["ok"] is False
    assert call(InventoryConflicts)["open_count"] == 0


def test_pins_list_and_unpin(two):
    a, b = two
    call(InventoryConflictDecideMany, {"ids": [c["id"] for c in call(InventoryConflicts)["conflicts"]], "decision": "pin"})
    pins = call(InventoryPins)["pins"]
    assert {(p["device_name"], p["field"], p["source"], p["pinned_by"]) for p in pins} == {("sw1", "model", "facts", "bob"), ("sw2", "vendor", "facts", "bob")}
    assert set(pins[0]) == {"device_id", "device_name", "field", "source", "pinned_by", "pinned_at"}
    assert len(call(InventoryPins, {"device_id": a["id"]})["pins"]) == 1
    assert call(InventoryUnpin, {"device_id": a["id"], "field": "model", "source": "facts"}) == {"ok": True}
    assert call(InventoryUnpin, {"device_id": a["id"], "field": "model", "source": "facts"})["ok"] is False
    assert len(call(InventoryPins)["pins"]) == 1


def test_device_json_carries_facts_at_and_ha_members(make):
    a, b, c = make("fw1", ha_group="cl1"), make("fw2", ha_group="cl1", status="active"), make("fw3", ha_group="other")
    store.add_observation(a["id"], "facts", "fw1")
    with store.transaction() as conn:
        devices = [store.get_device(x["id"], conn) for x in (a, b, c)]
        assert all(d["facts_at"] is None and d["ha_members"] == [] for d in devices)  # defaults until attached
        store.attach_sources(conn, devices)
    da, db_, dc = devices
    assert da["facts_at"] and db_["facts_at"] is None
    assert da["ha_members"] == [{"id": b["id"], "name": "fw2", "status": "active"}]
    assert db_["ha_members"] == [{"id": a["id"], "name": "fw1", "status": "planned"}] and dc["ha_members"] == []
