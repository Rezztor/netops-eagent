"""An Azure change set (ADR 0073) from draft to applied or stopped, against the stand-in executor: one plan call for the whole set, one
record, one card, one document, one number; the apply in order that stops at the first step that does not apply and rolls nothing
back; `continues`; the executor's six-a-minute admission; and every way a set is refused. No test contacts Microsoft."""

from __future__ import annotations

import asyncio
import copy
import json
import time

import pytest

from helpers import user_scope
from helpers.errors import RepairableException

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.tests import register
from plugins._ea_ansible.helpers.change_model import APPLIED, APPLYING, APPROVED, AWAITING_APPROVAL, FAILED, SUPERSEDED, VERIFY_FAILED
from plugins._ea_azure_change.api.azure_changes import AzureChanges
from plugins._ea_azure_change.api.azure_edit import AzureEdit
from plugins._ea_azure_change.helpers import card, conditions, drift, executor_client, records, sets, tier
from plugins._ea_azure_change.tests.fake_executor import SUB, plan_answer, plan_set_answer, resource
from plugins._ea_azure_change.tests.test_azure_change_flow import decide, download, gate, run, world  # noqa: F401 - `world` is a fixture
from plugins._ea_azure_change.tools.azure_apply import AzureApply
from plugins._ea_azure_change.tools.azure_change import AzureChange

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

TAGS = {"env": "test", "owner": "netops", "costcentre": "1"}


def vnet(name, group):
    return {"template": "vnet", "resource_group": group, "variables": {"name": name, "region": "uaenorth", "address_space": [{"cidr": "10.0.0.0/16"}], "tags": TAGS}}


def peering(name, group, remote):
    return {"template": "peering", "resource_group": group, "variables": {"name": name, "vnet_name": f"vnet-{group}", "remote_vnet": remote}}


#: A hub and two spokes: six steps in two groups.
STEPS = [vnet("vnet-hub", "rg-hub"), {"template": "subnet", "resource_group": "rg-hub", "variables": {"name": "GatewaySubnet", "vnet_name": "vnet-hub"}},
         vnet("vnet-a", "rg-spoke"), peering("hub-to-a", "rg-hub", "vnet-a"), vnet("vnet-b", "rg-spoke"), peering("hub-to-b", "rg-hub", "vnet-b")]
ID_LIKE = ["/subscriptions/x/resourceGroups/rg-hub/providers/Microsoft.Network/virtualNetworks/vnet-%d"]


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(sets, "RATE_WAIT_SECONDS", 0)


def draft_set(world, steps=None, **over):
    given = {} if "continues" in over else {"subscription": SUB, "steps": copy.deepcopy(steps or STEPS), "purpose": "A hub and two spokes."}
    message = run(AzureChange, **{"action": "draft_set", **given, **over})
    return change_store.load(world.cards[-1][0]), message


def approved_set(world, steps=None):
    record, _ = draft_set(world, steps)
    assert getattr(download(record), "status_code", None) == 200
    assert decide(change_store.load(record.id), reference="cr-12345")["ok"]
    return change_store.load(record.id)


def jobs_for(world, record, by_step=None):
    """One job per step id: every step applied unless `by_step` ({n: job}) says otherwise."""
    by_step = by_step or {}
    for step in record.steps:
        made = [f"/subscriptions/x/resourceGroups/{step['resource_group']}/providers/Microsoft.Network/virtualNetworks/vnet-{step['n']}"]
        world.jobs[f"{record.id}.{step['n']:02d}"] = by_step.get(step["n"]) or {"status": "applied", "verified": True, "changed_ids": made,
                                                                                "started_at": 1759478300.0 + step["n"], "ended_at": 1759478310.0 + step["n"]}


def applied_calls(world):
    return [c[2] for c in world.calls if c[1] == "/v1/apply"]


# --- draft ------------------------------------------------------------------------------------------------------------

