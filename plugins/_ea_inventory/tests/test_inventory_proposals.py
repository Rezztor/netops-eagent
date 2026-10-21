import pytest

from helpers import db
from plugins._ea_inventory.helpers import places, proposals, store
from plugins._ea_inventory.helpers.normalise import InventoryError


def test_migration_creates_the_tables(database):
    with db.unscoped_transaction() as conn:
        names = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    assert {"inventory_place", "inventory_device", "inventory_observation", "inventory_proposal"} <= names


def test_create_is_a_proposal_until_approved(database, events):
    p = proposals.propose("create", None, {"name": "SW1", "vendor": "cisco"}, actor="alice")
    assert p["status"] == "pending" and p["changes"]["name"] == {"old": None, "new": "sw1"}
    assert store.search_devices()[1] == 0
    done, device = proposals.decide(p["id"], "approve", "ok", actor="alice")  # self-approval
    assert done["status"] == "approved" and done["device_id"] == device["id"]
    assert device["version"] == 1 and device["name"] == "sw1" and device["serial"] == ""
    assert device["field_sources"]["vendor"]["source"] == "manual"
    assert device["field_sources"]["vendor"]["approved_by"] == "alice"


def test_audit_events_carry_both_requester_and_decider(database, events):
    p = proposals.propose("create", None, {"name": "sw1"}, actor="alice")
    proposals.decide(p["id"], "approve", actor="bob")
    names = [e for e, _ in events]
    assert names == ["inventory.proposed", "inventory.approved"]
    assert events[1][1]["requested_by"] == "alice" and events[1][1]["decided_by"] == "bob"


def test_update_applies_edits_bumps_version_and_records_provenance(make):
    d = make(vendor="cisco", software_version="1")
    p = proposals.propose("update", d["id"], {"software_version": "2", "model": "9300"}, actor="a")
    assert set(p["changes"]) == {"software_version", "model"} and p["base_version"] == 1
    _, d2 = proposals.decide(p["id"], "approve", edits={"software_version": "2.1"}, actor="b")
    assert d2["version"] == 2 and d2["software_version"] == "2.1" and d2["vendor"] == "cisco"
    assert d2["field_sources"]["software_version"]["approved_by"] == "b"
    assert d2["field_sources"]["vendor"]["approved_by"] == "alice"  # untouched field keeps its source


def test_update_with_no_difference_is_refused(make):
    d = make(vendor="cisco")
    with pytest.raises(InventoryError, match="Nothing would change"):
        proposals.propose("update", d["id"], {"vendor": "cisco"})


def test_edits_may_not_touch_other_fields(make):
    d = make()
    p = proposals.propose("update", d["id"], {"model": "x"})
    with pytest.raises(InventoryError, match="edits may only"):
        proposals.decide(p["id"], "approve", edits={"vendor": "y"})


def test_stale_base_version_supersedes(make):
    d = make()
    first = proposals.propose("update", d["id"], {"model": "a"})
    second = proposals.propose("update", d["id"], {"model": "b"})
    proposals.decide(first["id"], "approve")
    with pytest.raises(proposals.StaleProposal, match="propose again"):
        proposals.decide(second["id"], "approve")
    assert proposals.list_proposals(status="superseded")[1] == 1
    assert store.get_device(d["id"])["model"] == "a"


def test_reject_changes_nothing(make):
    d = make()
    p = proposals.propose("update", d["id"], {"model": "a"})
    done, device = proposals.decide(p["id"], "reject", "no", actor="bob")
    assert done["status"] == "rejected" and device is None and done["decided_by"] == "bob"
    assert store.get_device(d["id"])["version"] == 1
    with pytest.raises(InventoryError, match="already rejected"):
        proposals.decide(p["id"], "approve")


def test_decommission_keeps_the_row_and_frees_the_ip(make):
    d = make(mgmt_ip="10.0.0.1", status="active")
    p = proposals.propose("decommission", d["id"], None)
    _, gone = proposals.decide(p["id"], "approve")
    assert gone["status"] == "decommissioned" and store.search_devices()[1] == 1
    make("sw2", mgmt_ip="10.0.0.1")  # the address is free again
    with pytest.raises(InventoryError, match="already decommissioned"):
        proposals.propose("decommission", d["id"], None)


