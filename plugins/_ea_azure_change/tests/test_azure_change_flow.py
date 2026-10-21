"""An Azure change from draft to applied, against the stand-in executor (`fake_executor.py`): the draft and its document,
the card, the approval (CR or SR number, document downloaded), the gate, the apply and what follows it, a resumed or edited
plan, and the suggestion that never reaches the executor. No test contacts Microsoft or needs a credential."""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace

import pytest

from helpers import files, plugins, user_scope
from helpers.errors import RepairableException

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.tests import register
from plugins._ea_ansible.helpers.change_model import APPLIED, APPROVED, AWAITING_APPROVAL, CANCELLED, CLOSED, FAILED, REJECTED, SUPERSEDED
from plugins._ea_azure_change.api.azure_approve import AzureApprove
from plugins._ea_azure_change.api.azure_document import AzureDocument
from plugins._ea_azure_change.api.azure_edit import AzureEdit
from plugins._ea_azure_change.extensions.python.tool_execute_before._95_azure_change_gate import AzureChangeGate
from plugins._ea_azure_change.helpers import card, records
from plugins._ea_azure_change.tests.fake_executor import DIGEST_A, DIGEST_B, SUB, FakeExecutor, plan_answer, resource
from plugins._ea_azure_change.tools.azure_apply import AzureApply
from plugins._ea_azure_change.tools.azure_change import AzureChange

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

CONFIG = {"executor_url": "unix:///run/cloud-socket/cloud.sock", "timeout_seconds": 5, "apply_wait_seconds": 5, "guardrails": {}}
DRAFT = dict(action="draft", template="vnet", subscription=SUB, resource_group="rg-test", purpose="Network for the test environment.",
             name="vnet-test", region="uaenorth", address_space=[{"cidr": "10.20.0.0/16"}], tags={"env": "test", "owner": "netops", "costcentre": "1"})


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(plugins, "get_plugin_config", lambda *a, **k: dict(CONFIG))
    monkeypatch.setattr(plugins, "get_toggle_state", lambda name: "enabled")
    monkeypatch.setattr(records, "advice_for", lambda *a, **k: [])
    monkeypatch.setattr(records, "refresh_inventory", lambda actor: ("started", 1759478500.0))
    monkeypatch.setattr(records, "record_managed", lambda record, ids: len(ids))
    events, shown = [], []
    monkeypatch.setattr(change_store, "audit", SimpleNamespace(emit=lambda event, **kw: events.append((event, kw))))
    monkeypatch.setattr(card, "show", lambda agent, record, kvps: shown.append((record.id, kvps)))
    fake = FakeExecutor(monkeypatch)
    fake.events, fake.cards = events, shown
    with user_scope.user_scope("asha"):
        yield fake


def run(cls, number=0, **args) -> str:
    agent = SimpleNamespace(context=SimpleNamespace(id="chat1"), number=number)
    return asyncio.run(cls(agent=agent, name="t", method=None, args=args, message="", loop_data=None).execute()).message


def draft(world, **over):
    message = run(AzureChange, **{**DRAFT, **over})
    return change_store.load(world.cards[-1][0]), message


def decide(record, decision="approve", **body):
    return asyncio.run(AzureApprove(None, None).process({"change_id": record.id, "decision": decision,
                                                         "expected_hash": record.current_hash, **body}, None))


def download(record, fmt="pdf", version="issued"):
    return asyncio.run(AzureDocument(None, None).process({"change_id": record.id, "version": version, "format": fmt}, None))


def approved(world, reference="cr-12345"):
    record, _ = draft(world)
    assert getattr(download(record), "status_code", None) == 200
    answer = decide(change_store.load(record.id), reference=reference)
    assert answer["ok"], answer
    return change_store.load(record.id)


def gate(tool="azure_apply", number=0, **tool_args):
    asyncio.run(AzureChangeGate(agent=SimpleNamespace(number=number)).execute(tool_name=tool, tool_args=tool_args))