def test_a_set_is_planned_in_one_call_and_becomes_one_record_one_document_and_one_card(world):
    record, message = draft_set(world)
    assert (record.kind, record.playbook_name, record.status, record.variables, record.diff) == ("azure_change", "set", AWAITING_APPROVAL, {}, [])
    assert record.limit == f"{SUB}/rg-hub,rg-spoke" and len(record.steps) == 6 and [s["n"] for s in record.steps] == [1, 2, 3, 4, 5, 6]
    assert [(s["template"], s["resource_group"], s["status"]) for s in record.steps] == [(s["template"], s["resource_group"], "planned") for s in STEPS]
    assert sets.steps_match(record) and record.plan_digest == sets.set_digest(SUB, record.steps) and record.commands == sets.commands_of(record.steps)
    assert record.commands[0].startswith('1. vnet in rg-hub: address_space = ') and "2. subnet in rg-hub: " in record.commands[1]
    assert world.paths().count("/v1/plan-set") == 1 and "/v1/plan" not in world.paths()
    body = next(c for c in world.calls if c[1] == "/v1/plan-set")[2]
    assert [s["template"] for s in body["steps"]] == [s["template"] for s in STEPS] and set(body["steps"][0]) == {"template", "variables", "resource_group"}
    assert {(d["version"], d["format"]) for d in record.documents} == {("issued", "pdf"), ("issued", "docx")} and all(d["plan_digest"] == record.plan_digest for d in record.documents)
    assert record.preflight["set"] == {"steps": 6, "applied": 0, "stopped_at": 0} and record.preflight["continues"] == ""
    assert record.preflight["scope"]["resource_groups"] == [{"name": "rg-hub", "new": False}, {"name": "rg-spoke", "new": False}]
    assert record.preflight["summary"]["add"] == 6 and record.tier == "change-high" and any("vnet" in r or "template" in r for r in record.preflight["risk_reasons"])
    assert len(world.cards) == 1 and "Stop here" in message and "6 steps" in message and "[1]" in message and records.STEPS in message
    assert [e for e, _ in world.events].count("azure.change.drafted") == 1


def test_the_card_carries_the_steps_in_order_with_their_plan_lines_and_has_no_edit_button(world):
    record, _ = draft_set(world)
    args = card.change_card_args(record)
    assert [(s["n"], s["adds"]) for s in args["steps"]] == [(n, 1) for n in range(1, 7)] and args["steps"][0]["title"] == "vnet vnet-hub in rg-hub"
    assert args["steps"][0]["lines"] and args["edit_endpoint"] is None and args["reference"]["pattern"] and len(args["documents"]) == 2
    assert args["consequence"].startswith("6 steps. Adds 6, changes 0, removes 0.")
    rows = dict(args["details"])
    assert rows["Resource groups"] == "rg-hub, rg-spoke" and rows["Steps"].startswith("6") and record.plan_digest[:16] in rows["Change document"]
    assert "Why high risk" in rows and "steps" not in card.change_card_args(world_single(world))
    kvps = world.cards[-1][1]  # the card as the interaction plugin built it
    assert [s["n"] for s in json.loads(kvps["steps"])] == [1, 2, 3, 4, 5, 6] and "edit_endpoint" not in kvps and "steps" not in card.change_card(world_single(world))


def world_single(world):
    return records.make_record(change_id="s1", requester="asha", template="vnet", variables={"name": "v"}, subscription=SUB, resource_group="rg-test",
                               purpose="p", answer=plan_answer(), commit="c" * 40, advice=[])


def test_a_set_is_refused_with_no_record_and_no_card_for_each_way_it_can_be_wrong(world, monkeypatch):
    def refused(**over):
        message = run(AzureChange, **{"action": "draft_set", "subscription": SUB, "purpose": "p", "steps": copy.deepcopy(STEPS), **over})
        assert world.cards == [] and change_store.list_records() == [], message
        return message

    assert "single change" in refused(steps=STEPS[:1]) and "too big for one set" in refused(steps=STEPS * 3)
    assert "Free code is never a step" in refused(steps=[STEPS[0], {**STEPS[1], "code": 'resource "x" "y" {}'}])
    assert "Free code is never a step" in refused(steps=[STEPS[0], {"template": "vnet", "resource_group": "rg-hub"}])
    assert "Templates that exist" in refused(steps=[STEPS[0], {**STEPS[1], "template": "nuke_it"}])
    assert "Step 2" in refused(steps=[STEPS[0], {**STEPS[1], "resource_group": "bad group!"}]) and "GUID" in refused(subscription="sub")
    assert "list of 2 to 12 steps" in refused(steps="vnet")
    assert world.paths() == []  # nothing reached the executor
    monkeypatch.setattr(drift, "draft_problem", lambda sub, group: "rg-spoke has drift" if group == "rg-spoke" else None)
    assert "has drift" in refused()
    assert world.paths() == []


