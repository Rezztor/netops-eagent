import asyncio
from pathlib import Path

import pytest

from helpers import api_policy, rbac
from plugins._ea_inventory.api.inventory_device import InventoryDevice
from plugins._ea_inventory.api.inventory_list import InventoryList
from plugins._ea_inventory.api.inventory_sources import InventorySources
from plugins._ea_inventory.api.inventory_sync_solarwinds import InventorySyncSolarwinds
from plugins._ea_inventory.helpers import proposals, store
from plugins._ea_solarwinds.helpers import bulk
from plugins._ea_solarwinds.helpers import config as sw_config

SETTINGS = {"enabled": True, "max_nodes": 2000, "stale_days": 7, "min_platform_confidence": 0.6}
NODE = {
    "node_id": "7", "caption": "sw7", "dns": "", "sys_name": "", "ip_address": "10.0.0.7", "vendor": "Fortinet",
    "machine_type": "FortiGate", "software_version": "7.2", "sys_object_id": "", "location": "", "status": "up",
    "serial": "FG7", "last_poll": None,
}


def call(handler_class, payload=None):
    handler = handler_class(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process(payload or {}, None))


@pytest.fixture(autouse=True)
def who(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")


@pytest.fixture
def on(database, events, monkeypatch):
    monkeypatch.setattr(store, "solarwinds_settings", lambda: SETTINGS)
    monkeypatch.setattr(sw_config, "is_configured", lambda: True)
    monkeypatch.setattr(bulk, "fetch_nodes", lambda max_nodes=2000, page_size=200: bulk.NodesResult([NODE], True, False, []))


def test_routes_are_gated():
    assert api_policy.required_permission_for("plugins/_ea_inventory/inventory_sources") == rbac.PERM_INVENTORY_VIEW
    assert api_policy.required_permission_for("plugins/_ea_inventory/inventory_sync_solarwinds") == rbac.PERM_INVENTORY_APPROVE
    for path in (Path(__file__).parents[1] / "api").glob("inventory_s*.py"):
        assert api_policy.required_permission_for(f"plugins/_ea_inventory/{path.stem}")


def test_migration_creates_the_run_table_with_its_status_check(database):
    with store.transaction() as conn:
        cols = {r["column_name"] for r in store.rows(conn, "SELECT column_name FROM information_schema.columns WHERE table_name = 'inventory_source_run'")}
        assert cols == {"id", "source", "started_at", "finished_at", "status", "counts", "error", "batch_id", "requested_by"}
    rid = store.start_run("solarwinds", "alice")
    with pytest.raises(Exception, match="check"):
        with store.transaction() as conn:
            conn.execute("UPDATE inventory_source_run SET status = 'bogus' WHERE id = %s", (rid,))


def test_default_config_ships_the_feed_disabled():
    from helpers import plugins

    cfg = plugins.get_default_plugin_config("_ea_inventory")["solarwinds"]
    assert cfg == {"enabled": False, "max_nodes": 2000, "stale_days": 7, "min_platform_confidence": 0.6}


def test_sources_shape_before_and_after_a_run(on):
    [s] = call(InventorySources)["sources"]
    assert s == {"source": "solarwinds", "enabled": True, "configured": True, "last_run": None, "nodes_seen": 0, "stale_count": 0}
    assert call(InventorySyncSolarwinds, {"dry_run": True})["status"] == "dry_run"
    [s] = call(InventorySources)["sources"]
    assert set(s["last_run"]) == {"id", "status", "started_at", "finished_at", "counts", "error", "batch_id"}
    assert s["last_run"]["status"] == "dry_run" and s["last_run"]["counts"]["create"] == 1 and s["nodes_seen"] == 0


def test_sources_reports_disabled_and_unconfigured(database, monkeypatch):
    monkeypatch.setattr(store, "solarwinds_settings", lambda: {**SETTINGS, "enabled": False})
    monkeypatch.setattr(sw_config, "is_configured", lambda: False)
    [s] = call(InventorySources)["sources"]
    assert s["enabled"] is False and s["configured"] is False


def test_sync_handler_shape_and_refusal(on, monkeypatch):
    out = call(InventorySyncSolarwinds, {})
    assert out["ok"] and out["status"] == "succeeded" and out["batch_id"]
    assert set(out) >= {"run_id", "status", "counts", "batch_id", "needs_review", "missing_columns"}
    monkeypatch.setattr(store, "solarwinds_settings", lambda: {**SETTINGS, "enabled": False})
    refused = call(InventorySyncSolarwinds, {})
    assert refused["ok"] is False and "disabled" in refused["error"]


def test_devices_carry_sources_and_stale(on):
    call(InventorySyncSolarwinds, {})
    for p in proposals.list_proposals(status="pending")[0]:
        proposals.decide(p["id"], "approve", actor="alice")
    [d] = call(InventoryList)["devices"]
    assert d["sources"][0]["source_id"] == "7"  # a node seen before its device existed is linked by the approved request
    call(InventorySyncSolarwinds, {})
    [d] = call(InventoryList)["devices"]
    [src] = d["sources"]
    assert (src["source"], src["source_id"], src["stale"]) == ("solarwinds", "7", False) and src["last_seen_at"]
    with store.transaction() as conn:
        conn.execute("UPDATE inventory_observation SET observed_at = now() - interval '9 days'")
    assert call(InventoryDevice, {"id": d["id"]})["device"]["sources"][0]["stale"] is True
    assert call(InventoryList)["devices"][0]["sources"][0]["stale"] is True
    assert store.source_stats("solarwinds", 7)["stale_count"] == 1


def test_no_agent_tool_touches_the_feed():
    for path in (Path(__file__).parents[1] / "tools").glob("*.py"):
        assert "adapters" not in path.read_text()
