"""A runbook plan: validated like each step alone, approved once, stopped at the first failure (ADR 0053)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from helpers import files, user_scope
from helpers.errors import RepairableException

from plugins._ea_ansible.api.change_approve import ChangeApprove
from plugins._ea_ansible.helpers import catalogue, change_store, executor_client, plans
from plugins._ea_ansible.helpers.change_model import (
    APPLIED, APPROVED, AWAITING_APPROVAL, FAILED, ROLLED_BACK, SUPERSEDED, VERIFY_FAILED, ChangeRecord,
)
from plugins._ea_ansible.tools import net_plan

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

ADD = "changes/sonic_community_vlan_add"
ROW = "|    100 |"
DEVICES = {
    n: {"name": n, "mgmt_ip": f"10.0.0.{i}", "platform": "sonic_community", "status": "active"}
    for i, n in enumerate(["lab-a", "lab-b", "lab-c", "lab-d"], 1)
}
READ = {"devices": "lab-a", "capability": "reads/show", "commands": ["show vlan brief"]}
CHANGE = {"devices": "lab-a", "capability": ADD, "vlan_id": 100}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(net_plan.plugin_config, "is_configured", lambda agent=None: True)
    monkeypatch.setattr(
        net_plan, "_resolve",
        lambda names: ([DEVICES[n] for n in names], "") if all(n in DEVICES for n in names)
        else ([], f"`{names[0]}` is not in Inventory"),
    )
    state = SimpleNamespace(cards=[], jobs=[], show=ROW, fail=None, events=[])
    monkeypatch.setattr(
        net_plan.preflight, "run_preflight",
        lambda a, devices, e: {"ok": True, "checks": [{"label": "reachable", "ok": True, "detail": ""}], "devices": devices},
    )
    monkeypatch.setattr(net_plan.executor_client, "health", lambda agent=None: {"catalogue_commit": "c" * 40})
    monkeypatch.setattr(net_plan.NetPlan, "_show", lambda self, rec, title, cons: state.cards.append(plans.card(rec, title, cons)))

    def run(agent, **kw):
        state.jobs.append(kw)
        if state.fail and state.fail(kw):
            return executor_client.JobResult("j1", "failed", 2, (), "Permission denied")
        out = state.show if kw["playbook_name"] == "reads/show" else "changed"
        return executor_client.JobResult("j1", "succeeded", 0, (), out)

    monkeypatch.setattr(net_plan.executor_client, "run", run)
    audit = SimpleNamespace(emit=lambda name, **kw: state.events.append(name))
    monkeypatch.setattr(change_store, "audit", audit)
    monkeypatch.setattr(net_plan, "audit", audit)
    with user_scope.user_scope("asha"):
        yield state


def tool(**args):
    t = net_plan.NetPlan(agent=object(), name="net_plan", method=None, args=args, message="", loop_data=None)
    return asyncio.run(t.execute()).message


def request(*steps):
    message = tool(action="request", steps=list(steps))
    return message, [r for r in change_store.list_records() if r.kind == "plan"]


def approve(record, **body):
    return asyncio.run(ChangeApprove(None, None).process(
        {"decision": "approve", "change_id": record.id, "expected_hash": record.current_hash, **body}, None))


def approved(record):
    assert change_store.approve(record, "asha")
    return record


def run(record):
    return tool(action="run", change_id=record.id)


# --- request -----------------------------------------------------------------

def test_a_plan_is_one_record_one_card_and_one_hash(world):
    _, (record,) = request(READ, CHANGE, READ)
    assert [s["kind"] for s in record.steps] == ["read", "change", "read"]
    assert (record.tier, record.confirm, record.status) == ("change-low", "", AWAITING_APPROVAL)
    (card,) = world.cards
    commands, checks, undo = json.loads(card["commands"]), json.loads(card["checks"]), json.loads(card["rollback"])
    assert "sudo config vlan add 100" in commands and "# step 3: reads/show on lab-a" in commands
    assert len(checks) == 3 and undo == ["# undo step 2", "sudo config vlan del 100"]
    before = record.current_hash
    record.steps[1]["variables"] = {"vlan_id": 200}  # any step is in the hash, not only the listing
    assert record.current_hash != before


def test_a_bad_step_means_no_plan_and_names_the_step(world):
    message, records = request(READ, {**CHANGE, "devices": "nope"})
    assert "Step 2" in message and "not in Inventory" in message and records == [] and world.cards == []
    assert "Step 1" in request({**READ, "commands": ["show run | include x"]})[0]
    assert "rollback" in request({"devices": "lab-a", "capability": "changes/sonic_community_vlan_del", "vlan_id": 5})[0]
    assert "1 to 8" in request(*[READ] * 9)[0] and "1 to 8" in request()[0]
    assert change_store.list_records() == []


def test_the_riskiest_step_sets_the_tier_and_the_typed_text(world):
    _, (record,) = request(READ, {**CHANGE, "devices": "lab-a,lab-b,lab-c,lab-d"})
    assert (record.tier, record.confirm) == ("change-high", "run 2 steps") and world.cards[0]["confirm"] == "run 2 steps"
    assert approve(record)["ok"] is False and approve(record, confirmation="run 3 steps")["ok"] is False
    assert approve(record, confirmation="run 2 steps")["ok"]
    assert plans.tier_of(["read", "change-low"]) == "change-low"
    assert plans.tier_of(["read", "read"]) == "read"


def test_a_plan_too_long_for_one_card_is_refused(world):
    message, records = request(*[{**READ, "commands": ["show vlan brief"] * 5}] * 8)
    assert "one card" in message and records == []


# --- run ---------------------------------------------------------------------

def test_run_executes_in_order_and_verifies_the_change(world):
    _, (record,) = request(READ, CHANGE, READ)
    assert "not approved" in run(record) and world.jobs == []
    approved(record)
    run(record)
    assert [(j["playbook_name"], j["check_mode"]) for j in world.jobs] == [
        ("reads/show", True), (ADD, False), ("reads/show", True), ("reads/show", True)]  # read, change, its verify, read
    assert change_store.load(record.id).status == APPLIED and "network.plan.run" in world.events


def test_a_failed_step_stops_the_plan_and_offers_one_rollback_in_reverse_order(world):
    second = {"devices": "lab-b", "capability": ADD, "vlan_id": 200}
    _, (record,) = request(CHANGE, second, READ)
    approved(record)
    world.fail = lambda kw: kw["playbook_name"] == ADD and kw["limit"] == "lab-b"
    message = run(record)
    done = change_store.load(record.id)
    assert done.status == FAILED and "at step 2 of 3" in message
    assert [j["limit"] for j in world.jobs if j["playbook_name"] == ADD] == ["lab-a", "lab-b"]
    assert len(world.jobs) == 3  # change, its verify, the failed change; the read never ran
    undo = change_store.load(done.rollback_id)
    assert (undo.kind, undo.rollback_of, undo.status) == ("plan", record.id, AWAITING_APPROVAL)
    assert [s["limit"] for s in undo.steps] == ["lab-b", "lab-a"]  # reverse order, the failed step included
    assert undo.steps[0]["commands"] == ["sudo config vlan del 200"]
    assert "Roll back" in world.cards[-1]["options"] and "rollback" not in world.cards[-1]


def test_a_failed_verify_stops_the_plan(world):
    world.show = "| 7 |"
    _, (record,) = request(CHANGE, READ)
    approved(record)
    run(record)
    assert change_store.load(record.id).status == VERIFY_FAILED
    assert [j["playbook_name"] for j in world.jobs] == [ADD, "reads/show"]  # the trailing read never ran
    assert change_store.load(change_store.load(record.id).rollback_id).steps[0]["capability"] == "changes/sonic_community_vlan_del"


def test_a_rollback_plan_runs_through_the_same_gate_and_marks_the_original_rolled_back(world):
    _, (record,) = request(CHANGE)
    approved(record)
    world.show = "| 7 |"
    run(record)
    undo = change_store.load(change_store.load(record.id).rollback_id)
    world.show = "no vlans"
    assert approve(undo)["ok"]
    run(undo)
    assert change_store.load(undo.id).status == APPLIED and change_store.load(record.id).status == ROLLED_BACK


def test_a_plan_that_would_now_send_other_commands_is_superseded(world):
    _, (record,) = request(CHANGE)
    record.steps[0]["commands"] = ["sudo config vlan add 999"]  # what the devices would no longer render
    approved(record)
    assert "superseded" in run(record) and change_store.load(record.id).status == SUPERSEDED and world.jobs == []


def test_the_change_gate_covers_plan_run_only(world):
    from plugins._ea_ansible.extensions.python.tool_execute_before._95_change_gate import NetworkChangeGate

    _, (record,) = request(CHANGE)
    gate = NetworkChangeGate(agent=SimpleNamespace(number=0))
    call = lambda **a: asyncio.run(gate.execute(tool_name="net_plan", tool_args=a))  # noqa: E731
    call(action="request", steps=[CHANGE])  # asking is not gated
    with pytest.raises(RepairableException, match="not 'approved'"):
        call(action="run", change_id=record.id)
    approved(record)
    call(action="run", change_id=record.id)