def test_the_executors_refusal_names_the_step_and_a_removal_in_any_step_or_a_sum_over_the_ceiling_is_refused_by_the_application_too(world):
    def refused(answer):
        world.plan_set = answer
        message = run(AzureChange, action="draft_set", subscription=SUB, purpose="p", steps=copy.deepcopy(STEPS))
        assert world.cards == [] and change_store.list_records() == []
        return message

    assert "at step 3 at the 'policy' stage: nope" in refused({"ok": False, "step": 3, "stage": "policy", "refusal": "nope"})
    assert "Nothing was drafted" in refused({"ok": False, "step": 0, "stage": "gate", "refusal": "too small"})
    good = plan_set_answer(SUB, STEPS)
    for action in ("delete", "replace", "forget", "delete,create", "stop"):
        bad = copy.deepcopy(good)
        bad["steps"][4]["summary"]["resources"][0]["action"] = action
        assert "Step 5" in refused(bad) and "never performs" in refused(bad)
    assert "ceiling" in refused(plan_set_answer(SUB, STEPS, monthly=900.0))
    each_under = copy.deepcopy(plan_set_answer(SUB, STEPS, monthly=240.0))
    each_under["cost"]["monthly_usd"] = 100.0  # the executor's sum is wrong; the steps' own sum is over 250 and the application adds them up
    each_under["steps"][0]["cost"]["monthly_usd"] = 250.0
    assert "ceiling" in refused(each_under)
    wrong = copy.deepcopy(good)
    wrong["set_digest"] = "0" * 64
    assert "set digest" in refused(wrong)
    short = copy.deepcopy(good)
    del short["steps"][5]
    assert "does not hold the steps" in refused(short)
    nothing = copy.deepcopy(good)
    nothing["steps"][2]["summary"].update(add=0, resources=[])
    assert "Step 3 would change nothing" in refused(nothing)
    failing = copy.deepcopy(good)
    failing["checks"][0] = {"label": "Cost of the set", "ok": False, "detail": "too much"}
    assert "too much" in refused(failing)
    many = plan_set_answer(SUB, STEPS)
    for g in many["steps"]:
        g["summary"]["add"] = 8  # 48 resources against 40
    assert "more than the limit" in refused(many)


def test_the_tier_of_a_set_is_high_for_a_template_not_marked_low_for_a_summed_cost_or_count_and_low_otherwise():
    rails = {"low_risk_usd_month": 20}
    plan = {"summary": {"add": 3}, "cost": {"monthly_usd": 5.0, "unknown": []}}
    assert tier.decide_set({"nsg": {"risk": "low"}, "vnet": {"risk": "low"}}, plan, rails) == ("change-low", [])
    level, reasons = tier.decide_set({"nsg": {"risk": "low"}, "vnet": {"risk": "high"}, "x": None}, plan, rails)
    assert level == "change-high" and len(reasons) == 2 and "`vnet`" in reasons[0] and "`x`" in reasons[1]
    assert tier.decide_set({"nsg": {"risk": "low"}}, {"summary": {"add": 9, "change": 2}, "cost": {"monthly_usd": 5.0}}, rails)[0] == "change-high"
    assert tier.decide_set({"nsg": {"risk": "low"}}, {"summary": {"add": 2}, "cost": {"monthly_usd": 21.0}}, rails)[0] == "change-high"


def test_a_redraft_with_replaces_retires_the_open_set_and_only_a_single_open_change_in_the_same_subscription(world):
    first, _ = draft_set(world)
    second, message = draft_set(world, replaces=first.id, steps=STEPS[:3])
    assert change_store.load(first.id).status == SUPERSEDED and second.status == AWAITING_APPROVAL and first.id in message and len(second.steps) == 3
    assert "cannot be replaced" in run(AzureChange, action="draft_set", subscription=SUB, purpose="p", steps=copy.deepcopy(STEPS), replaces=first.id)
    assert "no change" in run(AzureChange, action="draft_set", subscription=SUB, purpose="p", steps=copy.deepcopy(STEPS), replaces="nope").lower()
    assert change_store.load(second.id).status == AWAITING_APPROVAL


def test_a_set_is_not_drafted_through_draft_and_a_single_change_still_is(world):
    assert "draft_set" in run(AzureChange, action="draft", steps=copy.deepcopy(STEPS))
    assert "draft_set" in run(AzureChange, action="draft", continues="x")
    assert world.calls == [] and "Use draft, draft_set" in run(AzureChange, action="delete", change_id="x")


# --- approval, and the things a set may not do --------------------------------------------------------------------------

def test_a_set_needs_its_document_downloaded_and_a_number_before_approve_and_one_approval_covers_every_step(world):
    record, _ = draft_set(world)
    assert "never downloaded" in decide(record, reference="CR-12345")["error"]
    assert download(record).status_code == 200
    for bad in (None, "", "cr 12", "CR-12"):
        assert "CR or SR" in decide(change_store.load(record.id), reference=bad)["error"]
    assert "no longer what would run" in decide(change_store.load(record.id), reference="CR-12345", expected_hash="0" * 64)["error"]
    assert decide(change_store.load(record.id), reference="cr-12345")["ok"]
    done = change_store.load(record.id)
    assert (done.status, done.reference, done.approved_hash) == (APPROVED, "CR-12345", done.current_hash) and done.approved_hash