# --- draft ------------------------------------------------------------------------------------------------------------

def test_a_draft_plans_writes_the_document_and_shows_the_card_and_tells_the_agent_to_stop(world):
    record, message = draft(world)
    assert (record.kind, record.status, record.plan_digest, record.tier) == ("azure_change", AWAITING_APPROVAL, DIGEST_A, "change-low")
    assert record.limit == f"{SUB}/rg-test" and record.commit == "c" * 40 and record.approved_by == "" and record.reference == ""
    assert {(d["version"], d["format"]) for d in record.documents} == {("issued", "pdf"), ("issued", "docx")}
    for meta in record.documents:
        assert records.file_sha256(records.document_path(record, meta)) == meta["sha256"]
    plan = next(c for c in world.calls if c[1] == "/v1/plan")
    assert plan[2]["mode"] == "apply" and plan[2]["source"]["template"] == "vnet" and plan[2]["workspace"]["resource_group"] == "rg-test"
    assert plan[3] == "REZZTOR_CLOUD_JOB_TOKEN"
    kvps = world.cards[-1][1]
    assert kvps["endpoint"] == "plugins/_ea_azure_change/azure_approve" and "Stop here" in message and "cannot approve" in message
    assert ("azure.change.drafted" in [e for e, _ in world.events]) and ("azure.change.document_issued" in [e for e, _ in world.events])


def test_a_plan_that_removes_anything_is_refused_with_no_record_no_document_and_no_card(world):
    for action in ("delete", "replace", "forget", "delete,create", "stop"):
        world.plan_answer = plan_answer(resources=[resource("azurerm_virtual_network.vnet", action=action)])
        message = run(AzureChange, **DRAFT)
        assert "never performs" in message and "no card" in message.lower()
    assert world.cards == [] and change_store.list_records() == []


@pytest.mark.parametrize("answer", [
    {"ok": False, "stage": "policy", "refusal": "a subnet would be removed"},
    plan_answer(checks=[{"label": "Plan policy", "ok": False, "detail": "nope"}]),
    plan_answer(monthly=900.0),
    {**plan_answer(), "plan_digest": ""},
    plan_answer(resources=[]),
])
def test_a_refused_or_failing_or_too_dear_or_empty_plan_makes_no_card(world, answer):
    world.plan_answer = answer
    run(AzureChange, **DRAFT)
    assert world.cards == [] and change_store.list_records() == []


def test_draft_refuses_free_code_a_bad_scope_an_unknown_template_and_a_stranger_but_has_no_delete(world):
    assert "Free code" in run(AzureChange, action="draft", code='resource "x" "y" {}')
    assert "GUID" in run(AzureChange, **{**DRAFT, "subscription": "sub"})
    assert "Templates that exist" in run(AzureChange, **{**DRAFT, "template": "nuke_it"})
    message = run(AzureChange, action="delete", change_id="x")
    assert "Unknown action" in message and "removes or stops" in message
    assert "/v1/plan" not in world.paths()


def test_no_card_means_no_change_when_the_card_cannot_be_shown(world, monkeypatch):
    monkeypatch.setattr(card, "show", lambda *a: (_ for _ in ()).throw(RuntimeError("no chat")))
    message = run(AzureChange, **DRAFT)
    (record,) = change_store.list_records()
    assert record.status == CANCELLED and "cancelled" in message


# --- approval ---------------------------------------------------------------------------------------------------------

def test_approve_needs_the_document_downloaded_and_a_cr_or_sr_number_in_the_customers_format(world):
    record, _ = draft(world)
    assert "never downloaded" in decide(record, reference="CR-12345")["error"]
    assert download(record).status_code == 200
    fresh = change_store.load(record.id)
    assert fresh.documents[0]["downloaded_at"] or fresh.documents[1]["downloaded_at"]
    for bad in (None, "", "   ", "cr 12", "CR-12", "CHG-12345", "CR-12345; DROP", "CR-" + "1" * 40):
        assert "CR or SR" in decide(fresh, reference=bad)["error"], bad
    assert change_store.load(record.id).status == AWAITING_APPROVAL
    assert decide(fresh, reference="cr12345")["ok"]
    done = change_store.load(record.id)
    assert (done.status, done.reference, done.approved_by, done.approved_hash) == (APPROVED, "CR12345", "asha", done.current_hash)
    assert ("azure.change.approved", "CR12345") in [(e, kw.get("reference")) for e, kw in world.events]


