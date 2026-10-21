import asyncio
import datetime as dt

import pytest

from plugins._ea_inventory.api.inventory_device import InventoryDevice
from plugins._ea_inventory.api.inventory_lifecycle import InventoryLifecycle
from plugins._ea_inventory.api.inventory_list import InventoryList
from plugins._ea_inventory.api.inventory_quality import InventoryQuality
from plugins._ea_inventory.api.inventory_racks import InventoryRacks
from plugins._ea_inventory.helpers import places, proposals, reports

TODAY = dt.date(2026, 10, 2)
REQUIRED = ["name", "device_type", "role", "vendor", "model", "serial", "mgmt_ip", "place_id", "status"]


def dev(name="sw1", **kw):
    base = {"id": name, "name": name, "status": "active", "platform": "", "sources": [], "site": None, "vendor": "", "model": "",
            "device_type": "other", "role": "", "serial": "", "mgmt_ip": "", "place_id": None, "rack_unit": None, "u_height": None, "warranty_expiry": None, "support_expiry": None,
            "eol_date": None}
    return base | kw


def comp(**kw):
    return reports.computed(dev(**kw), today=TODAY, required=REQUIRED, stale_days=7)


def test_computed_access_monitored_completeness_lifecycle():
    assert comp()["access_method"] == "" and comp(platform="ios")["access_method"] == "ssh"
    assert comp(platform="fortios")["access_method"] == "https_api"
    sw = {"source": "solarwinds", "stale": False}
    assert comp(sources=[sw])["monitored"] and not comp(sources=[{**sw, "stale": True}])["monitored"]
    assert not comp(sources=[{"source": "facts", "stale": False}])["monitored"]
    c = comp(name="a", status="active", device_type="router")["completeness"]  # "other" and "" count as empty
    assert c == {"percent": 33, "missing": ["role", "vendor", "model", "serial", "mgmt_ip", "place_id"]}
    life = comp(warranty_expiry="2026-10-01", support_expiry="2026-12-31", eol_date="2030-01-01")
    assert [life["lifecycle"][k]["state"] for k in ("warranty", "support", "eol")] == ["expired", "soon", "ok"]
    assert life["lifecycle"]["warranty"] == {"date": "2026-10-01", "days": -1, "state": "expired"}
    assert life["lifecycle_state"] == "expired" and comp()["lifecycle_state"] == "none"
    assert comp(eol_date="2026-10-02")["lifecycle"]["eol"]["state"] == "soon"  # today is 0 days left
    assert comp(eol_date="2027-01-01")["lifecycle"]["eol"]["state"] == "ok"  # 91 days


def test_reports_ignore_decommissioned_and_order_results():
    fleet = [
        dev("a", warranty_expiry="2026-11-01", site={"id": "1", "name": "HQ"}), dev("b", warranty_expiry="2026-09-01"),
        dev("gone", status="decommissioned", warranty_expiry="2020-01-01"), dev("c", support_expiry="2030-01-01"),
    ]
    out = reports.lifecycle(fleet, today=TODAY, within_days=90)
    assert [(i["name"], i["state"], i["site"]) for i in out["items"]] == [("b", "expired", ""), ("a", "soon", "HQ")]
    assert out["counts"]["warranty"] == {"expired": 1, "soon": 1, "ok": 0, "missing": 1}
    assert out["no_dates"] == 0
    assert reports.lifecycle([dev("x"), dev("y", eol_date="2030-01-01")], today=TODAY, within_days=90)["no_dates"] == 1
    assert [i["name"] for i in reports.lifecycle(fleet, today=TODAY, within_days=10, kind="support")["items"]] == []
    q = reports.quality(fleet, ["name", "role"])
    assert (q["total"], q["complete"], q["average"]) == (3, 0, 50)
    assert q["by_field"] == [{"field": "name", "missing": 0}, {"field": "role", "missing": 3}]
    assert len(reports.quality([dev(str(i)) for i in range(600)], ["role"])["devices"]) == 500


LEVELS = ["site", "location", "building", "floor", "room", "rack"]
ROOM, RK1, RK2, SPARE = "DIFC-GA-GV4-L1-R02", "DIFC-GA-GV4-L1-R02-RK01", "DIFC-GA-GV4-L1-R02-RK02", "DIFC-GA-GV4-L1-R02-RK09"


def tree(*ids, active=()):
    """A place list that holds every ancestor of `ids`, each named after its code."""
    every = sorted({a for i in ids for a in places.ancestors(i)})
    return [{"id": i, "kind": places.kind_of(i), "name": i.split("-")[-1].title(), "active": i not in active} for i in every]


