"""A whole pull against the stand-in Azure: nothing here contacts Microsoft or needs a real credential."""

from __future__ import annotations

import datetime as dt
import json
import time

import pytest

from helpers import db
from plugins._audit.helpers import audit
from plugins._ea_azure.helpers import categories, client, collect, config, credential, store
from plugins._ea_azure.tests.fake_azure import FakeAzure
from plugins._ea_azure.tests.fixtures.env import install
from plugins._ea_azure.tests.fixtures.tenant import CLIENT_ID, SUB_DEV, SUB_PROD, SUB_SHARED, TENANT_ID, generated_tenant, hand_tenant

TABLES = ("cloud_account", "cloud_container", "cloud_resource", "cloud_relation", "cloud_change", "cloud_signal",
          "inventory_source_run")


@pytest.fixture
def world(database, monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    config.save({"tenant_id": TENANT_ID, "client_id": CLIENT_ID})
    credential.set_secret("fake-client-secret", "")
    with FakeAzure(hand_tenant()) as fake:
        monkeypatch.setattr(client, "TEST_ENDPOINTS", fake.cloud)
        client.reset()
        yield fake


def sql(query, *args):
    with db.transaction(db.NO_SCOPE) as conn:
        cur = conn.execute(query, args)
        return cur.fetchall() if cur.description else []


def pull(trigger="schedule"):
    return collect.pull(trigger, "tester")


def test_a_full_pull_of_the_hand_tenant(world):
    out = pull("screen")
    c = out["counts"]
    assert out["status"] == "succeeded" and out["error"] == ""
    assert c["subscriptions"] == {"listed": 3, "read": 3, "failed": 0, "skipped": 0}
    assert c["resources"] == len(world.tenant["resources"]) + 4                  # the four subnets are rows of their own
    assert (c["added"], c["changed"], c["removed"], c["returned"]) == (0, 0, 0, 0)    # a first read is a baseline
    assert c["unknown_types"] == ["microsoft.contoso/widgets"]
    assert [(p["status"], p["error"]) for p in c["per_subscription"]] == [("ok", "")] * 3
    assert sql("SELECT count(*) FROM cloud_change") == [(0,)]

    tree = store.tree()
    by_name = {s["name"]: s for s in tree["subscriptions"]}
    assert set(by_name) == {"Production", "Non-production", "Shared services"}
    assert by_name["Production"]["props"] == {"management_groups": [{"id": "mg-corp", "name": "Corp"}]}
    for sub_id, sub in ((SUB_PROD, by_name["Production"]), (SUB_DEV, by_name["Non-production"]), (SUB_SHARED, by_name["Shared services"])):
        rows = [r for r in world.tenant["resources"] if r["subscriptionId"] == sub_id]
        want = {}
        for r in rows:
            want[categories.category_of(r["type"])] = want.get(categories.category_of(r["type"]), 0) + 1
        subnets = 3 if sub_id == SUB_PROD else 1 if sub_id == SUB_DEV else 0
        want["networking"] = want.get("networking", 0) + subnets
        assert sub["categories"] == want and sub["count"] == sum(want.values())
        assert sum(g["count"] for g in sub["groups"]) == sub["count"]
    run = store.runs(1)[0]
    assert run["status"] == "succeeded" and run["counts"]["resources"] == c["resources"]
    assert store.as_of()["last_pull"]["id"] == out["run_id"]


def test_what_the_screen_and_the_tool_will_read_is_there(world):
    pull()
    hub = store.get("vnet-prod-hub")
    kids = {k["name"]: k for k in hub["children"]}
    assert {r["kind"]: r["name"] for r in kids["snet-app"]["links_out"]} == {"route_table": "rt-app", "nsg": "nsg-app"}
    assert kids["snet-data"]["links_out"] == []
    peers = {r["props"]["name"]: r for r in hub["links_out"] if r["kind"] == "peering"}
    assert peers["hub-to-dev"]["id"] and peers["hub-to-partner"]["id"] is None
    assert store.get("vm-dev-1")["state"] == "stopped" and store.get("vm-dev-1")["state_detail"] == "PowerState/deallocated"
    assert store.get("app-portal-prod")["props"]["runtime"] == "NODE|20-lts"
    assert {r["name"] for r in store.get("rt-app")["links_in"]} == {"snet-app"}
    assert store.get("func-jobs-prod")["label"] == "Function app"
    assert [r["name"] for r in store.search({"without": "route_table"}, 50)[0]] == ["snet-data", "snet-dev", "snet-egress"]


def test_a_pull_with_planted_secrets_stores_none_of_them(world):
    markers = world.plant_secrets()
    pull()
    dump = " ".join(row[0] for table in TABLES for row in sql(f"SELECT row_to_json(t)::text FROM {table} t"))
    events = json.dumps([e for e in audit._recent if str(e.get("event", "")).startswith("azure.")], default=str)
    assert len(markers) >= 7 and "azure.pull_finished" in events
    assert not [m for m in markers if m in dump or m in events]
    assert "azureuser" not in dump and "NODE|20-lts" in dump


def test_the_second_pull_records_exactly_what_changed(world):
    pull()
    world.set_property("rt-app", "routes.0.properties.nextHopIpAddress", "10.10.0.9")
    gone_id = world.find("app-api-prod")["id"]
    world.remove("app-api-prod")
    c = pull()["counts"]
    assert (c["added"], c["changed"], c["removed"], c["returned"]) == (0, 1, 1, 0)
    rows = {(r["resource"]["name"], r["change"]) for r in store.changes(store.runs(1)[0]["id"])[0]}
    assert rows == {("rt-app", "changed"), ("app-api-prod", "removed")}
    assert store.get(gone_id)["removed_at"]
    assert all(r["name"] != "app-api-prod" for r in store.search({}, 100)[0])


def test_a_failed_page_fails_that_subscription_and_touches_nothing_of_it(world):
    pull()
    dev_id = world.find("app-portal-dev")["id"]
    world.remove("app-portal-dev")
    world.fail_page = (SUB_DEV, 1, 500)
    out = pull()
    states = {p["name"]: p for p in out["counts"]["per_subscription"]}
    assert out["status"] == "partial" and out["counts"]["subscriptions"] == {"listed": 3, "read": 2, "failed": 1, "skipped": 0}
    assert states["Non-production"]["status"] == "failed" and "500" in states["Non-production"]["error"]
    assert states["Production"]["status"] == "ok"
    assert store.get(dev_id)["removed_at"] is None                              # a failed read proves nothing
    assert out["counts"]["removed"] == 0
    sub = next(s for s in store.as_of()["subscriptions"] if s["name"] == "Non-production")
    assert sub["readable"] and sub["last_read_at"]                              # still the date of its last complete read
    world.fail_page = None
    assert pull()["counts"]["removed"] == 1                                     # the next complete read decides


def test_a_subscription_that_drops_out_of_the_list_is_marked_not_readable_and_loses_nothing(world):
    pull()
    before = store.search({}, 500)[1]
    world.hide_subscription.add(SUB_SHARED)
    out = pull()
    assert out["status"] == "succeeded" and out["counts"]["subscriptions"]["listed"] == 2
    shared = next(s for s in store.as_of()["subscriptions"] if s["name"] == "Shared services")
    assert shared["readable"] is False
    assert store.search({}, 500)[1] == before and out["counts"]["removed"] == 0


def test_a_failed_sign_in_applies_nothing_and_is_recorded(world):
    world.sign_in_error = "secret"
    out = pull()
    assert out["status"] == "failed" and "AADSTS7000215" in out["error"] and "fake-client-secret" not in out["error"]
    assert sql("SELECT count(*) FROM cloud_container") == [(0,)]
    assert store.runs(1)[0]["status"] == "failed" and store.as_of()["last_pull"] is None
    world.sign_in_error = None
    client.reset()
    pull()
    world.sign_in_error = "expired"
    client.reset()
    before = sql("SELECT count(*), max(last_seen_at) FROM cloud_resource")
    assert pull()["status"] == "failed"
    assert sql("SELECT count(*), max(last_seen_at) FROM cloud_resource") == before


def test_subscription_settings_choose_what_is_read(world):
    config.save({"subscriptions_mode": "only", "subscription_ids": [SUB_PROD]})
    c = pull()["counts"]["subscriptions"]
    assert c == {"listed": 3, "read": 1, "failed": 0, "skipped": 2}
    config.save({"subscriptions_mode": "except", "subscription_ids": [SUB_PROD, SUB_DEV]})
    assert pull()["counts"]["subscriptions"]["read"] == 1
    world.tenant["subscriptions"][2]["state"] = "Deleted"
    config.save({"subscriptions_mode": "all"})
    out = pull()
    assert out["counts"]["subscriptions"] == {"listed": 3, "read": 2, "failed": 0, "skipped": 1}


def test_busy_too_soon_and_the_schedules_exemption(world):
    pull("screen")
    with pytest.raises(collect.TooSoon):
        pull("screen")
    with pytest.raises(collect.TooSoon):
        pull("chat")
    assert pull("schedule")["status"] == "succeeded"
    sql("UPDATE inventory_source_run SET status = 'running', finished_at = NULL WHERE id = (SELECT id FROM inventory_source_run ORDER BY started_at DESC LIMIT 1)")
    for trigger in ("screen", "schedule"):
        with pytest.raises(collect.Busy):
            pull(trigger)


def test_a_stuck_run_is_closed_after_thirty_minutes(world):
    run = collect.inv.start_run("azure", "x")
    assert collect.status()["running"]["id"] == run
    sql("UPDATE inventory_source_run SET started_at = now() - interval '31 minutes' WHERE id = %s", run)
    after = collect.status()
    assert after["running"] is None and after["last_run"]["id"] == run and after["last_run"]["status"] == "failed"
    assert "30 minutes" in after["last_run"]["error"]
    assert pull("schedule")["status"] == "succeeded"


def test_nothing_starts_when_off_or_not_set_up(world, monkeypatch):
    monkeypatch.setattr(config, "is_enabled", lambda: False)
    with pytest.raises(client.NotConfigured):
        pull()
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    credential.clear()
    with pytest.raises(client.NotConfigured):
        collect.start("screen", "t")
    assert sql("SELECT count(*) FROM inventory_source_run") == [(0,)]


def test_start_runs_in_the_background_and_status_follows_it(world):
    run = collect.start("screen", "tester")
    deadline = time.monotonic() + 30
    while collect.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
    last = collect.status()["last_run"]
    assert last["id"] == run and last["status"] == "succeeded" and last["requested_by"] == "tester"


def test_a_tenant_past_the_limit_stops_and_is_recorded_failed(world):
    config.save({"max_resources": 10})
    out = pull()
    assert out["status"] == "failed" and "more than 10 resources" in out["error"]
    assert sql("SELECT count(*) FROM cloud_resource") == [(0,)]


def test_the_audit_lines_carry_counts_and_no_names(world):
    pull("screen")
    mine = [e for e in audit._recent if e.get("event") in ("azure.pull_started", "azure.pull_finished")][-2:]
    assert [e["event"] for e in mine] == ["azure.pull_started", "azure.pull_finished"]
    assert mine[0]["trigger"] == "screen" and mine[0]["requested_by"] == "tester"
    done = mine[1]
    assert done["status"] == "succeeded" and done["resources"] and "per_subscription" not in done and "unknown_types" not in done
    assert "Production" not in json.dumps(done)


def test_twenty_five_hundred_resources_in_a_few_calls(database, monkeypatch, tmp_path):
    monkeypatch.setattr(collect, "_sleep", time.sleep)  # this one checks the pacing for real
    install(monkeypatch, tmp_path)
    config.save({"tenant_id": TENANT_ID, "client_id": CLIENT_ID})
    credential.set_secret("fake-client-secret", "")
    with FakeAzure(generated_tenant(2500, 5)) as fake:
        monkeypatch.setattr(client, "TEST_ENDPOINTS", fake.cloud)
        client.reset()
        started = time.monotonic()
        out = pull()
        assert time.monotonic() - started < 60
        assert out["status"] == "succeeded" and out["counts"]["resources"] == 2500
        calls = fake.graph_calls
        assert all(sum(1 for t in calls if t0 <= t < t0 + 5) <= 15 for t0 in calls)
        assert store.search({}, 1)[1] == 2500
        # only the three documented routes were used
        assert {(e["method"], e["path"]) for e in fake.received} <= {
            ("POST", f"/{TENANT_ID}/oauth2/v2.0/token"), ("GET", "/subscriptions"),
            ("POST", "/providers/Microsoft.ResourceGraph/resources")}


def test_every_graph_call_sends_a_constant_from_queries_and_nothing_here_reaches_the_network_itself():
    import pathlib
    import re

    root = pathlib.Path(collect.__file__).parent.parent
    for path in [*root.glob("helpers/*.py"), *root.glob("api/*.py"), *root.glob("tools/*.py")]:
        text = path.read_text()
        if path.name != "client.py":
            for call in re.findall(r"client\.graph\(([^,)]*)", text):
                assert call.strip().startswith("queries.") or (path.name == "collect.py" and call == "query"), (path.name, call)
            for call in re.findall(r"\b_(?:read|signals)\(([^,)]*)", text.split("def _read", 1)[-1] if path.name == "collect.py" else ""):
                assert call.strip().startswith("queries."), (path.name, call)
        if path.name in ("collect.py", "schedule.py", "shape.py", "categories.py", "queries.py"):
            assert "requests" not in text, path.name