def test_a_set_has_no_edit_and_a_row_in_the_register_lists_its_steps(world):
    record, _ = draft_set(world)
    answer = asyncio.run(AzureEdit(None, None).process({"change_id": record.id, "variables": {"name": "x"}}, None))
    assert not answer["ok"] and "draft it again" in answer["error"] and change_store.load(record.id).status == AWAITING_APPROVAL
    row = asyncio.run(AzureChanges(None, None).process({"change_id": record.id}, None))["change"]
    assert row["steps"][0] == {"n": 1, "title": "vnet vnet-hub in rg-hub", "status": "planned"} and len(row["steps"]) == 6
    assert "steps" not in asyncio.run(AzureChanges(None, None).process({"change_id": "nope"}, None)).get("change", {})


def test_the_gate_lets_an_approved_or_applying_set_through_only_when_every_condition_holds_and_never_a_subordinate_agent(world):
    record = approved_set(world)
    gate(change_id=record.id)
    with pytest.raises(RepairableException, match="subordinate"):
        gate(number=1, change_id=record.id)
    record.status = APPLYING
    register.force(record)
    gate(change_id=record.id)  # a set goes on after a wait
    record.status = "failed"
    change_store.save(record)
    with pytest.raises(RepairableException, match="not 'approved'"):
        gate(change_id=record.id)


def test_a_set_whose_steps_were_tampered_with_fails_set_steps_match_before_anything_runs(world):
    record = approved_set(world)
    assert conditions.first_failure(record, "asha") is None
    names = [c.name for c in conditions.CONDITIONS]
    assert names.index("set_steps_match") == names.index("plan_unchanged") + 1

    def broken(change):
        copy_ = change_store.load(record.id)
        change(copy_)
        change_store.save(copy_)
        text = conditions.first_failure(copy_, "asha")
        change_store.save(record)
        return text

    assert "not the ones its digest was made from" in broken(lambda r: r.steps[0]["variables"].update(name="vnet-evil"))
    assert "not the ones" in broken(lambda r: r.steps.reverse())
    assert "not the ones" in broken(lambda r: r.steps.pop())
    assert "not the ones" in broken(lambda r: r.steps[2].update(plan_digest="f" * 64))
    assert "not the ones" in broken(lambda r: r.steps[1].update(resource_group="rg-other"))
    assert "not the ones" in broken(lambda r: setattr(r, "steps", []))
    assert not sets.steps_match(type(record)(kind="azure_change", playbook_name="set", steps=[{"n": 1}, {"template": "x"}]))
    assert conditions.first_failure(world_single(world), "asha") is not None  # the single change fails its own conditions, not set_steps_match
    assert "set_steps" not in str(conditions.first_failure(world_single(world), "asha"))


# --- apply ------------------------------------------------------------------------------------------------------------------

def test_apply_runs_every_step_in_order_under_its_own_id_and_digest_then_one_applied_set_and_one_second_document(world, monkeypatch):
    managed = []
    monkeypatch.setattr(records, "record_managed", lambda record, ids: managed.append((record.id, record.limit, ids)) or len(ids))
    record = approved_set(world)
    jobs_for(world, record)
    message = run(AzureApply, action="apply", change_id=record.id)
    done = change_store.load(record.id)
    assert done.status == APPLIED and done.verified is True and done.error == "" and done.applied_at and done.job_id == f"job-{record.id}-06"
    assert [s["status"] for s in done.steps] == ["applied"] * 6 and all(s["job_id"] and s["verified"] and s["changed_ids"] for s in done.steps)
    assert done.preflight["set"] == {"steps": 6, "applied": 6, "stopped_at": 0}
    bodies = applied_calls(world)
    assert [b["change_id"] for b in bodies] == [f"{record.id}.{n:02d}" for n in range(1, 7)]
    assert [b["plan_digest"] for b in bodies] == [s["plan_digest"] for s in record.steps] and {b["reference"] for b in bodies} == {"CR-12345"}
    assert [b["workspace"]["resource_group"] for b in bodies] == [s["resource_group"] for s in STEPS] and "mode" not in bodies[0]
    assert bodies[0]["source"] == {"template": "vnet", "variables": record.steps[0]["variables"]} and {b["actor"] for b in bodies} == {"asha"}
    assert [(m[0], m[1]) for m in managed] == [(record.id, f"{SUB}/{s['resource_group']}") for s in STEPS]
    assert {(d["version"], d["format"]) for d in done.documents} >= {("applied", "pdf"), ("applied", "docx")}
    events = [(e, kw) for e, kw in world.events]
    steps = [kw for e, kw in events if e == "azure.change.step"]
    assert [(k["step"], k["step_change_id"], k["status"]) for k in steps] == [(n, f"{record.id}.{n:02d}", "applied") for n in range(1, 7)]
    assert set(steps[0]) >= {"change_id", "template", "workspace", "plan_digest", "job_id", "reference", "requested_by", "approved_by"} and "variables" not in steps[0]
    assert [e for e, _ in events].count("azure.change.applied") == 1 and "applied: all 6 steps" in message and "Inventory pull: started" in message
    assert "/v1/plan" not in world.paths() and "/v1/plan-set" in world.paths()
    with pytest.raises(RepairableException, match="not 'approved'"):
        gate(change_id=record.id)
    assert "Only an approved change" in run(AzureApply, change_id=record.id) and len(applied_calls(world)) == 6  # a second apply of a finished set


