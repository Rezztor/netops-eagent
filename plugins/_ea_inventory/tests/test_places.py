"""places.py: the pure helpers, then the rules over the database."""

import pytest

from helpers import db
from plugins._ea_inventory.helpers import places, proposals
from plugins._ea_inventory.helpers.normalise import InventoryError


def test_pure_helpers():
    assert places.normalise_id(" difc-ga ") == "DIFC-GA" and places.normalise_id("  ") is None
    for bad in ("a b", "TOOLONGCODE", "A-B-C-D-E-F-G", "A--B"):
        with pytest.raises(InventoryError):
            places.normalise_id(bad)
    assert places.ancestors("DIFC-GA-GV4") == ["DIFC", "DIFC-GA", "DIFC-GA-GV4"]
    assert (places.depth("A-B"), places.kind_of("A-B-C-D-E-F"), places.under("A-B")) == (2, "rack", ("A-B", "A-B-%"))
    assert places.name_key("Gate-Village  4!") == "gatevillage4"


@pytest.mark.parametrize("kind,name,taken,want", [
    ("location", "Gate Avenue", set(), "GA"),
    ("building", "Gate Village Building 4", set(), "GV4"),
    ("building", "Tower One", set(), "ONE"),
    ("site", "DIFC", set(), "DIFC"),
    ("site", "Main Campus", set(), "MC"),
    ("floor", "Level 1", set(), "L1"),
    ("floor", "Ground Floor", set(), "GF"),
    ("floor", "Basement 2", set(), "B2"),
    ("floor", "Roof", set(), "RF"),
    ("room", "Server room", {"R01", "R02"}, "R03"),
    ("room", "Room 5", set(), "R05"),
    ("room", "1.01", set(), "R101"),
    ("rack", "R01", set(), "RK01"),
    ("rack", "Rack", {"RK01"}, "RK02"),
    ("location", "Gate Avenue", {"GA"}, "GA2"),
])
def test_suggest_code(kind, name, taken, want):
    assert places.suggest_code(kind, name, taken) == want


@pytest.fixture
def tree(database, events):
    places.create(None, "DIFC", "DIFC", address="Dubai", chain=[("Gate Avenue", "GA"), ("Gate Village 4", "GV4")], actor="ann")
    places.create("DIFC-GA-GV4", "Level 1", "L1", chain=[("Room 2", "R02"), ("Rack 1", "RK01")])
    return events


def refusal(call, *a, **k):
    with pytest.raises(InventoryError) as seen:
        call(*a, **k)
    return seen.value


def test_create_returns_the_named_place_first_and_audits(tree):
    made = places.create("DIFC-GA-GV4-L1", "Room 3", "R03", room_type="comms_room")
    assert [p["id"] for p in made] == ["DIFC-GA-GV4-L1-R03"] and made[0]["room_type"] == "comms_room"
    assert [(e, f.get("id")) for e, f in tree][:3] == [("inventory.place_created", i) for i in ("DIFC", "DIFC-GA", "DIFC-GA-GV4")]
    assert all("contact" not in f for _, f in tree)
    assert [p["id"] for p in places.list_places()][:3] == ["DIFC", "DIFC-GA", "DIFC-GA-GV4"]


def test_every_refusal(tree):
    msg = lambda e: str(e)  # noqa: E731
    assert "no place" in msg(refusal(places.create, "NOPE", "x", "X"))
    assert "lowest level" in msg(refusal(places.create, "DIFC-GA-GV4-L1-R02-RK01", "x", "X"))
    assert "six levels" in msg(refusal(places.create, "DIFC-GA-GV4-L1-R02", "x", "X", chain=[("a", "A"), ("b", "B")]))
    assert "not a code" in msg(refusal(places.create, None, "x", "bad code"))
    assert "needs a name" in msg(refusal(places.create, None, " ", "X"))
    taken = refusal(places.create, "DIFC", "Other", "GA")
    assert "DIFC-GA" in str(taken) and taken.existing["id"] == "DIFC-GA"
    named = refusal(places.create, "DIFC", "gate avenue", "GB")
    assert "DIFC-GA" in str(named) and named.existing["name"] == "Gate Avenue"
    assert "room type" in msg(refusal(places.create, "DIFC", "x", "X", room_type="office"))
    assert "address" in msg(refusal(places.create, "DIFC", "x", "X", address="here"))
    assert "cannot be changed" in msg(refusal(places.update, "DIFC", code="Z"))
    assert "no place" in msg(refusal(places.delete, "NOPE"))


def test_a_chain_is_atomic(tree):
    refusal(places.create, "DIFC", "New", "NEW", chain=[("Main", "M"), ("Main", "bad code")])
    refusal(places.create, "DIFC-GA", "Gate Village 4", "GVX", chain=[("a", "A")])  # name taken under the parent
    ids = {p["id"] for p in places.list_places()}
    assert "DIFC-NEW" not in ids and "DIFC-GA-GVX" not in ids and "DIFC-GA-GVX-A" not in ids


