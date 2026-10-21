"""A simple action (`azure_action`) from draft to applied, against the stand-in executor, and the suggestion that takes the
place of every removal or stop. The action is the same record, card, document, number and gate as `azure_change`; what is
different is the one call. No test contacts Microsoft or needs a credential."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from helpers import files, plugins, user_scope
from helpers.errors import RepairableException

from cloud_executor import actions as executor_actions
from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import APPLIED, APPROVED, AWAITING_APPROVAL, CANCELLED, FAILED, SUPERSEDED
from plugins._ea_azure_change.api.azure_approve import AzureApprove
from plugins._ea_azure_change.api.azure_document import AzureDocument
from plugins._ea_azure_change.api.azure_edit import AzureEdit
from plugins._ea_azure_change.extensions.python.tool_execute_before._95_azure_change_gate import AzureChangeGate
from plugins._ea_azure_change.helpers import card, records
from plugins._ea_azure_change.tests.fake_executor import DIGEST_A, DIGEST_B, SUB, FakeExecutor
from plugins._ea_azure_change.tools import azure_action
from plugins._ea_azure_change.tools.azure_action import AzureAction
from plugins._ea_azure_change.tools.azure_apply import AzureApply
from plugins._ea_azure_change.tools.azure_change import AzureChange

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

CONFIG = {"executor_url": "unix:///run/cloud-socket/cloud.sock", "timeout_seconds": 5, "apply_wait_seconds": 5, "guardrails": {}}
RID = f"/subscriptions/{SUB}/resourcegroups/rg-app/providers/microsoft.compute/virtualmachines/vm-web-01"
VM = {"native_id": RID, "name": "vm-web-01", "removed_at": None}
ACTIONS_COMMIT = "d" * 40


def preview_answer(digest=DIGEST_A, name="vm_start", **over):
    answer = {
        "ok": True, "action": name, "line": f"POST https://management.azure.com{RID}/start?api-version=2025-04-01", "plan_digest": digest,
        "current": {"state": "PowerState/deallocated"}, "checks": [{"label": "Reviewed action", "ok": True, "detail": name}],
        "summary": {"add": 0, "change": 1, "import": 0, "lines": ["~ vm-web-01  start: PowerState/deallocated -> PowerState/running"],
                    "resources": [{"address": RID, "type": "microsoft.compute/virtualmachines", "action": "start", "name": "vm-web-01",
                                   "before": {"state": "PowerState/deallocated"}, "after": {"state": "PowerState/running"}}]},
        "cost": {"monthly_usd": None, "items": [], "unknown": [], "priced_at": "", "note": "resumes billing for what already exists"},
        "risk": "low", "backout": "Deallocate it yourself in the portal, after your own approval.",
        "requires": "Microsoft.Compute/virtualMachines/start/action", "scope": {"subscription": SUB, "resource_group": "rg-app", "region": ""},
    }
    return {**answer, **over}


class ActionExecutor(FakeExecutor):
    """The plan stand-in plus `/v1/action` and an action-list commit on `/healthz`."""

    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.preview = preview_answer()
        self.actions_commit = ACTIONS_COMMIT

    def _send(self, agent, method, path, body=None, *, key="", timeout=None):
        if path == "/healthz":
            self.calls.append((method, path, body, key))
            return 200, {"ok": True, "templates_commit": "c" * 40, "actions_commit": self.actions_commit}
        if path == "/v1/action":
            self.calls.append((method, path, body, key))
            if body["mode"] == "preview":
                return 200, self.preview
            return (202, {"job_id": "job1"}) if self.apply_status == 202 else (self.apply_status, {"error": self.apply_error})
        return super()._send(agent, method, path, body, key=key, timeout=timeout)

    def action_calls(self, mode):
        return [b for _m, p, b, _k in self.calls if p == "/v1/action" and b["mode"] == mode]


@pytest.fixture
def world(tmp_path, monkeypatch):
    from plugins._ea_azure.helpers import store

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(plugins, "get_plugin_config", lambda *a, **k: dict(CONFIG))
    monkeypatch.setattr(plugins, "get_toggle_state", lambda name: "enabled")
    monkeypatch.setattr(records, "refresh_inventory", lambda actor: ("started", 1759478500.0))
    managed = []
    monkeypatch.setattr(records, "record_managed", lambda record, ids: managed.append(ids) or len(ids))
    monkeypatch.setattr(store, "get", lambda ref: VM if ref in ("vm-web-01", RID) else None)
    events, shown = [], []
    monkeypatch.setattr(change_store, "audit", SimpleNamespace(emit=lambda event, **kw: events.append((event, kw))))
    monkeypatch.setattr(card, "show", lambda agent, record, kvps: shown.append((record.id, kvps)))
    fake = ActionExecutor(monkeypatch)
    fake.events, fake.cards, fake.managed = events, shown, managed
    with user_scope.user_scope("asha"):
        yield fake


def run(cls, number=0, **args) -> str:
    agent = SimpleNamespace(context=SimpleNamespace(id="chat1"), number=number)
    return asyncio.run(cls(agent=agent, name="t", method=None, args=args, message="", loop_data=None).execute()).message


START = dict(action="draft", name="vm_start", resource="vm-web-01", purpose="Bring the web VM back for the month-end run.")


def draft(world, **over):
    message = run(AzureAction, **{**START, **over})
    return change_store.load(world.cards[-1][0]), message


def decide(record, decision="approve", **body):
    return asyncio.run(AzureApprove(None, None).process({"change_id": record.id, "decision": decision, "expected_hash": record.current_hash, **body}, None))


def download(record, fmt="pdf"):
    return asyncio.run(AzureDocument(None, None).process({"change_id": record.id, "version": "issued", "format": fmt}, None))


def approved(world, reference="cr-12345", **over):
    record, _ = draft(world, **over)
    assert getattr(download(record), "status_code", None) == 200
    assert decide(change_store.load(record.id), reference=reference)["ok"]
    return change_store.load(record.id)


def gate(**tool_args):
    asyncio.run(AzureChangeGate(agent=SimpleNamespace(number=0)).execute(tool_name="azure_action", tool_args=tool_args))


# --- the list is the executor's ---------------------------------------------------------------------------------------

def test_the_tool_and_the_executor_agree_on_the_seven_actions():
    assert tuple(azure_action.catalogue()) == executor_actions.ALLOWED
    assert all(azure_action._kind(n) == executor_actions.kind_of(n) for n in executor_actions.ALLOWED)
    assert not {"delete", "stop", "restart", "deallocate", "scale_in"} & {w for n in executor_actions.ALLOWED for w in n.split("_")}


# --- draft ---------------------------------------------------------------------------------------------------------------

def test_a_draft_asks_the_executor_for_a_preview_and_makes_the_record_the_document_and_the_card(world):
    record, message = draft(world)
    assert (record.kind, record.status, record.playbook_name, record.plan_digest, record.tier) == ("azure_action", AWAITING_APPROVAL, "vm_start", DIGEST_A, "change-low")
    assert record.limit == RID and record.commit == ACTIONS_COMMIT and record.variables == {} and record.confirm == ""
    assert record.commands == [preview_answer()["line"]] and record.diff[0].startswith("~ vm-web-01")
    assert record.rollback_commands == ["Deallocate it yourself in the portal, after your own approval."]
    assert record.expires_at and record.preflight["scope"]["resource_group"] == "rg-app" and record.preflight["backout"]
    assert {(d["version"], d["format"]) for d in record.documents} == {("issued", "pdf"), ("issued", "docx")}
    (asked,) = world.action_calls("preview")
    assert asked == {"action": "vm_start", "resource_id": RID, "variables": {}, "mode": "preview", "change_id": record.id, "actor": "asha"}
    assert [k for _m, p, _b, k in world.calls if p == "/v1/action"] == ["REZZTOR_CLOUD_JOB_TOKEN"] and "/v1/plan" not in world.paths()
    kvps = world.cards[-1][1]
    assert kvps["endpoint"] == "plugins/_ea_azure_change/azure_approve" and "edit_endpoint" not in kvps
    assert "Stop here" in message and "azure_action" in message and "cannot approve" in message
    assert ("azure.action.drafted" in [e for e, _ in world.events]) and ("azure.action.document_issued" in [e for e, _ in world.events])
    assert "Removes nothing" in kvps["consequence"] and "Template" not in kvps["details"] and "vm_start" in kvps["details"]


def test_a_scale_out_is_the_high_tier_with_its_reason_and_its_values_go_to_the_executor(world):
    world.preview = preview_answer(name="vmss_scale_out", line=f"PATCH https://management.azure.com{RID}?api-version=2025-04-01 " + '{"sku":{"capacity":4}}',
                                   risk="high", cost={"monthly_usd": 100.0, "items": [], "unknown": [], "priced_at": "2026-10-03", "note": "compute only"})
    record, _ = draft(world, name="vmss_scale_out", capacity="4")
    assert record.tier == "change-high" and record.variables == {"capacity": 4} and record.preflight["risk_reasons"]
    assert world.action_calls("preview")[0]["variables"] == {"capacity": 4}
    assert "100.0 USD" in world.cards[-1][1]["details"] and "Why high risk" in world.cards[-1][1]["details"]


@pytest.mark.parametrize("answer", [
    {"ok": False, "stage": "policy", "refusal": "vm-web-01 is PowerState/running, so there is nothing to start."},
    preview_answer(checks=[{"label": "Scope", "ok": False, "detail": "outside"}]),
    preview_answer(line=f"DELETE https://management.azure.com{RID}?api-version=2025-04-01"),
    preview_answer(line=f"PUT https://management.azure.com{RID}?api-version=2025-04-01"),
    preview_answer(line=f"POST https://evil.example{RID}/start?api-version=2025-04-01"),
    preview_answer(name="vm_restart"),
    preview_answer(plan_digest=""),
    preview_answer(summary={"add": 0, "change": 2, "import": 0, "lines": [], "resources": []}),
    preview_answer(cost={"monthly_usd": 900.0, "items": [], "unknown": [], "priced_at": "x", "note": "x"}),
])
def test_a_refused_or_odd_answer_makes_no_record_no_document_and_no_card(world, answer):
    world.preview = answer
    message = run(AzureAction, **START)
    assert "no card" in message.lower() and world.cards == [] and change_store.list_records() == []


def test_a_draft_needs_a_pinned_actions_commit_a_known_action_and_a_live_inventory_resource(world, monkeypatch):
    for unpinned in ("", "none"):
        world.actions_commit = unpinned
        assert "no action list commit" in run(AzureAction, **START)
    world.actions_commit = ACTIONS_COMMIT
    assert "Actions that exist" in run(AzureAction, **{**START, "name": "vm_delete"}) and "suggestion" in run(AzureAction, **{**START, "name": "vm_stop"})
    assert "not a live resource" in run(AzureAction, **{**START, "resource": "nope"})
    assert "not a live resource" in run(AzureAction, **{**START, "resource": ""})
    from plugins._ea_azure.helpers import store

    monkeypatch.setattr(store, "get", lambda ref: {**VM, "removed_at": "2026-10-01"})
    assert "not a live resource" in run(AzureAction, **START)
    monkeypatch.setattr(store, "get", lambda ref: (_ for _ in ()).throw(store.Ambiguous([])))
    assert "more than one" in run(AzureAction, **START)
    monkeypatch.setattr(store, "get", lambda ref: VM)
    assert "needs `capacity`" in run(AzureAction, **{**START, "name": "plan_scale_out"})
    assert "needs `tags`" in run(AzureAction, **{**START, "name": "tags_merge"})
    assert world.action_calls("preview") == [] and change_store.list_records() == []


def test_the_tool_has_no_action_that_removes_stops_or_restarts(world):
    for action in ("delete", "stop", "restart", "deallocate", "scale_in", "rollback"):
        message = run(AzureAction, action=action, name="vm_start", resource="vm-web-01", change_id="x")
        assert "Unknown action" in message and "removes, stops, restarts" in message
    assert world.calls == []


def test_no_card_means_no_change_when_the_card_cannot_be_shown(world, monkeypatch):
    monkeypatch.setattr(card, "show", lambda *a: (_ for _ in ()).throw(RuntimeError("no chat")))
    message = run(AzureAction, **START)
    (record,) = change_store.list_records()
    assert record.status == CANCELLED and "cancelled" in message


def test_an_action_has_no_edit_and_the_approve_endpoint_takes_it_like_any_azure_change(world):
    record, _ = draft(world)
    assert "template change" in asyncio.run(AzureEdit(None, None).process({"change_id": record.id, "variables": {"x": 1}}, None))["error"]
    assert "never downloaded" in decide(record, reference="CR-12345")["error"]
    download(record)
    assert "CR or SR" in decide(change_store.load(record.id), reference="cr 12")["error"]
    assert decide(change_store.load(record.id), reference="cr-12345")["ok"]
    done = change_store.load(record.id)
    assert (done.status, done.reference, done.approved_by) == (APPROVED, "CR-12345", "asha")


# --- the gate and the apply ----------------------------------------------------------------------------------------------

def test_the_gate_stops_an_action_that_is_not_fully_approved_and_lets_draft_through(world):
    gate(action="draft", name="vm_start")
    record, _ = draft(world)
    with pytest.raises(RepairableException, match="not 'approved'"):
        gate(action="apply", change_id=record.id)
    with pytest.raises(RepairableException, match="change_id"):
        gate(action="apply")
    download(record)
    decide(change_store.load(record.id), reference="CR-12345")
    gate(action="apply", change_id=record.id)
    broken = change_store.load(record.id)
    broken.variables = {"capacity": 99}
    change_store.save(broken)
    with pytest.raises(RepairableException, match="altered"):
        gate(action="apply", change_id=record.id)


def test_an_approved_action_applies_once_through_the_executor_and_the_applied_document_is_written(world):
    record = approved(world)
    message = run(AzureAction, action="apply", change_id=record.id)
    done = change_store.load(record.id)
    assert done.status == APPLIED and done.verified is True and "applied and verified" in message
    (ran,) = world.action_calls("run")
    assert ran == {"action": "vm_start", "resource_id": RID, "variables": {}, "mode": "run", "plan_digest": DIGEST_A, "reference": "CR-12345",
                   "change_id": record.id, "actor": "asha"}
    assert {(d["version"], d["format"]) for d in done.documents} >= {("issued", "pdf"), ("applied", "pdf"), ("applied", "docx")}
    assert world.managed == [] and "/v1/apply" not in world.paths() and "/v1/plan" not in world.paths()
    names = [e for e, _ in world.events]
    assert {"azure.action.drafted", "azure.action.approved", "azure.action.applied"} <= set(names) and names.count("azure.action.document_issued") == 2
    assert next(kw for e, kw in world.events if e == "azure.action.applied")["reference"] == "CR-12345"
    assert "'applied'" in run(AzureAction, action="apply", change_id=record.id)
    assert len(world.action_calls("run")) == 1


def test_a_job_that_is_superseded_or_an_executor_that_refuses_leaves_the_record_so_and_nothing_made(world):
    record = approved(world)
    world.job = {**world.job, "status": "superseded", "verified": None, "changed_ids": []}
    assert "not applied" in run(AzureAction, action="apply", change_id=record.id) and change_store.load(record.id).status == SUPERSEDED
    refused = approved(world)
    world.apply_status, world.apply_error = 400, "The reference is missing or is not in the format the guard rails require."
    assert "did not run" in run(AzureAction, action="apply", change_id=refused.id)
    assert change_store.load(refused.id).status == FAILED


def test_apply_refuses_what_is_not_an_approved_simple_action(world):
    record, _ = draft(world)
    assert "Only an approved change" in run(AzureAction, action="apply", change_id=record.id)
    assert "azure_apply" in run(AzureAction, action="apply", change_id=change_store.save(records.ChangeRecord(
        requested_by="asha", status=AWAITING_APPROVAL, kind="azure_change", playbook_name="vnet")).id)
    assert "azure_action" in run(AzureApply, change_id=record.id)  # the template tool will not run an action
    assert "nope" in run(AzureAction, action="apply", change_id="nope")
    assert world.action_calls("run") == []


def test_a_subordinate_agent_and_a_standard_user_never_reach_the_executor(world):
    record = approved(world)
    with pytest.raises(RepairableException, match="subordinate"):
        asyncio.run(AzureChangeGate(agent=SimpleNamespace(number=1)).execute(tool_name="azure_action", tool_args={"action": "apply", "change_id": record.id}))
    with user_scope.user_scope(None), pytest.raises(RepairableException, match="actor"):
        gate(action="apply", change_id=record.id)
    assert world.action_calls("run") == []


def test_when_the_plugin_is_off_or_the_executor_is_not_configured_nothing_is_asked(world, monkeypatch):
    with monkeypatch.context() as off:
        off.setattr(records, "enabled", lambda: False)
        assert "switched off" in run(AzureAction, **START)
    monkeypatch.setattr(azure_action.executor_client, "is_configured", lambda agent=None: False)
    assert "not configured" in run(AzureAction, **START)


# --- savings are suggestions -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["unattached_disk", "unused_public_ip", "old_snapshot", "stopped_not_deallocated_vm", "cost", "highavailability"])
def test_a_waste_finding_or_an_advisor_saving_becomes_a_suggestion_with_its_reviewed_runbook_never_an_action(world, kind):
    message = run(AzureChange, action="suggest", kind=kind, resources=["vm-web-01"], finding="a finding from the stored inventory")
    record = change_store.load(world.cards[-1][0])
    assert (record.kind, record.status, record.tier) == ("azure_suggestion", "closed", "suggestion")
    assert records.runbook(kind) is not records.GENERIC_RUNBOOK and record.summary.startswith(records.runbook(kind)["title"])
    assert any("vm-web-01" in c for c in record.commands) and "never delete or stop" in message
    assert world.calls == [] and "endpoint" not in world.cards[-1][1]
    assert "not a simple action" in run(AzureAction, action="apply", change_id=record.id)
    assert world.calls == []


def test_a_rule_with_no_reviewed_runbook_gets_the_generic_one_and_says_so_in_its_title(world):
    run(AzureChange, action="suggest", kind="some_new_rule", resources=["vm-web-01"])
    record = change_store.load(world.cards[-1][0])
    assert record.summary.startswith("Remove or stop by hand") and records.runbook("some_new_rule") is records.GENERIC_RUNBOOK
    assert record.kind == "azure_suggestion" and world.calls == []


def test_the_advice_skill_sends_a_removal_to_suggest_and_an_addition_to_the_change_skill():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "_ea_azure" / "skills" / "azure-advice" / "SKILL.md").read_text()
    assert "azure_change" in text and "action: suggest" in text and "never do that" in text and "Remove or stop by hand" in text
    assert "that is not built" not in text
    for needed in ("kind", "resources", "finding", "azure-change"):
        assert needed in text


def test_an_executor_that_turns_an_action_away_with_429_keeps_the_approval(world):
    record = approved(world)
    world.apply_status, world.apply_error = 429, "More than 6 requests in the last minute. Nothing was queued; try again shortly."
    assert "Nothing ran and the approval stands" in run(AzureAction, action="apply", change_id=record.id)
    assert change_store.load(record.id).status == "approved"
    world.apply_status = 202
    assert "applied" in run(AzureAction, action="apply", change_id=record.id)