@pytest.mark.parametrize("state,status,k_text", [
    ({"status": "failed", "error": "quota exceeded", "changed_ids": [], "started_at": 1.0, "ended_at": 2.0}, FAILED, "quota exceeded"),
    ({"status": "verify_failed", "verified": False, "changed_ids": ["/subscriptions/x/a"], "started_at": 1.0, "ended_at": 2.0}, VERIFY_FAILED, "verify did not find"),
    ({"status": "superseded", "changed_ids": [], "started_at": 1.0, "ended_at": 2.0}, FAILED, "the plan changed"),
])
def test_a_set_stops_at_step_three_of_six_and_later_steps_never_reach_the_executor_and_nothing_is_rolled_back(world, monkeypatch, state, status, k_text):
    managed = []
    monkeypatch.setattr(records, "record_managed", lambda record, ids: managed.append((record.limit, ids)) or len(ids))
    record = approved_set(world)
    jobs_for(world, record, {3: state})
    message = run(AzureApply, change_id=record.id)
    done = change_store.load(record.id)
    assert done.status == status and done.applied_at and done.preflight["set"] == {"steps": 6, "applied": 2, "stopped_at": 3}
    assert [s["status"] for s in done.steps] == ["applied", "applied", state["status"], "not_run", "not_run", "not_run"]
    assert done.error.startswith("Stopped at step 3 of 6: ") and k_text in done.error and "Steps 1 to 2 are applied and stay in Azure." in done.error
    assert len(applied_calls(world)) == 3 and not any(c[2] and str(c[2].get("change_id", "")).endswith((".04", ".05", ".06")) for c in world.calls)
    for text in (f"stopped at step 3 of 6", "1. vnet vnet-hub in rg-hub: applied and verified", "3. vnet vnet-a in rg-spoke: ", "4. peering hub-to-a in rg-hub: not run",
                 "Nothing is rolled back", "part 9", f"continues=`{record.id}`", "same number may be typed again"):
        assert text in message, text
    assert {(d["version"]) for d in done.documents} >= {"issued", "applied"}
    assert [m[0] for m in managed][:2] == [f"{SUB}/rg-hub", f"{SUB}/rg-hub"]
    statuses = [(kw["step"], kw["status"]) for e, kw in world.events if e == "azure.change.step"]
    assert statuses[:2] == [(1, "applied"), (2, "applied")] and statuses[2] == (3, state["status"]) and len(statuses) == 3
    with pytest.raises(RepairableException):
        gate(change_id=record.id)


def test_a_set_whose_first_step_is_superseded_ran_nothing_and_is_itself_superseded(world):
    record = approved_set(world)
    jobs_for(world, record, {1: {"status": "superseded", "changed_ids": [], "started_at": 1.0, "ended_at": 2.0}})
    message = run(AzureApply, change_id=record.id)
    done = change_store.load(record.id)
    assert done.status == SUPERSEDED and done.applied_at is None and done.error.endswith("Nothing was applied.") and "stopped at step 1 of 6" in message
    assert not any(d["version"] == "applied" for d in done.documents) and len(applied_calls(world)) == 1
    assert [s["status"] for s in done.steps] == ["superseded"] + ["not_run"] * 5


def test_a_step_the_executor_refuses_before_a_job_exists_stops_the_set_there(world):
    record = approved_set(world)
    jobs_for(world, record)
    world.replies["/v1/apply"] = [None, (409, {"error": "change already applied"})]
    message = run(AzureApply, change_id=record.id)
    done = change_store.load(record.id)
    assert done.status == FAILED and [s["status"] for s in done.steps] == ["applied", "failed"] + ["not_run"] * 4
    assert "already applied" in done.error and "Step 1 is applied and stays in Azure." in done.error and "stopped at step 2 of 6" in message
    assert len(applied_calls(world)) == 2


