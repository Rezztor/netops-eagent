"""Template header and the lookup's compact/expiry view. Only the last test needs the database."""

import asyncio
import csv
import datetime as _dt
import io
import json

from plugins._ea_inventory.api.inventory_template import InventoryTemplate, content
from plugins._ea_inventory.helpers.normalise import FIELDS
from plugins._ea_inventory.helpers import places, store
from plugins._ea_inventory.tools.inventory_lookup import InventoryLookup, compact, matching_expiry


def _run(tool):
    return asyncio.new_event_loop().run_until_complete(tool.execute()).message


def _device(name, **fields):
    device = {
        "id": name, "name": name, "status": "active", "vendor": "", "model": "",
        "site": {"name": "Jakarta"}, "warranty_expiry": None, "support_expiry": None, "eol_date": None,
    }
    device.update(fields)
    return device


def test_template_is_one_header_row():
    row = next(csv.reader(io.StringIO(content())))
    assert "custom" not in row and "place_id" in row
    assert row == [n for n in FIELDS if n != "custom"] + ["site", "location", "building", "floor", "room", "rack"]
    handler = InventoryTemplate(app=None, thread_lock=None)
    out = asyncio.new_event_loop().run_until_complete(handler.process({}, None))
    assert out["filename"] == "inventory-template.csv" and out["content_type"] == "text/csv"
    assert out["content"] == content()


def test_compact_hides_names_and_free_text():
    row = compact(_device(
        "sw1", device_type="switch", role="access", place_id="HQ-M-HQ-L1-MDF-R07", location="Main", building="HQ", floor="Level 1", room="MDF", rack="R07",
        rack_unit=22, department="net", warranty_expiry="2027-01-31",
        custodian="Ada", support_contact="Bob", custom={"note": "x"}, field_sources={"role": {}},
    ))
    assert row["device_type"] == "switch" and row["rack_unit"] == 22 and row["site"] == "Jakarta"
    assert (row["place_id"], row["location"], row["floor"]) == ("HQ-M-HQ-L1-MDF-R07", "Main", "Level 1")
    assert not {"custodian", "support_contact", "custom", "field_sources"} & set(row)


def test_expiring_is_expired_or_soon_soonest_first():
    today = _dt.date(2026, 10, 2)
    devices = [
        _device("late", warranty_expiry="2027-10-02"),
        _device("soon", warranty_expiry="2026-11-01"),
        _device("gone", warranty_expiry="2026-01-01"),
        _device("none"),
        _device("retired", warranty_expiry="2026-01-01", status="decommissioned"),
        _device("support", support_expiry="2026-10-10", warranty_expiry="2028-01-01"),
        _device("both", warranty_expiry="2026-01-01", support_expiry="2026-10-03"),
    ]
    assert [d["name"] for d in matching_expiry(devices, within_days=90, kind=None, today=today)] == [
        "both", "gone", "support", "soon",
    ]
    assert [d["name"] for d in matching_expiry(devices, within_days=90, kind="support", today=today)] == [
        "both", "support",
    ]


def test_lookup_passes_filters_and_caps_expiry(monkeypatch):
    seen = {}

    def search(**kwargs):
        seen.update(kwargs)
        return ([_device("sw1", device_type="switch", custodian="Ada", custom={"n": 1})], 1)

    monkeypatch.setattr(store, "search_devices", search)
    tool = InventoryLookup.__new__(InventoryLookup)
    tool.args = {"device_type": "Switch", "site": "HQ", "query": "c9300"}
    body = json.loads(_run(tool).split("\n\n", 1)[1])
    assert seen["device_type"] == "Switch" and seen["site_name"] == "HQ" and seen["place"] == "" and "rack" not in seen and seen["q"] == "c9300"
    assert body[0]["name"] == "sw1" and "custodian" not in body[0] and "custom" not in body[0]

    monkeypatch.setattr(store, "list_devices", lambda **kw: [
        _device("gone", warranty_expiry="2026-01-01"), _device("late", warranty_expiry="2099-01-01"),
    ])
    tool.args = {"expiring_within_days": 30, "expiring_kind": "warranty"}
    head, payload = _run(tool).split("\n\n", 1)
    assert head.startswith("1 device(s)") and json.loads(payload)[0]["name"] == "gone"
    tool.args = {"expiring_kind": "warranty"}
    assert _run(tool).startswith("expiring_kind needs")
    tool.args = {"expiring_within_days": "soon"}
    assert _run(tool).startswith("expiring_within_days must")


def test_lookup_by_place_returns_every_device_under_it(make):
    places.create(None, "DIFC", "DIFC", actor="alice", chain=[("Gate Avenue", "GA"), ("Gate Village 4", "GV4"), ("Level 1", "L1")])
    places.create("DIFC-GA-GV4-L1", "Room 2", "R02", actor="alice", chain=[("Rack 1", "RK01")])
    places.create("DIFC", "Dubai Walk", "DW", actor="alice")
    make("sw-room", place_id="DIFC-GA-GV4-L1-R02")
    make("sw-rack", place_id="DIFC-GA-GV4-L1-R02-RK01", rack_unit=4)
    make("sw-dw", place_id="DIFC-DW")
    make("sw-none")
    tool = InventoryLookup.__new__(InventoryLookup)
    for text in ("DIFC-GA", "difc-ga", "Gate Avenue"):
        tool.args = {"place": text}
        head, payload = _run(tool).split("\n\n", 1)
        rows = json.loads(payload)
        assert head.startswith("2 device(s)") and {r["name"] for r in rows} == {"sw-room", "sw-rack"}, text
    assert {r["place_id"] for r in rows} == {"DIFC-GA-GV4-L1-R02", "DIFC-GA-GV4-L1-R02-RK01"}
    assert {r["location"] for r in rows} == {"Gate Avenue"} and {r["floor"] for r in rows} == {"Level 1"}
    tool.args = {"place": "DIFC-GA-GV4-L1-R02-RK01"}
    assert _run(tool).startswith("1 device(s)")
    tool.args = {"place": "Narnia"}
    assert _run(tool).startswith("No place is named")