def test_approve_refuses_a_wrong_hash_an_expired_draft_a_second_decision_and_another_user(world):
    record, _ = draft(world)
    download(record)
    assert "no longer what would run" in decide(record, reference="SR-1234", expected_hash="0" * 64)["error"]
    with user_scope.user_scope("bilal"):
        assert "Only the engineer who requested" in decide(record, reference="SR-1234")["error"]
    assert decide(record, reference="SR-1234")["ok"]
    assert "not awaiting approval" in decide(record, reference="SR-1234")["error"]
    late, _ = draft(world)
    late.expires_at = time.time() - 1
    change_store.save(late)
    assert "expired" in decide(late, reference="SR-1234")["error"] and change_store.load(late.id).status == "expired"


def test_reject_needs_no_number_no_hash_and_no_download(world):
    record, _ = draft(world)
    assert decide(record, "reject", expected_hash="")["ok"] and change_store.load(record.id).status == REJECTED


def test_the_approval_endpoint_refuses_a_suggestion_a_read_and_a_device_record(world):
    for kind in ("azure_suggestion", "azure_read", "change"):
        other = change_store.save(records.ChangeRecord(requested_by="asha", status=AWAITING_APPROVAL, kind=kind, playbook_name="x"))
        assert "not an Azure change" in decide(other, reference="CR-12345")["error"]


def test_a_document_file_changed_after_issue_blocks_approval_and_the_download(world):
    record, _ = draft(world)
    download(record)
    meta = record.documents[0]
    with open(records.document_path(record, meta), "ab") as handle:
        handle.write(b"tampered")
    assert "no longer matches" in decide(record, reference="CR-12345")["error"]
    assert "no longer matches" in download(change_store.load(record.id), meta["format"])["error"]


def test_a_superseded_changes_document_is_void_and_cannot_be_downloaded(world):
    record, _ = draft(world)
    record.status = SUPERSEDED
    change_store.save(record)
    assert "void" in download(record)["error"]


# --- the gate and the apply ---------------------------------------------------------------------------------------------

def test_the_gate_lets_other_tools_and_actions_pass_and_refuses_a_change_that_is_not_fully_approved(world):
    gate(tool="azure_inventory", action="apply")
    gate(action="close", change_id="whatever")
    gate(tool="azure_action", action="draft")
    record, _ = draft(world)
    with pytest.raises(RepairableException, match="not 'approved'"):
        gate(change_id=record.id)
    with pytest.raises(RepairableException, match="change_id"):
        gate()
    with pytest.raises(RepairableException, match="subordinate"):
        gate(number=1, change_id=record.id)
    with pytest.raises(RepairableException, match="No agent"):
        asyncio.run(AzureChangeGate(agent=None).execute(tool_name="azure_apply", tool_args={"change_id": record.id}))
    with user_scope.user_scope(None), pytest.raises(RepairableException, match="actor"):
        gate(change_id=record.id)


def test_the_gate_passes_only_when_every_condition_holds_and_names_the_first_that_does_not(world, monkeypatch):
    record = approved(world)
    gate(change_id=record.id)
    with monkeypatch.context() as changed:
        changed.setattr(records, "rails", lambda agent=None: {**records.RAIL_DEFAULTS, "reference_pattern": "PO-[0-9]{9}"})
        with pytest.raises(RepairableException, match="CR or SR number"):
            gate(change_id=record.id)
    for attr, value, text in (("reference", "", "number"), ("approved_hash", "", "plan hash"), ("applied_at", 1.0, "already applied"),
                              ("approved_by", "", "No engineer"), ("expires_at", time.time() - 5, "expired"), ("variables", {"name": "x"}, "altered")):
        broken = change_store.load(record.id)
        setattr(broken, attr, value)
        register.force(broken)
        with pytest.raises(RepairableException, match=text):
            gate(change_id=record.id)
        register.force(record)


