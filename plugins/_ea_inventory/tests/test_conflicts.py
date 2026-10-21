import pytest

from plugins._ea_inventory.helpers import conflicts, proposals, store
from plugins._ea_inventory.helpers.adapters import solarwinds
from plugins._ea_inventory.helpers.normalise import InventoryError
from plugins._ea_solarwinds.helpers import bulk
from plugins._ea_solarwinds.helpers import config as sw_config

C = {"field": "model", "current": "Human", "proposed": "C9300", "current_source": "manual"}


@pytest.fixture
def dev(make):
    return make("sw1", model="Human", serial="S1")


def upsert(dev, *items, source="facts", at=None):
    return conflicts.upsert_conflicts(dev["id"], source, list(items), {"source": source, "job_id": "j1", "batch_id": "b1"}, at)


def test_migration_creates_the_tables_with_their_checks_and_open_index(dev):
    with store.transaction() as conn:
        cols = {r["column_name"] for r in store.rows(conn, "SELECT column_name FROM information_schema.columns WHERE table_name = 'inventory_conflict'")}
        pins = {r["column_name"] for r in store.rows(conn, "SELECT column_name FROM information_schema.columns WHERE table_name = 'inventory_pin'")}
    assert cols == {"id", "device_id", "field", "current_value", "current_source", "proposed_value", "source", "evidence",
                    "observed_at", "status", "decided_by", "decided_at", "note", "created_at"}
    assert pins == {"device_id", "field", "source", "pinned_by", "pinned_at"}
    upsert(dev, C)
    with pytest.raises(Exception, match="check"):
        with store.transaction() as conn:
            conn.execute("UPDATE inventory_conflict SET status = 'bogus'")


def test_upsert_dedupes_refreshes_and_supersedes_a_changed_observation(dev):
    assert upsert(dev, C) == {"created": 1, "refreshed": 0, "superseded": 0}
    assert upsert(dev, C) == {"created": 0, "refreshed": 1, "superseded": 0}
    assert upsert(dev, {**C, "proposed": "C9500"}) == {"created": 1, "refreshed": 0, "superseded": 1}
    assert upsert(dev, C, source="solarwinds")["created"] == 1  # a different source has its own
    rows, total, open_count = conflicts.list_conflicts("open")
    assert total == open_count == 2 and {r["proposed_value"] for r in rows} == {"C9500", "C9300"}
    assert [r["status"] for r in conflicts.list_conflicts("superseded")[0]] == ["superseded"]
    assert conflicts.list_conflicts("open", device_id=dev["id"])[1] == 2


def test_accept_applies_with_the_conflict_source_and_the_deciding_engineer(dev):
    upsert(dev, C)
    [c] = conflicts.list_conflicts()[0]
    conflict, proposal, device = conflicts.decide_conflict(c["id"], "accept", "yes", actor="bob")
    assert conflict["status"] == "accepted" and conflict["decided_by"] == "bob" and conflict["note"] == "yes"
    assert proposal["source"] == "facts" and proposal["evidence"]["conflict_id"] == c["id"] and "batch_id" not in proposal["evidence"]
    assert proposal["status"] == "approved" and proposal["decided_by"] == "bob"
    assert device["model"] == "C9300"
    assert device["field_sources"]["model"]["source"] == "facts" and device["field_sources"]["model"]["approved_by"] == "bob"
    with pytest.raises(InventoryError, match="already"):
        conflicts.decide_conflict(c["id"], "accept", actor="bob")


def test_accept_of_a_custom_key_merges_into_custom(make):
    d = make("sw2", custom={"rack": "r1"})
    upsert(d, {"field": "custom.ha_role", "current": None, "proposed": "primary", "current_source": "manual"})
    [c] = conflicts.list_conflicts()[0]
    assert conflicts.decide_conflict(c["id"], "accept", actor="bob")[2]["custom"] == {"rack": "r1", "ha_role": "primary"}


def test_accept_on_a_device_that_already_has_the_value_or_moved_on_marks_superseded(dev):
    upsert(dev, C)
    p = proposals.propose("update", dev["id"], {"model": "C9300"}, actor="carol")
    proposals.decide(p["id"], "approve", actor="carol")
    [c] = conflicts.list_conflicts()[0]
    conflict, proposal, device = conflicts.decide_conflict(c["id"], "accept", actor="bob")
    assert conflict["status"] == "superseded" and proposal is None and device is None


def test_keep_suppresses_the_same_value_but_not_a_new_one(dev):
    upsert(dev, C)
    [c] = conflicts.list_conflicts()[0]
    assert conflicts.decide_conflict(c["id"], "keep", "fine", actor="bob")[0]["status"] == "kept"
    pins, kept = conflicts.load_pins_and_kept(dev["id"])
    assert pins == set() and kept == {("model", "facts", "C9300")}
    from plugins._ea_inventory.helpers import precedence
    d = store.get_device(dev["id"])
    assert precedence.plan(d, {"model": "C9300"}, "facts", pins, kept).ignored == ["model"]
    assert precedence.plan(d, {"model": "C9500"}, "facts", pins, kept).conflicts
    assert store.get_device(dev["id"])["model"] == "Human"


