import datetime as dt

import pytest

from plugins._ea_inventory.helpers import proposals, store
from plugins._ea_inventory.helpers.adapters import solarwinds
from plugins._ea_inventory.helpers.normalise import InventoryError
from plugins._ea_solarwinds.helpers import bulk
from plugins._ea_solarwinds.helpers import config as sw_config


def node(i, **over):
    base = {
        "node_id": str(i), "caption": f"sw{i}", "dns": "", "sys_name": "", "ip_address": f"10.0.0.{i}",
        "vendor": "Cisco", "machine_type": "C9300", "software_version": "17.3.4 IOS XE",
        "sys_object_id": "1.3.6.1.4.1.9.1", "location": "", "status": "up", "serial": f"FOC{i}", "last_poll": None,
    }
    return {**base, **over}


class Fake:
    def __init__(self):
        self.nodes, self.error, self.truncated, self.missing, self.complete = [node(1), node(2)], None, False, [], True
        self.calls = []

    def __call__(self, max_nodes=2000, page_size=200):
        self.calls.append((max_nodes, page_size))
        if self.error:
            raise self.error
        return bulk.NodesResult(self.nodes, self.complete and not self.truncated, self.truncated, list(self.missing))


@pytest.fixture
def fake(database, events, monkeypatch):
    f = Fake()
    monkeypatch.setattr(bulk, "fetch_nodes", f)
    monkeypatch.setattr(sw_config, "is_configured", lambda: True)
    monkeypatch.setattr(store, "solarwinds_settings", lambda: {
        "enabled": True, "max_nodes": 2000, "stale_days": 7, "min_platform_confidence": 0.6})
    f.events = events
    return f


def run(dry_run=False):
    return solarwinds.run_solarwinds_sync(dry_run, "alice")


def approve_all():
    for p in proposals.list_proposals(status="pending")[0]:
        proposals.decide(p["id"], "approve", actor="alice")


def test_refuses_when_disabled_or_unconfigured_and_leaves_no_run(fake, monkeypatch):
    monkeypatch.setattr(store, "solarwinds_settings", lambda: {"enabled": False, "max_nodes": 1, "stale_days": 7, "min_platform_confidence": 0.6})
    with pytest.raises(InventoryError, match="disabled"):
        run()
    monkeypatch.setattr(store, "solarwinds_settings", lambda: {"enabled": True, "max_nodes": 1, "stale_days": 7, "min_platform_confidence": 0.6})
    monkeypatch.setattr(sw_config, "is_configured", lambda: False)
    with pytest.raises(InventoryError, match="not configured"):
        run()
    assert store.last_run("solarwinds") is None and fake.calls == []


@pytest.mark.parametrize("failure", ["raise", "incomplete"])
def test_a_failed_fetch_creates_nothing(fake, failure):
    if failure == "raise":
        fake.error = bulk.BulkFetchError("page 2: HTTP 500")
    else:
        fake.complete = False
    out = run()
    assert out["status"] == "failed" and out["error"] and out["batch_id"] is None
    assert proposals.list_proposals()[1] == 0 and store.latest_observations("solarwinds") == []
    last = store.last_run("solarwinds")
    assert last["status"] == "failed" and last["error"] == out["error"] and last["finished_at"]


def test_dry_run_stores_nothing(fake):
    out = run(dry_run=True)
    assert out["status"] == "dry_run", out.get("error") and out["batch_id"] is None
    assert out["counts"] == {"fetched": 2, "create": 2, "update": 0, "unchanged": 0, "skipped": 0, "needs_review": 0, "stale": 0, "conflicts": 0}
    assert proposals.list_proposals()[1] == 0 and store.latest_observations("solarwinds") == []
    assert store.last_run("solarwinds")["status"] == "dry_run"


def test_success_stages_one_batch_with_evidence_and_the_audit_has_counts_only(fake):
    out = run()
    assert out["status"] == "succeeded" and out["counts"]["create"] == 2 and out["batch_id"]
    found = proposals.list_proposals(batch_id=out["batch_id"])[0]
    assert len(found) == 2 and {p["source"] for p in found} == {"solarwinds"}
    ev = next(p for p in found if p["device_name"] == "sw1")["evidence"]
    assert ev["source"] == "solarwinds" and ev["node_id"] == "1" and ev["batch_id"] == out["batch_id"]
    assert 0 < ev["confidence"] <= 1 and ev["observed_at"]
    assert store.search_devices()[1] == 0  # nothing committed
    assert len(store.latest_observations("solarwinds")) == 2
    [event] = [f for e, f in fake.events if e == "inventory.synced"]
    assert event["create"] == 2 and event["fetched"] == 2 and "needs_review" in event
    assert store.last_run("solarwinds")["batch_id"] == out["batch_id"]