def test_the_gate_refuses_when_the_document_was_never_downloaded_or_is_gone(world):
    record = approved(world)
    for meta in record.documents:
        meta["downloaded_at"] = None
    change_store.save(record)
    with pytest.raises(RepairableException, match="never downloaded"):
        gate(change_id=record.id)
    for meta in record.documents:
        meta["downloaded_at"] = "2026-10-03T10:00:00+00:00"
        os.remove(records.document_path(record, meta))
    change_store.save(record)
    with pytest.raises(RepairableException, match="missing"):
        gate(change_id=record.id)


def test_apply_runs_the_approved_plan_once_with_the_number_then_verifies_pulls_and_writes_the_second_document(world):
    record = approved(world)
    message = run(AzureApply, action="apply", change_id=record.id)
    done = change_store.load(record.id)
    assert done.status == APPLIED and done.verified is True and done.job_id == "job1" and "applied and verified" in message
    body = next(c for c in world.calls if c[1] == "/v1/apply")[2]
    assert (body["plan_digest"], body["reference"], body["change_id"], body["actor"]) == (DIGEST_A, "CR-12345", record.id, "asha")
    assert body["source"] == {"template": "vnet", "variables": record.variables} and "mode" not in body
    assert {(d["version"], d["format"]) for d in done.documents} >= {("applied", "pdf"), ("applied", "docx")}
    assert any("applied-CR-12345" in d["filename"] for d in done.documents)
    assert "Inventory pull: started" in message and "azure.change.applied" in [e for e, _ in world.events]
    with pytest.raises(RepairableException, match="not 'approved'"):
        gate(change_id=record.id)  # a second apply is refused at the gate


def test_a_plan_that_moved_since_approval_is_superseded_by_the_executor_and_nothing_is_called_applied(world):
    record = approved(world)
    world.job = {"status": "superseded", "verified": None, "changed_ids": [], "started_at": 1.0, "ended_at": 2.0}
    message = run(AzureApply, change_id=record.id)
    done = change_store.load(record.id)
    assert done.status == SUPERSEDED and done.applied_at is None and "nothing ran" in message
    assert not any(d["version"] == "applied" for d in done.documents)


def test_an_executor_that_refuses_the_apply_leaves_a_failed_change_and_a_failed_job_a_failed_document(world):
    record = approved(world)
    world.apply_status = 409
    world.apply_error = "change already applied"
    assert "did not run" in run(AzureApply, change_id=record.id)
    assert change_store.load(record.id).status == FAILED
    again = approved(world)
    world.apply_status = 202
    world.job = {"status": "failed", "error": "quota exceeded", "changed_ids": [], "started_at": 1.0, "ended_at": 2.0}
    assert "failed" in run(AzureApply, change_id=again.id) and change_store.load(again.id).error == "quota exceeded"


def test_a_job_still_running_is_settled_by_status_later(world, monkeypatch):
    record = approved(world)
    world.job = {"status": "running", "started_at": 1.0}
    monkeypatch.setattr(records.executor_client, "wait", lambda agent, job_id, seconds: world.job)
    assert "still applying" in run(AzureApply, change_id=record.id)
    assert change_store.load(record.id).status == "applying"
    world.job = {"status": "verify_failed", "verified": False, "changed_ids": [], "started_at": 1.0, "ended_at": 5.0}
    assert "did not find the intended state" in run(AzureChange, action="status", change_id=record.id)
    assert change_store.load(record.id).status == "verify_failed"


