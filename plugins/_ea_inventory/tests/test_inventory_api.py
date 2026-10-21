import asyncio
import csv
import io
import json
import sys
import types
from pathlib import Path

import pytest

from helpers import api_policy, login, rbac
from plugins._ea_inventory.api.inventory_decide import InventoryDecide
from plugins._ea_inventory.api.inventory_device import InventoryDevice
from plugins._ea_inventory.api.inventory_export import COLUMNS, InventoryExport
from plugins._ea_inventory.api.inventory_list import InventoryList
from plugins._ea_inventory.api.inventory_propose import InventoryPropose
from plugins._ea_inventory.api.inventory_proposals import InventoryProposals
from plugins._ea_inventory import helpers as helpers_pkg
from plugins._ea_inventory.helpers import places, proposals
from plugins._ea_inventory.tools.inventory_lookup import InventoryLookup

API_DIR = Path(__file__).resolve().parents[1] / "api"
VIEW, MANAGE, APPROVE = rbac.PERM_INVENTORY_VIEW, rbac.PERM_INVENTORY_MANAGE, rbac.PERM_INVENTORY_APPROVE
GATES = {
    "inventory_list": VIEW, "inventory_device": VIEW, "inventory_proposals": VIEW,
    "inventory_places": VIEW, "inventory_place_save": MANAGE, "inventory_place_merge": None,  # the handler declares inventory.places; the table holds no entry
    "inventory_export": VIEW, "inventory_propose": MANAGE,
    "inventory_decide": APPROVE, "inventory_sync": APPROVE,
    "inventory_batches": VIEW, "inventory_decide_batch": APPROVE,
    "inventory_sources": VIEW, "inventory_sync_solarwinds": APPROVE,
    "inventory_conflicts": VIEW, "inventory_pins": VIEW, "inventory_stale": VIEW,
    "inventory_conflict_decide": APPROVE, "inventory_conflict_decide_many": APPROVE, "inventory_unpin": APPROVE,
    "inventory_propose_decommission_stale": MANAGE, "inventory_refresh_facts": MANAGE,
    "inventory_backups": VIEW, "inventory_backup_report": VIEW, "inventory_drift": VIEW, "inventory_backup_run": MANAGE,
    "inventory_backup_view": MANAGE, "inventory_backup_diff": MANAGE,
    "inventory_ports": VIEW, "inventory_vlan": VIEW, "inventory_refresh_ports": MANAGE, "inventory_port_uplink": MANAGE,
    "inventory_lifecycle": VIEW, "inventory_quality": VIEW, "inventory_racks": VIEW,
    "inventory_template": VIEW, "inventory_summary": VIEW, "inventory_schedule": VIEW, "inventory_propose_many": MANAGE,
}


def call(handler_class, payload):
    handler = handler_class(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process(payload, None))