def test_uniqueness_names_the_other_device(make):
    make("sw1", serial="abc123", mgmt_ip="10.0.0.1")
    for fields, word in (({"name": "SW1"}, "name"), ({"name": "x", "serial": "ABC123"}, "serial"),
                         ({"name": "x", "mgmt_ip": "10.0.0.1"}, "management IP")):
        with pytest.raises(InventoryError, match=f"{word}.*'sw1'"):
            proposals.propose("create", None, fields)


def test_unset_serials_do_not_collide(make):
    make("a", serial="N/A")
    make("b", serial="unknown")


def test_conflict_created_after_proposing_is_caught_on_approval(make):
    p1 = proposals.propose("create", None, {"name": "x", "mgmt_ip": "10.0.0.9"})
    p2 = proposals.propose("create", None, {"name": "y", "mgmt_ip": "10.0.0.9"})
    proposals.decide(p1["id"], "approve")
    with pytest.raises(InventoryError, match="management IP"):
        proposals.decide(p2["id"], "approve")


def test_bad_input_is_refused(make):
    d = make()
    for args in (("create", None, {"name": "n", "custom": {"password": "x"}}),
                 ("create", None, {"vendor": "x"}),
                 ("create", None, {"name": "n", "place_id": "NOWHERE"}),
                 ("create", None, {"name": "n", "site_id": "DIFC"}),
                 ("update", d["id"], {"bogus": 1}), ("update", None, {"model": "x"}),
                 ("purge", None, {})):
        with pytest.raises(InventoryError):
            proposals.propose(*args)
    with pytest.raises(InventoryError):
        proposals.propose("create", None, {"name": "n"}, source="telepathy")


RACK = "DIFC-GA-GV4-L1-R02-RK01"
ROOM = "DIFC-GA-GV4-L1-R02"


@pytest.fixture
def tree(database, events):
    places.create(None, "DIFC", "DIFC", chain=[("Gate Avenue", "GA"), ("Gate Village 4", "GV4")])
    places.create("DIFC-GA-GV4", "Level 1", "L1", chain=[("Room 2", "R02"), ("Rack 1", "RK01")])


def test_place_id_round_trips_through_create_and_update_proposals(make, tree):
    p = proposals.propose("create", None, {"name": "sw1", "place_id": " difc-ga-gv4-l1-r02 "}, actor="a")
    assert p["changes"]["place_id"] == {"old": None, "new": ROOM}
    d = proposals.decide(p["id"], "approve", actor="a")[1]
    assert d["place_id"] == ROOM and d["field_sources"]["place_id"]["source"] == "manual"
    u = proposals.propose("update", d["id"], {"place_id": RACK, "rack_unit": 4}, actor="a")
    assert u["changes"] == {"place_id": {"old": ROOM, "new": RACK}, "rack_unit": {"old": None, "new": 4}}
    d = proposals.decide(u["id"], "approve", actor="a")[1]
    assert (d["place_id"], d["rack_unit"]) == (RACK, 4)


def test_an_unknown_or_inactive_place_is_refused(make, tree):
    d = make(place_id=ROOM)
    with pytest.raises(InventoryError, match="no place"):
        proposals.propose("update", d["id"], {"place_id": "DIFC-GA-GV4-L2"})
    places.update("DIFC-GA", active=False)  # the room is inactive through its location
    with pytest.raises(InventoryError, match="inactive"):
        proposals.propose("create", None, {"name": "n", "place_id": RACK})
    with pytest.raises(InventoryError, match="rack"):
        proposals.propose("update", d["id"], {"rack_unit": 3})  # a room has no units
    assert proposals.propose("update", d["id"], {"model": "x"})  # keeping an inactive place is not a change of place


def test_leaving_a_rack_clears_the_rack_unit_in_the_same_proposal(make, tree):
    d = make(place_id=RACK, rack_unit=7)
    u = proposals.propose("update", d["id"], {"place_id": ROOM})
    assert u["changes"] == {"place_id": {"old": RACK, "new": ROOM}, "rack_unit": {"old": 7, "new": None}}
    assert proposals.decide(u["id"], "approve")[1]["rack_unit"] is None
    with pytest.raises(InventoryError, match="rack"):
        proposals.propose("create", None, {"name": "n", "place_id": ROOM, "rack_unit": 2})
    with pytest.raises(InventoryError, match="rack"):
        proposals.propose("create", None, {"name": "n", "rack_unit": 2})  # no place at all: the table forbids it too