def test_a_wait_that_runs_out_leaves_the_step_applying_and_a_later_apply_settles_it_and_finishes_the_set(world, monkeypatch):
    record = approved_set(world)
    jobs_for(world, record)
    real_wait = executor_client.wait
    running = {"status": "running", "started_at": 1.0}
    monkeypatch.setattr(executor_client, "wait", lambda agent, job_id, seconds: running if job_id.endswith("-03") else real_wait(agent, job_id, seconds))
    world.jobs[f"{record.id}.03"], held = running, world.jobs[f"{record.id}.03"]
    assert "still applying step 3 of 6" in run(AzureApply, change_id=record.id)
    mid = change_store.load(record.id)
    assert mid.status == APPLYING and [s["status"] for s in mid.steps] == ["applied", "applied", "applying", "planned", "planned", "planned"] and len(applied_calls(world)) == 3
    gate(change_id=record.id)
    world.jobs[f"{record.id}.03"] = held
    monkeypatch.setattr(executor_client, "wait", real_wait)
    message = run(AzureApply, change_id=record.id)
    done = change_store.load(record.id)
    assert done.status == APPLIED and [s["status"] for s in done.steps] == ["applied"] * 6 and "applied: all 6 steps" in message
    assert len(applied_calls(world)) == 6  # step 3 was not started again


def test_status_settles_a_running_step_and_says_what_is_left_or_finishes_a_set_that_stopped(world, monkeypatch):
    record = approved_set(world)
    jobs_for(world, record)
    running = {"status": "running", "started_at": 1.0}
    held = world.jobs[f"{record.id}.02"]
    real_wait = executor_client.wait
    monkeypatch.setattr(executor_client, "wait", lambda agent, job_id, seconds: running if job_id.endswith("-02") else real_wait(agent, job_id, seconds))
    world.jobs[f"{record.id}.02"] = running
    assert "still applying step 2" in run(AzureApply, change_id=record.id)
    assert "applying" in run(AzureChange, action="status", change_id=record.id) and "2. subnet GatewaySubnet in rg-hub: applying" in run(AzureChange, action="status", change_id=record.id)
    world.jobs[f"{record.id}.02"] = held
    message = run(AzureChange, action="status", change_id=record.id)
    assert "Step 2 of 6 applied" in message and "call azure_apply to go on" in message
    assert change_store.load(record.id).status == APPLYING
    world.jobs[f"{record.id}.03"] = {"status": "failed", "error": "boom", "changed_ids": [], "started_at": 1.0, "ended_at": 2.0}
    monkeypatch.setattr(executor_client, "wait", real_wait)
    assert "stopped at step 3 of 6" in run(AzureApply, change_id=record.id)


def test_an_executor_that_answers_429_is_waited_for_with_the_wait_said_and_the_same_step_tried_again(world, monkeypatch):
    record = approved_set(world)
    jobs_for(world, record)
    world.busy = 1
    said = []
    message = sets.run(None, record, "asha", said.append)
    done = change_store.load(record.id)
    assert done.status == APPLIED and "applied: all 6 steps" in message
    assert len(said) == 1 and "Step 1 of 6" in said[0] and "six changes a minute" in said[0] and "try" in said[0] and "(1 of 5)" in said[0]
    bodies = applied_calls(world)
    assert len(bodies) == 7 and bodies[0] == bodies[1] and [b["change_id"] for b in bodies[1:]] == [f"{record.id}.{n:02d}" for n in range(1, 7)]
    assert [(kw["step"], kw["status"]) for e, kw in world.events if e == "azure.change.step"] == [(n, "applied") for n in range(1, 7)]


def test_a_429_in_the_middle_of_a_set_is_waited_out_for_that_step_only_and_only_a_real_refusal_stops_it(world):
    record = approved_set(world)
    jobs_for(world, record)
    busy = (429, {"error": "More than 6 requests in the last minute."})
    world.replies["/v1/apply"] = [None, None, busy, busy]
    said = []
    sets.run(None, record, "asha", said.append)
    assert change_store.load(record.id).status == APPLIED and len(said) == 2 and all("Step 3 of 6" in t for t in said)
    assert [t[-9:] for t in said] == ["(1 of 5)." , "(2 of 5)."]