def test_apply_refuses_an_unapproved_change_a_suggestion_and_close_only_a_finished_one(world):
    record, _ = draft(world)
    assert "Only an approved change" in run(AzureApply, change_id=record.id)
    assert "only a finished change" in run(AzureApply, action="close", change_id=record.id)
    assert "Unknown action" in run(AzureApply, action="rollback", change_id=record.id)
    assert "/v1/apply" not in world.paths()


# --- resume and edit ----------------------------------------------------------------------------------------------------

def test_resume_with_the_same_digest_shows_the_same_card_again_and_notes_it(world):
    record, _ = draft(world)
    message = run(AzureChange, action="resume", change_id=record.id)
    assert "same digest" in message and len(change_store.list_records()) == 1
    assert world.cards[-1][0] == record.id
    assert any("same digest as the document" in c["label"] for c in card.change_card_args(world_record(world, record))["checks"])


def world_record(world, record):
    again = change_store.load(record.id)
    again.preflight["checks"] = [{"label": "Planned again today: same digest as the document", "ok": True}]
    return again


def test_resume_after_the_plan_moved_supersedes_the_old_change_and_makes_a_new_one_with_a_new_document(world):
    record, _ = draft(world)
    world.plan_answer = plan_answer(DIGEST_B, resources=[resource("azurerm_virtual_network.vnet"), resource("azurerm_subnet.s", type_="azurerm_subnet", name="snet-a")])
    message = run(AzureChange, action="resume", change_id=record.id)
    old, new = change_store.load(record.id), change_store.load(world.cards[-1][0])
    assert old.status == SUPERSEDED and new.status == AWAITING_APPROVAL and new.id != old.id and new.plan_digest == DIGEST_B
    assert "superseded" in message and "snet-a" in message and new.documents and new.documents[0]["plan_digest"] == DIGEST_B
    assert "void" in download(old)["error"]
    assert "azure.change.superseded" in [e for e, _ in world.events]


def test_resume_refuses_what_is_not_waiting_or_expired(world):
    record, _ = draft(world)
    record.expires_at = time.time() - 1
    change_store.save(record)
    assert "expired" in run(AzureChange, action="resume", change_id=record.id)
    assert "awaiting approval" in run(AzureChange, action="resume", change_id=record.id)


def test_edit_replans_and_supersedes_when_the_digest_moves_and_changes_nothing_when_it_does_not(world):
    record, _ = draft(world)
    same = asyncio.run(AzureEdit(None, None).process({"change_id": record.id, "variables": record.variables}, None))
    assert same["ok"] and same["unchanged"] and change_store.load(record.id).status == AWAITING_APPROVAL
    world.plan_answer = plan_answer(DIGEST_B)
    answer = asyncio.run(AzureEdit(None, None).process({"change_id": record.id, "commands": 'name = "vnet-other"\nregion = "uaenorth"'}, None))
    assert answer["ok"] and answer["change_id"] != record.id and "card" in answer
    assert change_store.load(record.id).status == SUPERSEDED and change_store.load(answer["change_id"]).variables["name"] == "vnet-other"
    with user_scope.user_scope("bilal"):
        assert not asyncio.run(AzureEdit(None, None).process({"change_id": answer["change_id"], "variables": {"name": "x"}}, None))["ok"]
    assert not asyncio.run(AzureEdit(None, None).process({"change_id": answer["change_id"], "commands": "not a line"}, None))["ok"]


# --- the suggestion -----------------------------------------------------------------------------------------------------