def test_completeness_asks_for_the_configured_depth():
    for level in LEVELS:
        for depth_ in range(1, 7):
            here = "-".join(["A", "B", "C", "D", "E", "F"][:depth_])
            got = reports.completeness(dev(place_id=here), ["place_id"], level)["missing"]
            assert got == (["place_id"] if depth_ <= LEVELS.index(level) else []), (level, depth_)
    assert reports.completeness(dev(), ["place_id"])["missing"] == ["place_id"]
    assert reports.settings()["place_level"] == "room" and "place_id" in reports.settings()["required"]


def test_racks_group_by_id_order_and_used_u_and_keep_empty_racks():
    fleet = [
        dev("low", place_id=RK1, rack_unit=1, u_height=2), dev("top", place_id=RK1, rack_unit=40), dev("loose", place_id=RK1),
        dev("other", place_id=RK2, rack_unit=5, u_height=1), dev("free"), dev("inroom", place_id=ROOM),
        dev("gone", place_id=RK1, status="decommissioned"), dev("old", rack="R1", rack_unit=3),
    ]
    out = reports.racks(fleet, tree(RK1, RK2, SPARE, "LAB-M-M-L1-R01-RK01", active={SPARE}))
    assert out["unplaced"] == 3  # free, inroom and the one with only old text
    assert [r["place_id"] for r in out["racks"]] == ["DIFC-GA-GV4-L1-R02-RK01", "DIFC-GA-GV4-L1-R02-RK02", SPARE, "LAB-M-M-L1-R01-RK01"]
    r1 = out["racks"][0]
    assert [d["name"] for d in r1["devices"]] == ["top", "low", "loose"] and r1["used_u"] == 3
    assert (r1["site"], r1["location"], r1["room"], r1["rack"]) == ("Difc", "Ga", "R02", "Rk01")
    assert out["racks"][2]["devices"] == [] and out["racks"][2]["used_u"] == 0
    here = reports.racks(fleet, tree(RK1, "LAB-M-M-L1-R01-RK01"), "LAB")
    assert [r["place_id"] for r in here["racks"]] == ["LAB-M-M-L1-R01-RK01"]


def call(handler_class, payload=None):
    handler = handler_class(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process(payload or {}, None))


@pytest.fixture(autouse=True)
def who(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")


@pytest.fixture
def rack(database, events):
    places.create(None, "DIFC", "DIFC", chain=[("Gate Avenue", "GA"), ("Gate Village 4", "GV4")])
    places.create("DIFC-GA-GV4", "Level 1", "L1", chain=[("Room 2", "R02"), ("Rack 1", "RK01")])
    places.create("DIFC-GA-GV4-L1-R02", "Rack 2", "RK02")


def test_endpoints_over_real_devices(make, rack):
    soon = (reports.today() + dt.timedelta(days=30)).isoformat()
    a = make("a", device_type="router", role="core", place_id=RK1, rack_unit=10, u_height=2, warranty_expiry=soon)
    make("b", status="planned")
    out = call(InventoryLifecycle, {"within_days": 90, "kind": "warranty"})
    assert out["ok"] and [(i["name"], i["state"], i["days"]) for i in out["items"]] == [("a", "soon", 30)]
    assert call(InventoryLifecycle, {"kind": "x"})["ok"] is False and call(InventoryLifecycle, {"within_days": "x"})["ok"] is False
    q = call(InventoryQuality)
    assert q["total"] == 2 and q["complete"] == 0 and q["required"] == REQUIRED
    assert {d["name"] for d in q["devices"]} == {"a", "b"} and q["devices"][0]["name"] == "b"  # worst first
    r = call(InventoryRacks)
    assert r["unplaced"] == 1 and [x["place_id"] for x in r["racks"]] == [RK1, RK2]
    assert r["racks"][0]["devices"][0]["device_id"] == a["id"] and r["racks"][1]["devices"] == []
    assert [x["place_id"] for x in call(InventoryRacks, {"place": "difc-ga-gv4-l1-r02-rk02"})["racks"]] == [RK2]
    assert call(InventoryLifecycle, {"place": "NOPE"})["items"] == []
    listed = call(InventoryList, {"device_type": "router"})
    assert [d["name"] for d in listed["devices"]] == ["a"] and listed["devices"][0]["computed"]["lifecycle_state"] == "soon"
    assert call(InventoryDevice, {"id": a["id"]})["device"]["computed"]["completeness"]["percent"] == 56  # now placed