def test_update_deactivate_and_the_inactive_parent(tree):
    assert places.update("DIFC-GA", name="Gate Ave")["name"] == "Gate Ave"
    places.update("DIFC-GA", active=False)
    assert "inactive" in str(refusal(places.create, "DIFC-GA-GV4", "x", "X"))
    assert ("inventory.place_deactivated", "DIFC-GA") in [(e, f.get("id")) for e, f in tree]
    by_id = {p["id"]: p for p in places.list_places()}
    assert not places.effective_active("DIFC-GA-GV4", by_id) and places.effective_active("DIFC", by_id)
    assert [p["id"] for p in places.list_places(include_inactive=False)] == ["DIFC"]


def test_delete_is_blocked_by_children_devices_and_old_proposals(tree, make):
    assert "under it" in str(refusal(places.delete, "DIFC-GA"))
    device = make("sw1")
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("UPDATE inventory_device SET place_id = 'DIFC-GA-GV4-L1-R02-RK01', status = 'decommissioned'")
    message = str(refusal(places.delete, "DIFC-GA-GV4-L1-R02-RK01"))
    assert "1 device(s)" in message and "1 decommissioned" in message and "inactive" in message
    counts = {p["id"]: (p["device_count"], p["device_total"]) for p in places.list_places()}
    assert counts["DIFC-GA-GV4-L1-R02-RK01"] == (0, 0)  # decommissioned devices are not counted
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("UPDATE inventory_device SET place_id = NULL")
        conn.execute(
            "INSERT INTO inventory_proposal (kind, device_id, changes) VALUES ('update', %s, "
            "'{\"place_id\": {\"old\": null, \"new\": \"DIFC-GA-GV4-L1-R02-RK01\"}}')", (device["id"],))
    assert "proposal" in str(refusal(places.delete, "DIFC-GA-GV4-L1-R02-RK01"))
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("DELETE FROM inventory_proposal WHERE changes ? 'place_id'")
    places.delete("DIFC-GA-GV4-L1-R02-RK01")
    assert places.get("DIFC-GA-GV4-L1-R02-RK01") is None and ("inventory.place_deleted", "DIFC-GA-GV4-L1-R02-RK01") in [
        (e, f.get("id")) for e, f in tree]


def test_device_counts_roll_up(tree, make):
    make("sw1"), make("sw2")
    with db.transaction(db.NO_SCOPE) as conn:
        conn.execute("UPDATE inventory_device SET place_id = 'DIFC-GA-GV4-L1-R02' WHERE name = 'sw1'")
        conn.execute("UPDATE inventory_device SET place_id = 'DIFC-GA-GV4-L1-R02-RK01' WHERE name = 'sw2'")
    counts = {p["id"]: (p["device_count"], p["device_total"]) for p in places.list_places()}
    assert counts["DIFC-GA-GV4-L1-R02"] == (1, 2) and counts["DIFC"] == (0, 2) and counts["DIFC-GA-GV4-L1"] == (0, 2)
    assert places.get("DIFC-GA")["device_total"] == 2


def test_resolve(tree):
    top = "DIFC-GA-GV4-L1-R02-RK01"
    assert places.resolve(place_id="difc-ga") == ("DIFC-GA", "")
    assert places.resolve("difc", "Gate Avenue", "GV4", "l1", "Room-2", "rk01") == (top, "")
    assert places.resolve("DIFC", "GA") == ("DIFC-GA", "")
    assert places.resolve("DIFC", "", "GV4") == ("DIFC-GA-GV4", "")  # one location: filled
    assert places.resolve("DIFC", "", "GV4", "L1", "R02") == ("DIFC-GA-GV4-L1-R02", "")
    assert places.resolve(place_id="NOPE")[0] is None
    assert places.resolve() == (None, "No place named.")
    found, why = places.resolve("DIFC", "GA", "GV9", "L1")
    assert found == "DIFC-GA" and "building 'GV9' not found under DIFC-GA" in why
    assert places.resolve("DIFC", "gate  avenu")[0] == "DIFC"  # no fuzzy matching
    places.create("DIFC", "Second", "SEC")
    found, why = places.resolve("DIFC", "", "GV4")
    assert found == "DIFC" and why == "location missing: DIFC has 2 locations"
    places.update("DIFC-SEC", active=False)  # an inactive place does not count, or match
    assert places.resolve("DIFC", "", "GV4")[0] == "DIFC-GA-GV4"
    assert places.resolve("DIFC", "SEC")[0] == "DIFC"
    places.update("DIFC-GA", active=False)
    assert places.resolve("DIFC", "GA")[0] == "DIFC" and "inactive" in places.resolve(place_id="DIFC-GA")[1]
