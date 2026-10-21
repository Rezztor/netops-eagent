"""places.merge: fold one place and its subtree into another of the same kind; devices move by proposal."""

import pytest

from plugins._ea_inventory.api.inventory_place_merge import InventoryPlaceMerge
from plugins._ea_inventory.helpers import places, proposals, store
from plugins._ea_inventory.helpers.normalise import InventoryError

OLD_RACK, NEW_RACK = "DIFC-OLD-B1-L1-R01-RK01", "DIFC-NEW-B1-L1-R01-RK01"


@pytest.fixture
def tree(database, events):
    places.create(None, "DIFC", "DIFC", chain=[("Old", "OLD"), ("Tower", "B1"), ("Level 1", "L1"), ("Room 1", "R01"),
                                               ("Rack 1", "RK01")])
    places.create("DIFC", "New", "NEW", chain=[("Tower", "B1")])  # the new location already has its building
    places.create("DIFC-OLD-B1-L1", "Room 2", "R02", room_type="office", notes="fire exit")


def merge(**kw):
    return places.merge("DIFC-OLD", "DIFC-NEW", actor="alice", **kw)


def test_a_dry_run_says_what_would_happen_and_changes_nothing(tree, make):
    make("sw1", place_id=OLD_RACK, rack_unit=4)
    plan = merge()
    assert plan["dry_run"] and plan["devices"] == 1 and plan["clashes"] == []
    assert [c["id"] for c in plan["create"]] == [
        "DIFC-NEW-B1-L1", "DIFC-NEW-B1-L1-R01", "DIFC-NEW-B1-L1-R01-RK01", "DIFC-NEW-B1-L1-R02"]
    assert plan["sample"] == [{"device_id": plan["sample"][0]["device_id"], "name": "sw1", "from": OLD_RACK, "to": NEW_RACK}]
    assert len(places.list_places()) == 9 and all(p["active"] for p in places.list_places())
    assert proposals.list_proposals(status="pending")[1] == 0


def test_apply_creates_stages_one_batch_and_retires_the_old_place(tree, make, events):
    a, b = make("sw1", place_id=OLD_RACK, rack_unit=4), make("sw2", place_id="DIFC-OLD-B1-L1-R02")
    done = merge(dry_run=False)
    by_id = {p["id"]: p for p in places.list_places()}
    assert by_id["DIFC-NEW-B1-L1-R02"]["room_type"] == "office" and by_id["DIFC-NEW-B1-L1-R02"]["notes"] == "fire exit"
    assert not any(p["active"] for i, p in by_id.items() if i.startswith("DIFC-OLD")) and by_id["DIFC-NEW"]["active"]
    pending, _, _ = proposals.list_proposals(status="pending")
    assert len(pending) == 2 and {p["evidence"]["batch_id"] for p in pending} == {done["batch_id"]}
    assert store.get_device(a["id"])["place_id"] == OLD_RACK  # nothing written until Review approves
    for p in pending:
        proposals.decide(p["id"], "approve", actor="bob")
    moved = store.get_device(a["id"])
    assert (moved["place_id"], moved["rack_unit"], moved["place"]["active"]) == (NEW_RACK, 4, True)
    assert store.get_device(b["id"])["place_id"] == "DIFC-NEW-B1-L1-R02"
    assert ("inventory.place_merged", {"id": "DIFC-OLD", "into": "DIFC-NEW", "kind": "location", "created": 4,
                                       "devices": 2, "batch_id": done["batch_id"], "actor": "alice"}) in events
    assert merge(dry_run=False)["devices"] == 0  # nothing left to move; running again is harmless


def test_a_pending_move_into_the_old_place_follows_the_merge(tree, make):
    d = make("sw1")
    p = proposals.propose("update", d["id"], {"place_id": OLD_RACK})
    merge(dry_run=False)
    assert proposals.list_proposals(status="pending")[0][0]["changes"]["place_id"]["new"] == NEW_RACK
    assert proposals.decide(p["id"], "approve")[1]["place_id"] == NEW_RACK


def test_refusals(tree):
    for args, why in (
        (("DIFC-OLD", "DIFC-OLD"), "different place"), (("DIFC-OLD", "DIFC-NEW-B1"), "only into another location"),
        (("DIFC-OLD", "NOPE-X"), "no place"), (("DIFC-OLD", ""), "Name the place"),
    ):
        with pytest.raises(InventoryError, match=why):
            places.merge(*args)
    places.update("DIFC-NEW", active=False)
    with pytest.raises(InventoryError, match="inactive"):
        merge()
    places.update("DIFC-NEW", active=True)
    places.create("DIFC-NEW-B1", "Level 1", "L1", chain=[("Room 1", "RX")])  # the same name under another code
    plan = merge()
    assert (plan["clashes"][0]["id"], plan["clashes"][0]["existing"]) == ("DIFC-OLD-B1-L1-R01", "DIFC-NEW-B1-L1-RX")
    with pytest.raises(InventoryError, match="another code"):
        merge(dry_run=False)
    assert places.get("DIFC-OLD")["active"]


def test_the_endpoint_declares_the_places_permission_and_answers_in_json(tree, monkeypatch):
    import asyncio
    from helpers import rbac

    assert InventoryPlaceMerge.required_permission() == rbac.PERM_INVENTORY_PLACES
    handler = InventoryPlaceMerge(app=None, thread_lock=None)
    out = asyncio.run(handler.process({"from_id": "DIFC-OLD", "into_id": "DIFC-NEW"}, None))
    assert out["ok"] and out["dry_run"] and out["into_id"] == "DIFC-NEW"
    assert asyncio.run(handler.process({"from_id": "DIFC-OLD", "into_id": "DIFC"}, None))["ok"] is False