def test_pin_blocks_and_unpin_restores(dev, events):
    from plugins._ea_inventory.helpers import precedence
    upsert(dev, C)
    [c] = conflicts.list_conflicts()[0]
    assert conflicts.decide_conflict(c["id"], "pin", actor="bob")[0]["status"] == "pinned"
    pins, kept = conflicts.load_pins_and_kept(dev["id"])
    assert pins == {("model", "facts")}
    assert precedence.plan(store.get_device(dev["id"]), {"model": "Z"}, "facts", pins, kept).pinned == ["model"]
    [pin] = conflicts.list_pins()
    assert (pin["device_name"], pin["field"], pin["source"], pin["pinned_by"]) == ("sw1", "model", "facts", "bob")
    conflicts.unpin(dev["id"], "model", "facts", actor="carol")
    assert conflicts.load_pins_and_kept(dev["id"]) == (set(), set())
    assert precedence.plan(store.get_device(dev["id"]), {"model": "Z"}, "facts", set(), set()).conflicts
    with pytest.raises(InventoryError, match="no such pin"):
        conflicts.unpin(dev["id"], "model", "facts")
    names = [e for e, _ in events]
    assert {"inventory.pinned", "inventory.unpinned", "inventory.conflict_decided"} <= set(names)


def test_audit_events_carry_ids_and_field_names_not_values(dev, events):
    upsert(dev, {**C, "proposed": "SECRET-LOOKING-VALUE"})
    [c] = conflicts.list_conflicts()[0]
    conflicts.decide_conflict(c["id"], "pin", "my note", actor="bob")
    for name, fields in events:
        if name in ("inventory.conflict_decided", "inventory.pinned"):
            assert "SECRET-LOOKING-VALUE" not in repr(fields) and "Human" not in repr(fields)
    [e] = [f for n, f in events if n == "inventory.conflict_decided"]
    assert e["field"] == "model" and e["device_id"] == dev["id"] and e["decision"] == "pin"


def test_bad_decision_and_unknown_id(dev):
    with pytest.raises(InventoryError, match="decision"):
        conflicts.decide_conflict("x", "burn")
    with pytest.raises(InventoryError, match="no such conflict"):
        conflicts.decide_conflict("00000000-0000-0000-0000-000000000000", "keep")


# --- the SolarWinds hook: a human's value is a conflict, not a silent skip ----------------

SETTINGS = {"enabled": True, "max_nodes": 2000, "stale_days": 7, "min_platform_confidence": 0.6}
NODE = {"node_id": "7", "caption": "sw1", "dns": "", "sys_name": "", "ip_address": "10.0.0.7", "vendor": "Cisco",
        "machine_type": "C9300", "software_version": "", "sys_object_id": "", "location": "", "status": "up",
        "serial": "S1", "last_poll": None}


@pytest.fixture
def sw(database, events, monkeypatch):
    monkeypatch.setattr(store, "solarwinds_settings", lambda: SETTINGS)
    monkeypatch.setattr(sw_config, "is_configured", lambda: True)
    monkeypatch.setattr(bulk, "fetch_nodes", lambda max_nodes=2000, page_size=200: bulk.NodesResult([NODE], True, False, []))


def test_solarwinds_disagreeing_with_a_human_becomes_a_conflict(sw, dev):
    out = solarwinds.run_solarwinds_sync(False, "alice")
    assert out["status"] == "succeeded" and out["counts"]["conflicts"] == 1
    assert out["counts"]["update"] == 1  # vendor was empty and is filled
    [c] = conflicts.list_conflicts()[0]
    assert (c["field"], c["current_value"], c["proposed_value"], c["source"], c["current_source"]) == ("model", "Human", "C9300", "solarwinds", "manual")
    assert solarwinds.run_solarwinds_sync(False, "alice")["counts"]["conflicts"] == 1
    assert conflicts.list_conflicts()[1] == 1  # the repeat refreshed, not duplicated


def test_solarwinds_dry_run_counts_conflicts_but_stores_none(sw, dev):
    assert solarwinds.run_solarwinds_sync(True, "alice")["counts"]["conflicts"] == 1
    assert conflicts.list_conflicts()[1] == 0


def test_solarwinds_honours_pins_and_kept(sw, dev):
    solarwinds.run_solarwinds_sync(False, "alice")
    [c] = conflicts.list_conflicts()[0]
    conflicts.decide_conflict(c["id"], "keep", actor="bob")
    assert solarwinds.run_solarwinds_sync(False, "alice")["counts"]["conflicts"] == 0
    assert conflicts.list_conflicts()[1] == 0