@pytest.fixture(autouse=True)
def who(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")


def test_every_route_is_gated_and_the_handler_files_agree():
    for route, permission in GATES.items():
        assert api_policy.required_permission_for(f"plugins/_ea_inventory/{route}") == permission
    for path in API_DIR.glob("inventory_*.py"):
        assert path.stem in GATES, f"{path.stem} is not in the policy table"


def test_roles():
    for role in (rbac.ROLE_ADMIN, rbac.ROLE_POWER):
        assert {VIEW, MANAGE, APPROVE} <= rbac.get_permissions(role)
    assert not {VIEW, MANAGE, APPROVE} & rbac.get_permissions(rbac.ROLE_USER)


def test_approval_is_not_a_tool():
    for path in (API_DIR.parent / "tools").glob("*.py"):
        text = path.read_text()
        assert "decide" not in text and "propose" not in text and "APPROVE" not in text


def fake_sync(monkeypatch, fn):
    """Stand in for executor_sync, whether or not the real module exists yet."""
    fake = types.SimpleNamespace(sync_after_commit=fn)
    monkeypatch.setitem(sys.modules, "plugins._ea_inventory.helpers.executor_sync", fake)
    monkeypatch.setattr(helpers_pkg, "executor_sync", fake, raising=False)


def test_propose_approve_list_device_round_trip(database, events, monkeypatch):
    p = call(InventoryPropose, {"kind": "create", "fields": {"name": "sw1", "vendor": "cisco"}})
    assert p["ok"] and p["proposal"]["requested_by"] == "alice"
    assert call(InventoryProposals, {})["pending_count"] == 1
    fake_sync(monkeypatch, lambda: {"ok": True, "changed": True})
    d = call(InventoryDecide, {"id": p["proposal"]["id"], "decision": "approve"})
    assert d["ok"] and d["sync"] == {"ok": True, "changed": True} and d["device"]["name"] == "sw1"
    listed = call(InventoryList, {"q": "sw1"})
    assert listed["total"] == 1 and listed["facets"]["vendor"] == ["cisco"]
    detail = call(InventoryDevice, {"id": d["device"]["id"]})
    assert detail["proposals"][0]["status"] == "approved" and detail["observations"] == []
    assert call(InventoryDevice, {"id": "nope"})["ok"] is False


def test_approval_survives_a_broken_or_missing_sync(database, monkeypatch):
    p = call(InventoryPropose, {"kind": "create", "fields": {"name": "sw1"}})["proposal"]
    fake_sync(monkeypatch, lambda: 1 / 0)
    d = call(InventoryDecide, {"id": p["id"], "decision": "approve"})
    assert d["ok"] and d["sync"]["ok"] is False and d["device"]["name"] == "sw1"


def test_reject_has_no_sync_and_errors_are_reported(database):
    p = call(InventoryPropose, {"kind": "create", "fields": {"name": "sw1"}})["proposal"]
    r = call(InventoryDecide, {"id": p["id"], "decision": "reject", "note": "dup"})
    assert r["ok"] and "sync" not in r and r["proposal"]["status"] == "rejected"
    assert call(InventoryDecide, {"id": p["id"], "decision": "approve"})["ok"] is False
    assert call(InventoryDecide, {"id": p["id"], "decision": "maybe"})["ok"] is False
    assert call(InventoryPropose, {"kind": "create", "fields": {"nope": 1}})["ok"] is False


def test_export_formats_and_formula_cells(make):
    make("sw1", vendor="=cmd()", tags=["a", "b"], serial="s1")
    j = call(InventoryExport, {"format": "json"})
    assert j["content_type"] == "application/json" and json.loads(j["content"])[0]["name"] == "sw1"
    c = call(InventoryExport, {"format": "csv"})
    row = list(csv.DictReader(io.StringIO(c["content"])))[0]
    assert c["filename"] == "inventory.csv" and row["vendor"] == "'=cmd()" and row["tags"] == "a;b"
    assert call(InventoryExport, {"format": "xml"})["ok"] is False


def test_export_carries_the_asset_record_and_the_place(make):
    places.create(None, "DIFC", "DIFC", chain=[("Gate Avenue", "GA"), ("Gate Village 4", "GV4")])
    places.create("DIFC-GA-GV4", "Level 1", "L1", chain=[("Room 2", "R02"), ("Rack 1", "RK01")])
    make("sw1", device_type="switch", place_id="DIFC-GA-GV4-L1-R02-RK01", rack_unit=22, warranty_expiry="2027-01-31")
    make("sw2", place_id="DIFC-GA")
    c = call(InventoryExport, {"format": "csv"})
    assert {"device_type", "base_mac", "rack_unit", "u_height", "custodian", "eol_date"} <= set(COLUMNS)
    assert COLUMNS[COLUMNS.index("status") + 1:][:7] == ("place_id", "site", "location", "building", "floor", "room", "rack")
    one, two = list(csv.DictReader(io.StringIO(c["content"])))
    assert (one["device_type"], one["rack_unit"], one["warranty_expiry"], one["eol_date"]) == ("switch", "22", "2027-01-31", "")
    assert [one[k] for k in COLUMNS[8:15]] == ["DIFC-GA-GV4-L1-R02-RK01", "DIFC", "Gate Avenue", "Gate Village 4", "Level 1", "Room 2", "Rack 1"]
    assert (two["place_id"], two["location"], two["building"], two["rack"]) == ("DIFC-GA", "Gate Avenue", "", "")


def test_lookup_is_compact_capped_and_never_returns_custom(make):
    for i in range(27):
        make(f"sw{i:02}", vendor="cisco", custom={"note": "x"})
    tool = InventoryLookup.__new__(InventoryLookup)
    tool.args = {"vendor": "cisco"}
    out = asyncio.new_event_loop().run_until_complete(tool.execute()).message
    head, body = out.split("\n\n", 1)
    assert head.startswith("27 device(s) match") and "first 25" in head
    devices = json.loads(body)
    assert len(devices) == 25 and "custom" not in devices[0] and "field_sources" not in devices[0]
    tool.args = {"name": "SW01"}
    assert json.loads(asyncio.new_event_loop().run_until_complete(tool.execute()).message.split("\n\n", 1)[1])[0]["name"] == "sw01"


def test_lookup_says_which_of_the_appliances_networks_a_device_is_reached_through(make, monkeypatch):
    """Lab, 2026-10-11: asked which interface each router was reached through, the agent read its own sandbox and
    said "eth0" for all three. The approval card already knew."""
    from plugins._ea_ansible.helpers import executor_client, network_list

    listing = network_list.parse({"networks": [
        {"name": "Interface 1", "roles": ["internet", "users", "devices"], "subnet": "192.168.122.0/24", "address": "192.168.122.30/24"},
        {"name": "Interface 2", "roles": ["devices"], "subnet": "10.99.0.0/24", "address": "10.99.0.2/24",
         "gateway": "10.99.0.1", "routed_subnets": ["10.50.0.0/24"]},
    ]})
    monkeypatch.setattr(executor_client, "networks", lambda agent=None: listing)
    make("r1", mgmt_ip="192.168.122.11"); make("r2", mgmt_ip="10.50.0.2"); make("r3", mgmt_ip="172.16.9.9")
    tool = InventoryLookup.__new__(InventoryLookup)
    tool.args = {}
    found = json.loads(asyncio.new_event_loop().run_until_complete(tool.execute()).message.split("\n\n", 1)[1])
    assert {d["name"]: d["reached_through"] for d in found} == {"r1": "Interface 1", "r2": "Interface 2, routed", "r3": ""}

    def down(agent=None):
        raise executor_client.ExecutorError("no executor")

    monkeypatch.setattr(executor_client, "networks", down)
    found = json.loads(asyncio.new_event_loop().run_until_complete(tool.execute()).message.split("\n\n", 1)[1])
    assert "reached_through" not in found[0]


def test_lookup_permission_is_enforced_by_the_tool_layer():
    from helpers import tool_permissions

    assert tool_permissions.required_permission("inventory_lookup") == VIEW
    assert not tool_permissions.is_allowed("inventory_lookup", rbac.ROLE_USER)
    assert tool_permissions.is_allowed("inventory_lookup", rbac.ROLE_POWER)