def test_a_429_that_never_ends_leaves_the_step_planned_and_the_set_waiting_for_a_later_apply_never_failed(world):
    record = approved_set(world)
    jobs_for(world, record)
    world.busy = 100
    said = []
    message = sets.run(None, record, "asha", said.append)
    mid = change_store.load(record.id)
    assert mid.status == APPROVED and [s["status"] for s in mid.steps] == ["planned"] * 6 and len(said) == sets.RATE_TRIES - 1
    assert "Nothing of it ran" in message and "azure_apply again" in message and mid.applied_at is None and mid.error == ""
    assert len(applied_calls(world)) == sets.RATE_TRIES
    world.busy = 0
    gate(change_id=record.id)
    assert "applied: all 6 steps" in run(AzureApply, change_id=record.id) and change_store.load(record.id).status == APPLIED


def test_a_429_that_never_ends_after_step_two_leaves_the_set_applying_with_step_three_to_run(world):
    record = approved_set(world)
    jobs_for(world, record)
    world.replies["/v1/apply"] = [None, None] + [(429, {"error": "More than 6 requests in the last minute."})] * sets.RATE_TRIES
    assert "Nothing of it ran" in sets.run(None, record, "asha")
    mid = change_store.load(record.id)
    assert mid.status == APPLYING and [s["status"] for s in mid.steps] == ["applied", "applied"] + ["planned"] * 4 and mid.applied_at is None
    assert "applied: all 6 steps" in run(AzureApply, change_id=record.id) and len(applied_calls(world)) == 2 + sets.RATE_TRIES + 4


def test_the_wait_is_said_in_the_tools_progress(world, monkeypatch):
    record = approved_set(world)
    jobs_for(world, record)
    said = []

    async def set_progress(self, content):
        said.append(content)

    monkeypatch.setattr(AzureApply, "set_progress", set_progress)
    world.busy = 1
    run(AzureApply, change_id=record.id)
    assert len(said) == 1 and "Waiting" in said[0] and change_store.load(record.id).status == APPLIED


def test_two_calls_cannot_apply_the_same_set_at_once(world):
    record = approved_set(world)
    with records.in_flight(record.id):
        assert "already being applied" in sets.run(None, record, "asha")
        assert "already being applied" in run(AzureApply, change_id=record.id)
    assert applied_calls(world) == [] and not records.is_in_flight(record.id)


def test_a_condition_that_fails_before_step_one_changes_nothing_and_after_step_one_stops_the_set_at_that_step(world, monkeypatch):
    record = approved_set(world)
    jobs_for(world, record)
    expired = change_store.load(record.id)
    expired.expires_at = time.time() - 1
    register.force(expired)
    assert "expired" in run(AzureApply, change_id=record.id) and change_store.load(record.id).status == APPROVED and applied_calls(world) == []
    register.force(record)
    calls = []
    real = conditions.first_failure
    monkeypatch.setattr(conditions, "first_failure", lambda rec, actor: (calls.append(1), "The CR number cannot be accepted: now" if len(calls) > 3 else real(rec, actor))[1])
    run(AzureApply, change_id=record.id)  # the tool's own check is call 1; the steps' are 2, 3, 4 (refused)
    done = change_store.load(record.id)
    assert done.status == FAILED and [s["status"] for s in done.steps] == ["applied", "applied", "failed", "not_run", "not_run", "not_run"]
    assert "cannot be accepted" in done.error and len(applied_calls(world)) == 2


# --- continues and resume ---------------------------------------------------------------------------------------------------

def stopped_at_three(world):
    record = approved_set(world)
    jobs_for(world, record, {3: {"status": "failed", "error": "quota", "changed_ids": [], "started_at": 1.0, "ended_at": 2.0}})
    run(AzureApply, change_id=record.id)
    return change_store.load(record.id)


def test_continues_drafts_steps_three_to_six_from_the_stopped_record_with_a_new_digest_and_a_new_document(world):
    old = stopped_at_three(world)
    world.calls.clear()
    new, message = draft_set(world, continues=old.id)
    assert [(s["template"], s["resource_group"], s["variables"]) for s in new.steps] == [(s["template"], s["resource_group"], s["variables"]) for s in old.steps[2:]]
    assert [s["n"] for s in new.steps] == [1, 2, 3, 4] and new.plan_digest != old.plan_digest and new.id != old.id
    assert new.preflight["continues"] == old.id and new.status == AWAITING_APPROVAL and new.reference == "" and new.approved_by == ""
    assert new.preflight["purpose"].startswith(f"Continues {old.id[:8]}") and "Continues" in dict(card.change_card_args(new)["details"])
    assert world.paths().count("/v1/plan-set") == 1 and {(d["version"], d["format"]) for d in new.documents} == {("issued", "pdf"), ("issued", "docx")}
    assert change_store.load(old.id).status == FAILED and "drafted: 4 steps" in message
    assert "need" in decide(new, reference="CR-12345")["error"] or "never downloaded" in decide(new, reference="CR-12345")["error"]


