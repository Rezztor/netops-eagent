from types import SimpleNamespace

import pytest

from plugins._ea_inventory.helpers import places, proposals, staging, store
from plugins._ea_inventory.helpers.normalise import InventoryError

TEXT = (
    "name\tserial\tip\n"
    "sw-a\tFOC111\t10.0.0.1\n"
    "sw-b\tFOC222\t10.0.0.2\n"
)
DOC = SimpleNamespace(text=TEXT, source_id="abc123")


def rec(name="sw-a", quote="sw-a\tFOC111\t10.0.0.1", **fields):
    return {"fields": {"name": name, **fields}, "evidence": {"quote": quote, "location": "row 1"}}


def stage(records, doc=DOC):
    return staging.stage(doc, records, "devices.csv", "alice")


def pending():
    return proposals.list_proposals(status="pending")[0]


def test_stages_a_create_with_evidence_and_commits_nothing(database, events):
    out = stage([{**rec(serial="foc111", mgmt_ip="10.0.0.1"), "confidence": 0.9}])
    assert (out["created"], out["updates"], out["unchanged"], out["rejected"]) == (1, 0, 0, [])
    assert store.search_devices()[1] == 0
    [p] = pending()
    assert p["source"] == "extraction" and p["kind"] == "create" and p["requested_by"] == "alice"
    assert p["evidence"] == {
        "file": "devices.csv", "source_id": "abc123", "quote": "sw-a\tFOC111\t10.0.0.1",
        "location": "row 1", "confidence": 0.9, "batch_id": out["batch_id"],
    }
    assert p["changes"]["serial"]["new"] == "FOC111"


def test_audit_event_has_counts_only(database, events):
    out = stage([rec(), rec("sw-b", "sw-b\tFOC222\t10.0.0.2"), rec("zzz")])
    [(_, f)] = [e for e in events if e[0] == "inventory.staged"]
    assert f == {
        "batch_id": out["batch_id"], "created": 2, "updates": 0, "unchanged": 0, "rejected": 1,
        "requested_by": "alice",
    }


def test_quote_must_be_in_the_source_ignoring_case_and_whitespace(database, events):
    out = stage([rec(quote="SW-A   foc111 10.0.0.1"), rec("sw-b", "sw-b has 10.9.9.9")])
    assert out["created"] == 1
    assert out["rejected"] == [{"index": 1, "reason": "The quote does not appear in the (redacted) source."}]


@pytest.mark.parametrize("evidence", [None, {}, {"quote": "sw-a"}, {"location": "x"}, "sw-a"])
def test_evidence_is_required(database, events, evidence):
    out = stage([{"fields": {"name": "sw-a"}, "evidence": evidence}])
    assert out["created"] == 0 and "evidence" in out["rejected"][0]["reason"]


def test_hallucinated_serial_and_ip_are_dropped_with_a_warning(database, events):
    out = stage([rec(serial="ZZZ999", mgmt_ip="10.9.9.9")])
    assert out["created"] == 1 and len(out["warnings"]) == 2
    assert set(pending()[0]["changes"]) == {"name"}


def test_name_must_be_in_the_source_and_present(database, events):
    out = stage([rec("ghost", "sw-a\tFOC111\t10.0.0.1"), {"fields": {"vendor": "x"}, "evidence": rec()["evidence"]}])
    assert [r["index"] for r in out["rejected"]] == [0, 1]
    assert "does not appear" in out["rejected"][0]["reason"] and "required" in out["rejected"][1]["reason"]


def test_unknown_fields_and_keys_are_rejected(database, events):
    out = stage([rec(colour="red"), {**rec(), "extra": 1}])
    assert out["created"] == 0 and len(out["rejected"]) == 2
    assert "Unknown field" in out["rejected"][0]["reason"]


def test_existing_device_gets_an_update_with_only_the_differences(make, events):
    make("sw-a", serial="FOC111", vendor="cisco", model="old")
    out = stage([rec(serial="foc111", vendor="cisco", model="C9300", software_version="")])
    assert (out["created"], out["updates"]) == (0, 1)
    [p] = pending()
    assert p["kind"] == "update" and list(p["changes"]) == ["model"]