def test_derived_keys_follow_the_place(make, tree):
    names = lambda d: [d[k] for k in ("location", "building", "floor", "room", "rack")]  # noqa: E731
    rack, room = make("a", place_id=RACK), make("b", place_id=ROOM)
    near, site = make("c", place_id="DIFC-GA"), make("d", place_id="DIFC")
    assert rack["place"]["kind"] == "rack" and [p["code"] for p in rack["place"]["path"]] == ["DIFC", "GA", "GV4", "L1", "R02", "RK01"]
    assert names(rack) == ["Gate Avenue", "Gate Village 4", "Level 1", "Room 2", "Rack 1"] and rack["site"]["id"] == "DIFC"
    assert names(room) == ["Gate Avenue", "Gate Village 4", "Level 1", "Room 2", ""] and room["place"]["active"] is True
    assert names(near) == ["Gate Avenue", "", "", "", ""] and names(site) == [""] * 5 and site["site"] == {"id": "DIFC", "name": "DIFC"}
    bare = make("e")
    assert bare["place"] is None and bare["site"] is None and names(bare) == [""] * 5
    places.update("DIFC-GA", active=False)
    assert store.get_device(rack["id"])["place"]["active"] is False


def test_place_filter_at_every_level_and_none(make, tree):
    make("a", place_id=RACK)
    make("b", place_id=ROOM)
    make("c")
    ids = list(places.ancestors(RACK))
    names = lambda **kw: sorted(d["name"] for d in store.search_devices(**kw)[0])  # noqa: E731
    assert [names(place=i) for i in ids] == [["a", "b"]] * 5 + [["a"]]
    assert names(place="none") == ["c"] and names(place="difc-ga") == ["a", "b"] and names(place="NOPE") == []
    assert names(site_name="difc") == ["a", "b"] and names(site_name="Nothing") == []
    assert names(q="gate avenue") == ["a", "b"] and names(q="rk01") == ["a"] and names(q="room 2") == ["a", "b"]


def test_list_filters_and_facets(make):
    make("a", vendor="cisco", platform="ios", tags=["core"], status="active")
    make("b", vendor="dell", platform="sonic", status="planned")
    assert [d["name"] for d in store.search_devices(vendor="Cisco")[0]] == ["a"]
    assert [d["name"] for d in store.search_devices(q="b")[0]] == ["b"]
    assert [d["name"] for d in store.search_devices(tag="core")[0]] == ["a"]
    assert store.search_devices(status="active", platform="sonic")[1] == 0
    f = store.facets()
    assert f["vendor"] == ["cisco", "dell"] and f["tag"] == ["core"] and f["status"] == ["active", "planned"]


def test_observations_are_stored_without_changing_the_device(make):
    d = make()
    store.add_observation(d["id"], "solarwinds", "NodeID:1", {"model": "x"})
    assert store.device_observations(d["id"])[0]["payload"] == {"model": "x"}
    assert store.get_device(d["id"])["model"] == "" and store.get_device(d["id"])["version"] == 1


def test_list_devices_is_unpaged_for_the_sync(make):
    make("a", status="active")
    make("b")
    assert [d["name"] for d in store.list_devices(status="active")] == ["a"]
    assert len(store.list_devices()) == 2


RECORD = {
    "place_id": RACK, "device_type": "switch", "part_number": "WS-C3850-48P", "base_mac": "aa:bb:cc:dd:ee:ff", "rack_unit": 22, "u_height": 2, "department": "IT",
    "custodian": "Dewi", "support_contact": "noc@example.com", "support_contract": "SN-1",
    "purchase_date": "2024-01-31", "warranty_expiry": "2027-01-31", "support_expiry": "2028-01-31",
    "eol_date": "2030-12-31",
}


def test_asset_record_round_trips_through_create_and_update_proposals(make, tree):
    p = proposals.propose("create", None, {"name": "sw1", **RECORD}, actor="a")
    assert p["changes"]["rack_unit"] == {"old": None, "new": 22}
    assert p["changes"]["eol_date"] == {"old": None, "new": "2030-12-31"}
    device = proposals.decide(p["id"], "approve", actor="a")[1]
    assert {k: device[k] for k in RECORD} == RECORD
    assert device["field_sources"]["rack_unit"]["source"] == "manual"

    u = proposals.propose("update", device["id"], {"rack_unit": "23", "eol_date": "", "base_mac": "AABB.CCDD.EEF0"}, actor="a")
    assert u["changes"] == {
        "rack_unit": {"old": 22, "new": 23}, "eol_date": {"old": "2030-12-31", "new": None},
        "base_mac": {"old": "aa:bb:cc:dd:ee:ff", "new": "aa:bb:cc:dd:ee:f0"},
    }
    after = proposals.decide(u["id"], "approve", actor="a")[1]
    assert (after["rack_unit"], after["eol_date"], after["base_mac"]) == (23, None, "aa:bb:cc:dd:ee:f0")
    assert proposals.propose("update", after["id"], {"status": "spare"}, actor="a")["changes"]["status"]["new"] == "spare"