def test_second_run_while_pending_does_not_duplicate_requests(fake):
    run()
    out = run()
    assert out["counts"]["create"] == 0 and out["counts"]["skipped"] == 2 and out["batch_id"] is None
    assert proposals.list_proposals()[1] == 2


def test_identical_run_after_approval_proposes_nothing_and_updates_last_seen(fake):
    run()
    approve_all()
    assert all(d["last_seen_at"] is None for d in store.list_devices())
    out = run()
    assert out["counts"]["create"] == out["counts"]["update"] == 0 and out["counts"]["unchanged"] == 2
    assert out["batch_id"] is None and proposals.list_proposals()[1] == 2
    devices = store.list_devices()
    assert all(d["last_seen_at"] for d in devices)
    assert all(d["sources"][0]["source"] == "solarwinds" and not d["sources"][0]["stale"] for d in devices)
    assert {d["sources"][0]["source_id"] for d in devices} == {"1", "2"}


def test_a_changed_node_stages_an_update_and_a_missing_one_is_only_stale(fake, monkeypatch):
    run()
    approve_all()
    fake.nodes = [node(1, software_version="17.9.1 IOS XE")]  # node 2 vanished
    # age node 2's observation
    with store.transaction() as conn:
        conn.execute("UPDATE inventory_observation SET observed_at = now() - interval '30 days' WHERE source_id = '2'")
    out = run()
    assert out["counts"]["update"] == 1 and out["counts"]["stale"] == 1 and out["counts"]["create"] == 0
    [p] = proposals.list_proposals(status="pending")[0]
    assert p["kind"] == "update" and p["changes"]["software_version"]["new"] == "17.9.1 IOS XE"
    assert not [x for x in proposals.list_proposals()[0] if x["kind"] == "decommission"]
    assert store.search_devices(name="sw2")[0][0]["status"] != "decommissioned"
    stale = {d["name"]: d["sources"][0]["stale"] for d in store.list_devices()}
    assert stale == {"sw1": False, "sw2": True}
    assert store.source_stats("solarwinds", 7) == {"nodes_seen": 2, "stale_count": 1}


def test_needs_review_unnamable_and_missing_columns_are_reported(fake):
    p = proposals.propose("create", None, {"name": "sw1", "serial": "ZZZ", "mgmt_ip": "10.7.7.7"}, actor="alice")
    proposals.decide(p["id"], "approve", actor="alice")
    fake.nodes = [node(1), node(3, caption="", ip_address=""), node(4)]
    fake.missing = ["Orion.Nodes.SysObjectID"]
    out = run()
    assert out["missing_columns"] == ["Orion.Nodes.SysObjectID"]
    assert [r["node_id"] for r in out["needs_review"]] == ["1"] and out["needs_review"][0]["device_name"] == "sw1" and out["needs_review"][0]["observed_name"] == "sw1"
    assert out["counts"] == {"fetched": 3, "create": 1, "update": 0, "unchanged": 0, "skipped": 1, "needs_review": 1, "stale": 0, "conflicts": 0}
    assert out["skipped"][0]["node_id"] == "3"


def test_truncated_fetch_reports_no_stale_and_passes_the_cap(fake):
    fake.truncated = True
    out = run()
    assert out["truncated"] is True and fake.calls == [(2000, 200)] and out["counts"]["stale"] == 0


def test_the_agent_tools_never_reach_the_feed():
    from pathlib import Path

    for path in (Path(solarwinds.__file__).parents[2] / "tools").glob("*.py"):
        assert "solarwinds" not in path.read_text().lower().replace("_ea_solarwinds", "")


def test_end_to_end_against_the_fake_swis_server(database, events, monkeypatch):
    """Real bulk fetch over HTTP into a batch of requests; failures create nothing."""
    import requests

    from plugins._ea_solarwinds.helpers import client, policy
    from plugins._ea_solarwinds.tests.fake_swis import FakeSwis

    real_post = requests.post
    monkeypatch.setattr(requests, "post", lambda url, **kw: real_post(url.replace("https://", "http://", 1), **kw))
    monkeypatch.setattr(policy, "check_and_record", lambda key: None)
    monkeypatch.setattr(sw_config, "is_configured", lambda: True)
    monkeypatch.setattr(store, "solarwinds_settings", lambda: {
        "enabled": True, "max_nodes": 2000, "stale_days": 7, "min_platform_confidence": 0.6})
    with FakeSwis() as server:
        monkeypatch.setattr(client, "resolve_connection", lambda: client.Connection("127.0.0.1", server.port, "svc", "secret", True, 0.5))
        out = run()
        assert out["status"] == "succeeded", out.get("error")
        assert out["counts"]["fetched"] >= 12 and out["counts"]["create"] > 0 and out["batch_id"]
        assert proposals.list_proposals(batch_id=out["batch_id"])[1] == out["counts"]["create"]
        assert store.search_devices()[1] == 0
