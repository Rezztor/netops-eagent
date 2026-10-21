import asyncio
import datetime as dt

import pytest

from helpers import api_policy, rbac
from plugins._ea_inventory.api.inventory_summary import InventorySummary
from plugins._ea_inventory.helpers import conflicts, places, proposals, reports, store


def call():
    handler = InventorySummary(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process({}, None))


@pytest.fixture(autouse=True)
def who(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")


def test_summary_counts_live_devices_open_queues_and_caps_stale_names(make):
    assert api_policy.required_permission_for("plugins/_ea_inventory/inventory_summary") == rbac.PERM_INVENTORY_VIEW
    today = reports.today()
    expired, soon, ok = ((today + dt.timedelta(days=n)).isoformat() for n in (-1, 30, 400))
    places.create(None, "HQ", "HQ", chain=[("Main", "M"), ("Main", "M")])
    places.create(None, "OLD", "OLD")
    places.update("OLD", active=False)
    for name in ("stale-a", "stale-b", "stale-c", "stale-d"):
        make(name, status="active")
    seen = make("seen-expired", status="active", warranty_expiry=expired, support_expiry=soon)
    soon_dev = make("seen-soon", status="active", eol_date=soon)
    ok_dev = make("seen-ok", status="active", warranty_expiry=ok)
    make("planned-one")
    make("gone", status="decommissioned", warranty_expiry=expired)
    for device in (seen, soon_dev, ok_dev):
        store.add_observation(device["id"], "facts", device["name"])
    conflicts.upsert_conflicts(
        seen["id"], "facts",
        [{"field": "model", "current": "", "proposed": "C9300", "current_source": "manual"}],
        {"source": "facts"},
    )
    proposals.propose("create", None, {"name": "queued"}, actor="alice")

    out = call()
    assert out == {
        "ok": True,
        "devices": 8,
        "sites": 1,
        "pending": 1,
        "conflicts": 1,
        "lifecycle": {"expired": 1, "soon": 1},
        "incomplete": 8,
        "average": out["average"],
        "stale": {"count": 4, "names": ["stale-a", "stale-b", "stale-c"]},
    }
    assert out["average"] == reports.quality(store.list_devices(), reports.settings()["required"], "room")["average"]
    assert out["average"] < 100