def test_a_suggestion_is_a_closed_record_with_a_runbook_that_nothing_can_run_and_the_executor_never_sees(world, monkeypatch):
    from plugins._ea_azure.helpers import store

    disk = {"native_id": "/subscriptions/x/resourcegroups/rg/providers/microsoft.compute/disks/disk-old", "name": "disk-old"}
    monkeypatch.setattr(store, "get", lambda ref: disk if ref == "disk-old" else None)
    assert "not in the stored Azure inventory" in run(AzureChange, action="suggest", kind="unattached_disk", resources=["nope"])
    message = run(AzureChange, action="suggest", kind="unattached_disk", resources=["disk-old"], finding="unattached for 40 days")
    record = change_store.load(world.cards[-1][0])
    assert (record.kind, record.status, record.tier, record.limit) == ("azure_suggestion", CLOSED, "suggestion", disk["native_id"])
    assert [(d["version"], d["format"]) for d in record.documents] == [("runbook", "pdf")] and any("disk-old" in c for c in record.commands)
    assert "never delete or stop" in message and world.calls == []
    assert "endpoint" not in world.cards[-1][1]
    assert "not an Azure change" in decide(record, reference="CR-12345")["error"]
    assert "not an Azure change" in run(AzureApply, change_id=record.id)
    with pytest.raises(RepairableException):
        gate(change_id=record.id)
    assert "azure.suggestion.written" in [e for e, _ in world.events]
    assert world.calls == []


# --- the register -------------------------------------------------------------------------------------------------------

def test_the_register_lists_azure_changes_with_reference_digest_and_documents_and_filters_by_number(world):
    from plugins._ea_azure_change.api.azure_changes import AzureChanges

    first = approved(world, "CR-11111")
    draft(world)
    listed = asyncio.run(AzureChanges(None, None).process({}, None))
    assert len(listed["changes"]) == 2 and listed["changes"][0]["reference"] == ""
    only = asyncio.run(AzureChanges(None, None).process({"reference": "cr-11111"}, None))["changes"]
    assert [c["id"] for c in only] == [first.id] and only[0]["plan_digest"] == DIGEST_A
    assert {d["version"] for d in only[0]["documents"]} == {"issued"} and any(d["downloaded"] for d in only[0]["documents"])


def test_what_an_apply_made_is_listed_for_drift_by_lower_cased_id_and_listing_it_again_moves_it(database):
    from helpers import db

    record = records.ChangeRecord(id="chg1", limit=f"{SUB}/rg-test")
    assert records.record_managed(record, ["/subscriptions/X/resourceGroups/Rg/providers/Microsoft.Network/virtualNetworks/V"]) == 1
    records.record_managed(records.ChangeRecord(id="chg2", limit=f"{SUB}/rg-test"), ["/subscriptions/x/resourcegroups/rg/providers/microsoft.network/virtualnetworks/v"])
    with db.transaction(db.NO_SCOPE) as conn:
        rows = conn.execute("SELECT native_id, workspace, change_id FROM cloud_managed").fetchall()
    assert [tuple(r) for r in rows] == [("/subscriptions/x/resourcegroups/rg/providers/microsoft.network/virtualnetworks/v", f"{SUB}/rg-test", "chg2")]


# --- what the card is told: its state, the order, a redraft ----------------------------------------------------------

def lookup(record_id):
    from plugins._ea_azure_change.api.azure_changes import AzureChanges

    return asyncio.run(AzureChanges(None, None).process({"change_id": record_id}, None))


def test_the_card_can_ask_for_the_state_of_one_change_and_only_its_own(world, monkeypatch):
    from helpers import login

    monkeypatch.setattr(login, "current_username", lambda: "asha-name")
    record, _ = draft(world)
    row = lookup(record.id)["change"]
    assert (row["status"], row["downloaded"], row["reference"], row["approved_by_name"]) == (AWAITING_APPROVAL, False, "", "")
    assert download(record, fmt="docx").status_code == 200
    assert lookup(record.id)["change"]["downloaded"] is True
    assert decide(change_store.load(record.id), reference="cr-12345")["ok"]
    row = lookup(record.id)["change"]
    assert (row["status"], row["reference"], row["approved_by_name"]) == (APPROVED, "CR-12345", "asha-name") and row["approved_at"]
    assert not lookup("nope")["ok"]
    with user_scope.user_scope("bilal"):
        assert not lookup(record.id)["ok"]


