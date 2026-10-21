import asyncio

import pytest

from plugins._ea_inventory.api.inventory_list import InventoryList
from plugins._ea_inventory.api.inventory_propose_decommission_stale import InventoryProposeDecommissionStale
from plugins._ea_inventory.api.inventory_stale import InventoryStale
from plugins._ea_inventory.helpers import proposals, store


def call(handler_class, payload=None):
    handler = handler_class(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process(payload or {}, None))


@pytest.fixture(autouse=True)
def who(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")


@pytest.fixture
def fleet(make):
    """seen today, seen 20 days ago, never observed, and a planned one (not active)."""
    fresh, old, never = (make(n, status="active") for n in ("fresh", "old", "never"))
    make("planned")
    store.add_observation(fresh["id"], "facts", "fresh")
    store.add_observation(old["id"], "solarwinds", "7")
    with store.transaction() as conn:
        conn.execute("UPDATE inventory_observation SET observed_at = now() - interval '20 days' WHERE device_id = %s", (old["id"],))
        conn.execute("UPDATE inventory_device SET last_seen_at = now() - interval '20 days' WHERE id = %s", (old["id"],))
    return fresh, old, never


def test_stale_lists_active_devices_unseen_for_days_never_seen_first(fleet):
    out = call(InventoryStale)
    assert out["ok"] and out["stale_days"] == 7 and out["total"] == 2
    never, old = out["devices"]
    assert (never["name"], never["last_seen_at"], never["days_unseen"], never["sources"]) == ("never", None, None, [])
    assert (old["name"], old["days_unseen"], old["sources"]) == ("old", 20, ["solarwinds"])
    assert set(old) == {"id", "name", "status", "last_seen_at", "days_unseen", "sources"}
    assert [d["name"] for d in call(InventoryStale, {"days": 30})["devices"]] == ["never"]
    assert call(InventoryStale, {"days": "x"})["ok"] is False


def test_inventory_list_stale_filter(fleet):
    out = call(InventoryList, {"stale": True})
    assert [d["name"] for d in out["devices"]] == ["old"] and out["total"] == 1  # any source stale; never-seen has none
    assert call(InventoryList, {})["total"] == 4


def test_propose_decommission_stale_makes_one_batch_of_proposals_only(fleet):
    fresh, old, never = fleet
    out = call(InventoryProposeDecommissionStale, {"device_ids": [old["id"], never["id"], fresh["id"], "bogus"], "note": "gone"})
    assert out["ok"] and out["created"] == 2 and out["batch_id"]
    assert sorted(r["id"] for r in out["rejected"]) == sorted([fresh["id"], "bogus"])
    found, total, _ = proposals.list_proposals(batch_id=out["batch_id"])
    assert total == 2 and {p["kind"] for p in found} == {"decommission"} and {p["source"] for p in found} == {"manual"}
    assert {p["status"] for p in found} == {"pending"}
    ev = {p["device_name"]: p["evidence"] for p in found}
    assert ev["old"]["reason"] == "stale" and ev["old"]["days_unseen"] == 20 and ev["never"]["days_unseen"] is None
    assert store.get_device(old["id"])["status"] == "active"  # nothing changes until approved
    assert call(InventoryProposeDecommissionStale, {"device_ids": []})["ok"] is False
    assert call(InventoryProposeDecommissionStale, {"device_ids": [fresh["id"]]})["batch_id"] is None