def test_matches_by_ip_then_name_and_unchanged_is_skipped(make, events):
    make("sw-a", mgmt_ip="10.0.0.1", status="active")
    out = stage([rec("sw-a", mgmt_ip="10.0.0.1"), rec("sw-b", "sw-b\tFOC222\t10.0.0.2", vendor="x")])
    assert (out["unchanged"], out["created"], out["updates"]) == (1, 1, 0)
    make("sw-c", vendor="x")
    doc = SimpleNamespace(text="sw-c juniper", source_id="s")
    out = staging.stage(doc, [rec("sw-c", "sw-c juniper", vendor="juniper")], "f", "alice")
    assert out["updates"] == 1


def test_a_blank_cell_never_clears_a_field(make, events):
    make("sw-a", vendor="cisco")
    out = stage([rec(vendor="", model="")])
    assert out["unchanged"] == 1 and pending() == []


def test_two_keys_naming_different_devices_is_ambiguous(make, events):
    make("sw-a", serial="FOC111")
    make("sw-b")
    out = stage([rec("sw-b", "sw-a\tFOC111\t10.0.0.1", serial="FOC111")])
    assert out["rejected"][0]["reason"].startswith("ambiguous: sw-a, sw-b")
    assert pending() == []


def test_duplicates_in_a_batch_keep_the_first(database, events):
    out = stage([rec(serial="foc111"), rec("sw-a", "sw-a\tFOC111\t10.0.0.1", vendor="dup"), rec("sw-b", "sw-b\tFOC222\t10.0.0.2", serial="foc111")])
    assert out["created"] == 1 and out["rejected"] == [] and len(out["warnings"]) == 2
    assert out["unchanged"] == 0


def test_cap_and_empty_list_refuse_the_call(database, events):
    with pytest.raises(InventoryError, match="At most 200"):
        stage([rec()] * 201)
    with pytest.raises(InventoryError, match="non-empty"):
        stage([])
    assert stage([rec()] * 200)["created"] == 1


def test_confidence_must_be_a_fraction(database, events):
    out = stage([{**rec(), "confidence": 1.5}, {**rec(), "confidence": True}])
    assert len(out["rejected"]) == 2


ROOM = "DIFC-GA-GV4-L1-R02"
RACK = ROOM + "-RK01"
ROW_B = ("sw-b", "sw-b\tFOC222\t10.0.0.2")


def build(second_location=False):
    places.create(None, "DIFC", "DIFC", actor="alice", chain=[
        ("Gate Avenue", "GA"), ("Gate Village 4", "GV4"), ("Level 1", "L1"), ("Room 2", "R02"), ("Rack 1", "RK01"),
    ])
    if second_location:
        places.create("DIFC", "Dubai Walk", "DW", actor="alice")


def placed():
    return {p["changes"]["name"]["new"]: p["changes"].get("place_id", {}).get("new") for p in pending()}


def test_a_record_is_placed_by_id_by_six_names_or_five_at_a_single_location_site(database, events):
    build()
    out = stage([
        rec(place_id=ROOM.lower()),
        rec(*ROW_B, site="difc", location="Gate Avenue", building="GV4", floor="Level 1", room="R02", rack="rk01"),
    ])
    assert placed() == {"sw-a": ROOM, "sw-b": RACK}
    five = stage([rec(site="DIFC", building="Gate Village 4", floor="L1", room="Room 2", warranty_expiry="2027-03-01")])
    assert [(o["created"], o["warnings"], o["missing_places"]) for o in (out, five)] == [(2, [], []), (1, [], [])]
    assert sorted(p["changes"]["place_id"]["new"] for p in pending()) == [ROOM, ROOM, RACK]


def test_five_names_at_a_two_location_site_are_refused_naming_the_level(database, events):
    build(second_location=True)
    out = stage([rec(site="DIFC", building="GV4", floor="L1", room="R02")])
    assert out["created"] == 1 and placed() == {"sw-a": None}
    assert out["warnings"] == ["Record 0: location missing: DIFC has 2 locations; staged without a place. Add it under Places first."]
    assert out["missing_places"] == [{"path": "DIFC / GV4 / L1 / R02", "level": "location", "records": [0]}]


def test_a_missing_room_is_listed_once_and_the_device_is_staged_without_a_place(database, events):
    build()
    named = dict(site="DIFC", location="GA", building="GV4", floor="L1", room="R09")
    out = stage([rec(**named), rec(*ROW_B, **named, vendor="x")])
    assert out["created"] == 2 and len(out["warnings"]) == 2 and "room 'R09' not found under DIFC-GA-GV4-L1" in out["warnings"][0]
    assert out["missing_places"] == [{"path": "DIFC / GA / GV4 / L1 / R09", "level": "room", "records": [0, 1]}]
    assert placed() == {"sw-a": None, "sw-b": None}