def test_continues_refuses_what_did_not_stop_steps_given_and_another_subscription_and_a_single_step_left(world):
    old = stopped_at_three(world)
    fresh, _ = draft_set(world)
    assert "do not give `steps`" in run(AzureChange, action="draft_set", continues=old.id, steps=copy.deepcopy(STEPS))
    assert "stopped part-way" in run(AzureChange, action="draft_set", continues=fresh.id)
    assert "subscription of the set" in run(AzureChange, action="draft_set", continues=old.id, subscription="99999999-2222-3333-4444-555555555555")
    old.preflight["set"]["stopped_at"] = 6
    change_store.save(old)
    assert "single change" in run(AzureChange, action="draft_set", continues=old.id)
    with user_scope.user_scope("bilal"):
        assert "no change" in run(AzureChange, action="draft_set", continues=old.id).lower()


def test_continues_drops_a_step_that_now_changes_nothing_and_names_it(world):
    old = stopped_at_three(world)
    world.calls.clear()
    world.replies["/v1/plan-set"] = [(200, {"ok": False, "step": 1, "stage": "policy", "refusal": "This step changes nothing in Azure."})]
    new, message = draft_set(world, continues=old.id)
    asked = [len(c[2]["steps"]) for c in world.calls if c[1] == "/v1/plan-set"]
    assert asked == [4, 3] and [s["n"] for s in new.steps] == [1, 2, 3] and new.steps[0]["template"] == "peering"
    assert "Dropped, because Azure already matches: vnet vnet-a in rg-spoke" in message
    nothing = (200, {"ok": False, "step": 1, "stage": "policy", "refusal": "This step changes nothing in Azure."})
    world.replies["/v1/plan-set"] = [nothing] * 5  # every step now matches Azure: one is left and it is a single change
    assert "Only one step is left" in run(AzureChange, action="draft_set", continues=old.id)


def test_resume_plans_the_set_again_and_shows_the_same_card_or_supersedes_it_when_the_plan_moved(world):
    record, _ = draft_set(world)
    message = run(AzureChange, action="resume", change_id=record.id)
    assert "same digest" in message and len(change_store.list_records()) == 1 and world.cards[-1][0] == record.id
    moved = copy.deepcopy(STEPS)
    moved[2]["variables"]["name"] = "vnet-other"
    world.plan_set = plan_set_answer(SUB, moved, digests=["d" * 63 + str(n) for n in range(6)])
    message = run(AzureChange, action="resume", change_id=record.id)
    old, new = change_store.load(record.id), change_store.load(world.cards[-1][0])
    assert old.status == SUPERSEDED and new.status == AWAITING_APPROVAL and new.id != old.id and new.plan_digest != old.plan_digest
    assert "superseded" in message and "document is void" in message and new.documents and "void" in download(old)["error"]
    assert len(new.steps) == 6 and sets.steps_match(new)


def test_status_lists_the_steps_and_cancel_closes_an_open_set(world):
    record, _ = draft_set(world)
    status = run(AzureChange, action="status", change_id=record.id)
    assert "1. vnet vnet-hub in rg-hub: planned" in status and "6. peering hub-to-b in rg-hub: planned" in status
    assert "cancelled" in run(AzureChange, action="cancel", change_id=record.id) and change_store.load(record.id).status == "cancelled"


def test_the_set_digest_the_application_checks_changes_when_two_steps_swap_a_value_changes_or_a_step_is_dropped(world):
    record, _ = draft_set(world)
    steps = copy.deepcopy(record.steps)
    base = sets.set_digest(SUB, steps)
    assert base == record.plan_digest
    swapped = [steps[1], steps[0], *steps[2:]]
    changed = copy.deepcopy(steps)
    changed[3]["plan_digest"] = "e" * 64
    assert len({base, sets.set_digest(SUB, swapped), sets.set_digest(SUB, changed), sets.set_digest(SUB, steps[:-1])}) == 4


def test_nothing_in_a_set_can_ask_the_executor_for_anything_but_a_plan_set_and_an_apply_per_step(world):
    record = approved_set(world)
    jobs_for(world, record)
    run(AzureApply, change_id=record.id)
    assert {p for p in world.paths() if not p.startswith("/v1/jobs/")} == {"/healthz", "/v1/plan-set", "/v1/apply"}
    assert all("destroy" not in str(c[2]).lower() and "delete" not in str(c[2]).lower() for c in world.calls)
    ones = [c for c in world.calls if c[1] == "/v1/apply"]
    assert all(set(c[2]) == {"source", "workspace", "plan_digest", "reference", "change_id", "actor"} for c in ones)