def test_a_new_device_gets_asset_record_defaults(make):
    d = make("plain")
    assert d["device_type"] == "other" and d["rack_unit"] is None and d["warranty_expiry"] is None and d["rack"] == ""
    assert d["place_id"] is None and d["place"] is None and d["site"] is None


def test_asset_record_filters_search_and_facets(make, tree):
    make("a", **RECORD)
    make("b", device_type="router", role="Core", department="Ops")
    names = lambda **kw: [d["name"] for d in store.search_devices(**kw)[0]]  # noqa: E731
    assert names(device_type="SWITCH") == ["a"] and names(role="core") == ["b"] and names(department="ops") == ["b"]
    assert names(q="ws-c3850") == ["a"] and names(q="aa:bb:cc") == ["a"] and names(q="dewi") == ["a"]
    assert names(q="sn-1") == ["a"] and names(q="ops") == ["b"]
    f = store.facets()
    assert f["device_type"] == ["router", "switch"] and f["role"] == ["Core"]
    assert f["department"] == ["IT", "Ops"] and "building" not in f and "rack" not in f
    assert "access_point" in f["device_types"] and "core" in f["role_suggestions"] and "faulty" in f["statuses"]


def test_migration_0013_keeps_existing_rows(database):
    import psycopg

    with psycopg.connect(db.owner_url()) as conn:  # DDL is transactional: nothing here outlives the test
        for col in ("device_type", "part_number", "base_mac", "rack_unit",
                    "u_height", "department", "custodian", "support_contact", "support_contract",
                    "purchase_date", "warranty_expiry", "support_expiry", "eol_date"):
            conn.execute(f"ALTER TABLE inventory_device DROP COLUMN {col} CASCADE")
        conn.execute("ALTER TABLE inventory_device DROP CONSTRAINT inventory_device_status_check")
        conn.execute("ALTER TABLE inventory_device ADD CONSTRAINT inventory_device_status_check "
                     "CHECK (status IN ('active','planned','staged','offline','decommissioned'))")
        conn.execute("INSERT INTO inventory_device (name, status) VALUES ('old', 'active')")
        conn.execute(open(next(p for n, _, p in db.migrations() if n == 13)).read())
        row = conn.execute("SELECT device_type, rack_unit, eol_date FROM inventory_device").fetchone()
        assert row == ("other", None, None)
        conn.execute("UPDATE inventory_device SET status = 'spare', device_type = 'router'")
        for bad in ("status = 'gone'", "device_type = 'toaster'", "rack_unit = 61", "u_height = 0"):
            with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
                conn.execute(f"UPDATE inventory_device SET {bad}")
        conn.rollback()


def test_a_value_the_approver_typed_is_recorded_as_manual(make):
    """It was recorded with the proposal's automatic source, so the next automatic run treated it as its own."""
    d = make(vendor="cisco", software_version="1")
    p = proposals.propose("update", d["id"], {"software_version": "2", "model": "9300"}, source="facts", actor="a")
    _, d2 = proposals.decide(p["id"], "approve", edits={"software_version": "2.1"}, actor="b")
    assert d2["field_sources"]["software_version"]["source"] == "manual"
    assert d2["field_sources"]["model"]["source"] == "facts"


def test_the_propose_endpoint_sets_the_source_and_keeps_evidence_as_a_claim(database, monkeypatch):
    import asyncio

    from plugins._ea_inventory.api.inventory_propose import InventoryPropose

    handler = InventoryPropose.__new__(InventoryPropose)
    out = asyncio.run(handler.process({
        "kind": "create", "fields": {"name": "sw9"}, "source": "solarwinds",
        "evidence": {"quote": "seen on the wire", "location": "p1", "confidence": 99, "batch_id": "b", "observed_at": "2020-01-01"},
    }, None))
    row = out["proposal"]
    assert row["source"] == "manual"
    assert set(row["evidence"]) == {"claimed", "claimed_by"} and row["evidence"]["claimed"]["confidence"] == 99
    assert not asyncio.run(handler.process({"kind": "create", "fields": {"name": "sw8"}, "evidence": "x" * 5000}, None))["ok"]