def test_a_redraft_with_replaces_supersedes_the_open_one_and_only_that_one(world):
    first, _ = draft(world)
    second, message = draft(world, replaces=first.id, name="vnet-other")
    assert change_store.load(first.id).status == SUPERSEDED and second.status == AWAITING_APPROVAL and first.id in message
    assert lookup(first.id)["change"]["status"] == SUPERSEDED
    before = len(world.cards)
    assert "cannot be replaced" in run(AzureChange, **{**DRAFT, "replaces": first.id})  # no longer open
    assert "cannot be replaced" in run(AzureChange, **{**DRAFT, "replaces": second.id, "resource_group": "rg-other"})  # another group
    assert "no change" in run(AzureChange, **{**DRAFT, "replaces": "nope"}).lower()
    assert len(world.cards) == before and change_store.load(second.id).status == AWAITING_APPROVAL
    third, _ = draft(world, name="vnet-third")  # without `replaces` nothing is retired
    assert change_store.load(second.id).status == AWAITING_APPROVAL and third.id != second.id


def test_every_instruction_to_the_user_gives_the_same_three_steps_in_the_same_order(world):
    from pathlib import Path

    here = Path(__file__).resolve().parents[1]
    for name in ("prompts/agent.system.tool.azure_change.md", "prompts/agent.system.tool.azure_action.md",
                 "prompts/agent.system.tool.azure_apply.md", "skills/azure-change/SKILL.md"):
        assert records.STEPS in (here / name).read_text(), name
    _, message = draft(world)
    assert records.STEPS in message
    record = change_store.load(world.cards[-1][0])
    for text in (run(AzureApply, change_id=record.id), records.pass_on(records.issued_document_problem(record))):
        assert records.STEPS in text and "as written" in text
    assert "Step 1 on the card" in records.issued_document_problem(record)
    with pytest.raises(RepairableException, match="3. Press Approve"):
        gate(change_id=record.id)


def test_an_executor_that_turns_a_single_apply_away_with_429_keeps_the_approval_and_the_next_apply_runs(world):
    record = approved(world)
    world.busy = 1
    message = run(AzureApply, change_id=record.id)
    assert "Nothing ran and the approval stands" in message
    kept = change_store.load(record.id)
    assert kept.status == "approved" and kept.applied_at is None and not kept.job_id
    assert "applied and verified" in run(AzureApply, change_id=record.id)


def test_a_subscription_given_by_name_is_resolved_and_an_unknown_name_lists_them_with_guids(world, monkeypatch):
    from plugins._ea_azure.helpers import store
    monkeypatch.setattr(store, "subscriptions", lambda: [{"native_id": SUB, "name": "Prod", "readable": True}])
    record, _ = draft(world, subscription="Prod")  # a name, not the GUID: the trigger of the incident
    assert record.limit == f"{SUB}/rg-test"
    out = run(AzureChange, **{**DRAFT, "subscription": "Nowhere"})
    assert "No subscription" in out and SUB in out and "Prod" in out and world.cards[-1][0] == record.id  # no new card


def test_an_approved_record_file_put_back_after_the_apply_is_not_applied_again(world, tmp_path):
    """ADR 0077: the approval is a row that was used, so the signed, approved, unexpired file is no approval any more."""
    import shutil

    record = approved(world)
    copy = shutil.copy(change_store._path(record.id), tmp_path / "approved.json")
    stale = change_store.load(record.id)  # what a second call read while the change was still approved
    assert "applied and verified" in run(AzureApply, action="apply", change_id=record.id)

    shutil.copy(copy, change_store._path(record.id))
    with pytest.raises(RepairableException, match="not 'approved'"):
        gate(change_id=record.id)
    assert "Only an approved change" in run(AzureApply, action="apply", change_id=record.id)
    tool = AzureApply(agent=None, name="t", method=None, args={}, message="", loop_data=None)
    assert "no approval left" in asyncio.run(tool._run(stale, "asha")).message
    assert len([c for c in world.calls if c[1] == "/v1/apply"]) == 1 and change_store.load(record.id).status == APPLIED
