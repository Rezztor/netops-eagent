import json

import pytest

from plugins._ea_ansible.helpers import executor_client
from plugins._ea_inventory.helpers import conflicts, proposals, store
from plugins._ea_inventory.helpers.facts import collect
from plugins._ea_inventory.helpers.normalise import InventoryError

FACTS = {
    "ansible_net_hostname": "sw1", "ansible_net_model": "C9300-48P",
    "ansible_net_serialnum": "FOC1234ABCD", "ansible_net_version": "17.3.4",
}


def line(host, facts, platform="ios", escaped=False):
    text = f"EAGENT_FACTS {host} " + json.dumps({"platform": platform, "facts": facts}, separators=(",", ":"))
    return json.dumps({"msg": text})[1:-1].join(['"msg": "', '"']) if escaped else text


class Executor:
    def __init__(self):
        self.facts, self.calls, self.fail = {"sw1": FACTS}, [], {}

    def health(self, agent=None):
        return {"catalogue_commit": "abc"}

    def run(self, agent, playbook, commit, variables, limit, check_mode, wait_seconds=None):
        self.calls.append((playbook, commit, variables, limit, check_mode))
        status = self.fail.get(limit, "succeeded")
        output = line(limit, self.facts[limit], escaped=True) if limit in self.facts else "ok: [x]"
        return executor_client.JobResult(f"job-{limit}", status, 0 if status == "succeeded" else 2, (), output)


@pytest.fixture
def ex(database, events, monkeypatch):
    e = Executor()
    monkeypatch.setattr(executor_client, "health", e.health)
    monkeypatch.setattr(executor_client, "run", e.run)
    return e


def refresh(names=None, **kw):
    return collect.refresh_facts(names, actor="alice", **kw)


def test_parse_plain_and_escaped():
    for esc in (False, True):
        assert collect.parse_facts("noise\n" + line("sw1", FACTS, escaped=esc) + "\nmore")["sw1"]["facts"] == FACTS
    assert collect.parse_facts("EAGENT_FACTS sw1 {broken") == {}


def test_refresh_stages_one_batch_and_an_observation(ex, make):
    d = make("sw1", status="active", platform="ios")
    out = refresh(["SW1"])
    [r] = out.results
    assert r["status"] == "ok" and r["proposals"] == 1 and r["conflicts"] == 0
    assert {"vendor", "model", "serial", "software_version"} <= set(r["fields_seen"])
    assert ex.calls == [("reads/inventory_facts", "abc", {}, "sw1", True)]
    [p] = proposals.list_proposals(status="pending")[0]
    assert p["source"] == "facts" and p["evidence"]["batch_id"] == out.batch_id == p["evidence"]["batch_id"]
    assert p["evidence"]["job_id"] == "job-sw1" and p["changes"]["model"]["new"] == "C9300-48P"
    assert p["requested_by"] == "alice"
    [o] = store.device_observations(d["id"])
    assert o["source"] == "facts" and o["source_id"] == "sw1" and o["payload"]["job_id"] == "job-sw1"
    assert store.get_device(d["id"])["last_seen_at"]
    assert out.counts["proposals"] == 1 and out.counts["ok"] == 1


def test_dry_run_stores_nothing(ex, make):
    d = make("sw1", status="active", platform="ios")
    out = refresh(["sw1"], dry_run=True)
    assert out.results[0]["proposals"] == 1 and out.batch_id is None
    assert not proposals.list_proposals(status="pending")[0]
    assert store.device_observations(d["id"]) == [] and conflicts.list_conflicts()[1] == 0


def test_human_value_becomes_a_conflict_not_a_change(ex, make):
    d = make("sw1", status="active", platform="ios", vendor="Acme", model="C9300-48P")
    r = refresh(["sw1"]).results[0]
    assert r["conflicts"] == 1 and r["unchanged"] == 1
    [c] = conflicts.list_conflicts()[0]
    assert c["field"] == "vendor" and c["current_value"] == "Acme" and c["source"] == "facts"
    assert store.get_device(d["id"])["vendor"] == "Acme"
    refresh(["sw1"])  # the same conflict is refreshed, not duplicated
    assert conflicts.list_conflicts()[1] == 1


def test_pending_facts_request_is_not_duplicated(ex, make):
    make("sw1", status="active", platform="ios")
    refresh(["sw1"])
    r = refresh(["sw1"]).results[0]
    assert r["proposals"] == 0 and "already pending" in r["warnings"][-1]
    assert len(proposals.list_proposals(status="pending")[0]) == 1


def test_hostname_mismatch_is_a_warning_only(ex, make):
    make("core-a", status="active", platform="ios")
    ex.facts = {"core-a": {**FACTS, "ansible_net_hostname": "Other"}}
    r = refresh(["core-a"]).results[0]
    assert r["status"] == "ok" and any("'other'" in w for w in r["warnings"])
    assert "name" not in proposals.list_proposals(status="pending")[0][0]["changes"]


def test_per_device_failures_do_not_stop_the_others(ex, make):
    for n in ("sw1", "sw2", "sw3"):
        make(n, status="active", platform="ios")
    ex.facts = {"sw1": FACTS, "sw3": {**FACTS, "ansible_net_serialnum": "FOC9999ZZZZ"}}
    ex.fail = {"sw2": "failed"}
    out = refresh(["sw1", "sw2", "sw3"])
    assert [r["status"] for r in out.results] == ["ok", "error", "ok"]
    assert "failed" in out.results[1]["error"] and out.counts["error"] == 1 and out.counts["proposals"] == 2
    ex.fail = {}
    ex.facts.pop("sw3")
    assert "no facts line" in refresh(["sw3"]).results[0]["error"]


def test_skips_and_cap(ex, make):
    make("fw1", status="active", platform="other")
    make("gone", platform="ios", status="decommissioned")
    out = refresh(["fw1", "gone", "nope"])
    assert [r["status"] for r in out.results] == ["skipped"] * 3 and ex.calls == []
    assert "no facts collection" in out.results[0]["error"]
    for i in range(12):
        make(f"sw{i:02}", status="active", platform="sonic")
    ex.facts = {}
    assert len(refresh().results) == 10 and refresh().counts["capped"] == 2
    assert len(refresh([f"sw{i:02}" for i in range(12)]).results) == 12  # 10 run, 2 reported skipped


def test_empty_sonic_facts_are_recorded_as_seen(ex, make):
    d = make("sn1", status="active", platform="sonic")
    ex.facts = {"sn1": {}}
    r = refresh(["sn1"]).results[0]
    assert r["status"] == "ok" and r["fields_seen"] == [] and r["proposals"] == 0 and r["warnings"]
    assert store.get_device(d["id"])["last_seen_at"]


def test_unreachable_executor_creates_nothing(ex, make, monkeypatch):
    d = make("sw1", status="active", platform="ios")

    def down(agent=None):
        raise executor_client.ExecutorUnreachable("The Ansible executor did not answer")

    monkeypatch.setattr(executor_client, "health", down)
    with pytest.raises(InventoryError, match="did not answer"):
        refresh(["sw1"])
    assert store.device_observations(d["id"]) == [] and not proposals.list_proposals(status="pending")[0]
