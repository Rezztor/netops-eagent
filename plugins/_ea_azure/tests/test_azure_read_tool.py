"""azure_read: a live read is requested, approved on a card (or by the standing setting), then run, never before; the
gate, the approval endpoint and the audit around it. Against the stand-in (fake_azure.py); needs PostgreSQL. No test
reaches Microsoft."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from helpers import api_policy, rbac, tool_permissions, user_scope
from helpers.errors import RepairableException
from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.tests import register
from plugins._ea_ansible.helpers.change_model import APPROVED, AWAITING_APPROVAL, CLOSED, FAILED, REJECTED
from plugins._ea_azure.api.azure_read_approve import AzureReadApprove
from plugins._ea_azure.extensions.python.tool_execute_before._95_azure_read_gate import AzureReadGate
from plugins._ea_azure.helpers import client, config, credential, live
from plugins._ea_azure.tests import azure_read_tenant as t
from plugins._ea_azure.tests.fake_azure import CLIENT_ID, TENANT_ID, FakeAzure, hand_tenant
from plugins._ea_azure.tests.fixtures.env import install
from plugins._ea_azure.tools import azure_read
from plugins._ea_interaction.helpers import cards

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

VM1 = f"/subscriptions/{t.SUB}/resourcegroups/rg-vm/providers/microsoft.compute/virtualmachines/vm1"
VM2 = f"/subscriptions/{t.SUB}/resourcegroups/rg-vm/providers/microsoft.compute/virtualmachines/vm2"
VIEW = {"osName": "ubuntu", "statuses": [{"code": "PowerState/running", "displayStatus": "VM running", "level": "Info"}]}


@pytest.fixture
def world(database, monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    config.save({"tenant_id": TENANT_ID, "client_id": CLIENT_ID})
    credential.set_secret("fake-client-secret", "")
    t.load()
    client.reset()
    monkeypatch.setattr(live, "_sleep", lambda seconds: None)
    monkeypatch.setattr(live, "_blocked_until", 0.0)
    monkeypatch.setattr(tool_permissions, "role_for_context", lambda ctx: "power")
    events, cards_shown = [], []
    monkeypatch.setattr(azure_read.audit, "emit", lambda event, **kw: event.startswith("azure.") and events.append((event, kw)))
    monkeypatch.setattr(azure_read.AzureRead, "_show_card", lambda self, record, entry: cards_shown.append(record.id))
    with FakeAzure(hand_tenant()) as fake:
        monkeypatch.setattr(client, "TEST_ENDPOINTS", fake.cloud)
        fake.arm_answers[("GET", VM1 + "/instanceview")] = (200, VIEW, {})
        fake.arm_answers[("GET", VM2 + "/instanceview")] = (200, VIEW, {})
        fake.events, fake.cards = events, cards_shown
        with user_scope.user_scope("asha"):
            yield fake
    client.reset()


def ask(ctx="chat1", number=0, **args):
    agent = SimpleNamespace(context=SimpleNamespace(id=ctx), number=number)
    tool = azure_read.AzureRead(agent=agent, name="azure_read", method=None, args=args, message="", loop_data=None)
    return asyncio.new_event_loop().run_until_complete(tool.execute()).message


def decide(**body):
    return asyncio.run(AzureReadApprove(None, None).process(body, None))


def records():
    return [r for r in change_store.list_records() if r.kind == azure_read.KIND]


def only() -> "azure_read.ChangeRecord":
    (record,) = records()
    return record


def approve(record, by="asha"):
    with user_scope.user_scope(by):
        return decide(change_id=record.id, decision="approve", expected_hash=record.current_hash)


def sent(fake):
    return [r for r in fake.received if not r["path"].endswith("/oauth2/v2.0/token")]


def test_request_saves_a_record_and_a_card_and_sends_nothing_to_azure(world):
    message = ask(read="vm_instance_view", resource="vm1")
    record = only()
    assert (record.kind, record.status, record.limit, record.playbook_name) == ("azure_read", AWAITING_APPROVAL, VM1, "vm_instance_view")
    assert record.requested_by == "asha" and record.commit == live.commit() and record.variables == {}
    assert record.commands[0].startswith("GET http://127.0.0.1") and record.commands[0].endswith("/instanceView?api-version=2025-04-01")
    assert 890 < record.expires_at - time.time() <= 900
    assert world.cards == [record.id] and sent(world) == []
    assert "Nothing has been sent to Azure" in message and "Stop here and wait" in message
    assert [e for e, _ in world.events] == ["azure.read.requested"]
    assert record.output_excerpt == "" and record.rollback_commands == [] and record.confirm == ""


def test_the_card_is_the_decision_and_names_the_exact_request_and_the_cost_of_a_probe(world):
    ask(read="vm_instance_view", resource="vm1")
    record = only()
    card = azure_read.read_card(record, live.load()["vm_instance_view"])
    flat = json.dumps(card)
    assert "plugins/_ea_azure/azure_read_approve" in flat and record.current_hash in flat and "/instanceView" in flat
    assert "Nothing in Azure changes" in flat and "Reader" in flat
    assert "plugins/_ea_azure/azure_read_approve" in cards.AUTHORISED_ENDPOINTS
    probe = live.load()["next_hop"]
    assert "0.001 USD" in json.dumps(azure_read.read_card(record, probe)) and "Rezztor Diagnostics" in json.dumps(azure_read.read_card(record, probe))


def test_what_cannot_be_asked_gets_a_sentence_and_no_record(world):
    assert "Reads that exist" in ask(read="", resource="vm1") and "- vm_instance_view" in ask(read="nope", resource="vm1")
    assert "needs `resource`" in ask(read="vm_instance_view")
    assert "not in the stored Azure inventory" in ask(read="vm_instance_view", resource="ghost")
    assert "is a microsoft.web/sites" in ask(read="vm_instance_view", resource="app1")
    assert "needs `metric`" in ask(read="vm_metrics", resource="vm1")
    assert "must be between" in ask(read="activity_log", resource="vm1", duration="P30D")
    assert "no Network Watcher" in ask(read="next_hop", resource="vm1", source_ip="10.0.0.4", destination_ip="10.0.0.5")
    for extra in ({"url": "https://evil.example/"}, {"method": "DELETE"}, {"body": "{}"}, {"workspace": "w"}):
        assert "no argument" in ask(read="vm_instance_view", resource="vm1", **extra)
    assert records() == [] and world.cards == [] and sent(world) == []


def test_not_set_up_is_said_plainly(world, monkeypatch):
    monkeypatch.setattr(config, "is_configured", lambda: False)
    assert "not set up" in ask(read="vm_instance_view", resource="vm1") and records() == []


def test_run_refuses_until_approved_then_runs_once_and_keeps_no_answer(world):
    ask(read="vm_instance_view", resource="vm1")
    record = only()
    refused = ask(action="run", change_id=record.id)
    assert "not approved yet" in refused and "press Approve" in refused and sent(world) == []

    assert approve(record)["ok"]
    out = ask(action="run", change_id=record.id)
    assert "PowerState/running" in out and "not stored" in out and "not a standing fact" in out
    (one,) = sent(world)
    assert (one["method"], one["path"]) == ("GET", VM1 + "/instanceView")
    done = change_store.load(record.id)
    assert (done.status, done.exit_code, done.output_excerpt) == (CLOSED, 200, "")
    assert "PowerState" not in json.dumps(done.to_dict())  # the record holds the request and who approved it, never the answer
    run_event = [kw for e, kw in world.events if e == "azure.read.run"][0]
    assert run_event["read"] == "vm_instance_view" and run_event["resource"] == VM1 and run_event["approved_by"] == "asha"
    assert run_event["status"] == 200 and run_event["rows"] >= 1 and "result" not in run_event and "body" not in json.dumps(run_event)
    assert "not approved" in ask(action="run", change_id=record.id) and len(sent(world)) == 1  # not twice


def test_a_refusal_from_azure_is_reported_and_recorded(world):
    world.arm_answers[("GET", VM1 + "/instanceview")] = (403, {}, {})
    ask(read="vm_instance_view", resource="vm1")
    record = only()
    approve(record)
    out = ask(action="run", change_id=record.id)
    assert "The Azure role lacks Microsoft.Compute/virtualMachines/instanceView/read" in out
    done = change_store.load(record.id)
    assert done.status == FAILED and "403" in done.error
    assert [kw["outcome"] for e, kw in world.events if e == "azure.read.run"] == [FAILED]


def test_run_refuses_a_changed_expired_foreign_or_out_of_date_approval(world, monkeypatch):
    ask(read="vm_instance_view", resource="vm1")
    record = only()
    approve(record)

    changed = change_store.load(record.id)
    changed.commands = [*changed.commands, "GET https://elsewhere"]
    change_store.save(changed)
    assert "changed" in ask(action="run", change_id=record.id)

    back = change_store.load(record.id)
    back.commands = back.commands[:-1]
    back.expires_at = time.time() - 1
    register.force(back)
    assert "expired" in ask(action="run", change_id=record.id)

    back.expires_at = time.time() + 600
    register.force(back)
    with user_scope.user_scope("mallory"):
        assert "There is no change" in ask(action="run", change_id=record.id)  # records are per user
    assert "not made by the current user" in azure_read.run_refusal(change_store.load(record.id), "mallory")
    monkeypatch.setattr(live, "commit", lambda: "f" * 40)  # reads.yaml was edited after the approval
    assert "changed since this was approved" in ask(action="run", change_id=record.id)
    assert sent(world) == []


def test_a_record_of_another_kind_is_not_run_here(world):
    ask(read="vm_instance_view", resource="vm1")
    record = only()
    record.kind = "change"
    change_store.save(record)
    assert "not an Azure read" in ask(action="run", change_id=record.id)
    assert "There is no change" in ask(action="run", change_id="doesnotexist")


# --- the approval endpoint --------------------------------------------------------------------------------------------

def test_the_endpoint_is_for_network_approvers_and_listed():
    assert AzureReadApprove.required_permission() == rbac.PERM_NETWORK_APPROVE
    assert api_policy.required_permission_for("plugins/_ea_azure/azure_read_approve") == rbac.PERM_NETWORK_APPROVE
    assert tool_permissions.required_permission("azure_read") == rbac.PERM_TOOL_NETWORK


def test_approving_binds_the_shown_hash_and_names_both_people(world):
    ask(read="vm_instance_view", resource="vm1")
    record = only()
    with user_scope.user_scope("boss"):
        assert "no longer what would be sent" in decide(change_id=record.id, decision="approve", expected_hash="0" * 64)["error"]
        assert "no longer what would be sent" in decide(change_id=record.id, decision="approve")["error"]
        assert decide(change_id=record.id, decision="maybe")["ok"] is False
    assert change_store.load(record.id).status == AWAITING_APPROVAL
    out = approve(record, by="boss")
    assert out["ok"] and out["change"]["approved_by"] == "boss" and out["change"]["requested_by"] == "asha"
    assert change_store.load(record.id).approved_hash == record.current_hash
    assert "not awaiting approval" in decide(change_id=record.id, decision="approve", expected_hash=record.current_hash)["error"]
    assert "is 'approved'" in decide(change_id=record.id, decision="reject")["error"]
    assert "PowerState" in ask(action="run", change_id=record.id)  # the requester runs what boss approved


def test_reject_expiry_other_kinds_and_a_switched_off_plugin_are_refused_or_recorded(world, monkeypatch):
    ask(read="vm_instance_view", resource="vm1")
    record = only()
    assert decide(change_id=record.id, decision="reject")["change"]["status"] == REJECTED  # no hash needed to decline
    assert "not awaiting approval" in decide(change_id=record.id, decision="approve", expected_hash=record.current_hash)["error"]

    ask(read="vm_instance_view", resource="vm2")
    (second,) = [r for r in records() if r.status == AWAITING_APPROVAL]
    stale = change_store.load(second.id)
    stale.expires_at = time.time() - 1
    change_store.save(stale)
    assert "expired" in decide(change_id=second.id, decision="approve", expected_hash=second.current_hash)["error"]
    assert change_store.load(second.id).status == "expired"

    ask(read="vm_instance_view", resource="vm1")
    (third,) = [r for r in records() if r.status == AWAITING_APPROVAL]
    third.kind = "azure_change"  # an Azure change has its own endpoint, with a reference and a document
    change_store.save(third)
    assert "is not an Azure read" in decide(change_id=third.id, decision="approve", expected_hash=third.current_hash)["error"]
    third.kind = azure_read.KIND
    change_store.save(third)
    monkeypatch.setattr(config, "is_enabled", lambda: False)
    assert "Azure is off" in decide(change_id=third.id, decision="approve", expected_hash=third.current_hash)["error"]


# --- the standing setting (ADR 0048) ----------------------------------------------------------------------------------

def test_every_time_is_the_default_and_a_typo_never_loosens_it(world):
    assert live.read_approval() == "every_time"
    for value in ("always", "LOG_ONLY ", "", None, True):
        config.save({"read_approval": value})
        assert live.read_approval() == "every_time", value
    ask(read="vm_instance_view", resource="vm1")
    assert only().approval_basis == "" and len(world.cards) == 1


def test_log_only_needs_no_card_but_is_audited_and_runs(world):
    config.save({"read_approval": "log_only"})
    message = ask(read="vm_instance_view", resource="vm1")
    record = only()
    assert (record.status, record.approved_by, record.approval_basis) == (APPROVED, "asha", "standing:log_only")
    assert world.cards == [] and "standing read setting" in message and record.approved_hash == record.current_hash
    assert [e for e, _ in world.events] == ["azure.read.requested", "azure.read.auto_approved"]
    assert "PowerState/running" in ask(action="run", change_id=record.id)


def test_a_standing_setting_does_not_cover_someone_who_could_not_press_approve(world, monkeypatch):
    config.save({"read_approval": "log_only"})
    monkeypatch.setattr(tool_permissions, "role_for_context", lambda ctx: "user")
    ask(read="vm_instance_view", resource="vm1")
    assert only().status == AWAITING_APPROVAL and len(world.cards) == 1


def test_once_per_chat_asks_once_per_resource_per_chat_and_only_after_a_card_approval(world):
    config.save({"read_approval": "once_per_chat"})
    ask(ctx="c1", read="vm_instance_view", resource="vm1")
    first = only()
    assert first.status == AWAITING_APPROVAL
    ask(ctx="c1", read="vm_instance_view", resource="vm1")  # the first was never approved: still a card
    assert len(world.cards) == 2
    approve(first)
    ask(ctx="c1", read="vm_instance_view", resource="vm1")
    auto = [r for r in records() if r.approval_basis == "standing:once_per_chat"]
    assert len(auto) == 1 and len(world.cards) == 2
    ask(ctx="c1", read="vm_instance_view", resource="vm2")  # another resource
    ask(ctx="c2", read="vm_instance_view", resource="vm1")  # another chat
    assert len(world.cards) == 4
    ask(ctx="c1", read="vm_instance_view", resource="vm1")  # an auto-approved read does not count as a card approval for more
    assert len([r for r in records() if r.approval_basis == "standing:once_per_chat"]) == 2


# --- the gate ---------------------------------------------------------------------------------------------------------

def gate(tool="azure_read", number=0, **tool_args):
    extension = AzureReadGate(agent=SimpleNamespace(number=number))
    asyncio.run(extension.execute(tool_name=tool, tool_args=tool_args))


def test_the_gate_lets_a_request_and_other_tools_pass_and_only_runs_an_approved_unchanged_read(world):
    gate(action="request", read="vm_instance_view", resource="vm1")
    gate(tool="azure_inventory", action="run")
    ask(read="vm_instance_view", resource="vm1")
    record = only()
    for tool_args, why in (({"action": "run"}, "takes a change_id"), ({"action": "run", "change_id": record.id}, "not approved yet"),
                           ({"action": "RUN", "change_id": "missing"}, "could not complete")):
        with pytest.raises(RepairableException, match=why):
            gate(**tool_args)
    approve(record)
    gate(action="run", change_id=record.id)  # approved, unchanged, in date
    edited = change_store.load(record.id)
    edited.commands = [*edited.commands, "POST https://elsewhere"]
    change_store.save(edited)
    with pytest.raises(RepairableException, match="changed"):
        gate(action="run", change_id=record.id)
    with pytest.raises(RepairableException, match="could not complete|not approved|changed"):
        gate(action="delete", change_id=record.id)  # anything but a request is held to the same checks


def test_the_gate_refuses_a_subordinate_an_unattended_run_and_a_broken_check(world, monkeypatch):
    ask(read="vm_instance_view", resource="vm1")
    record = only()
    approve(record)
    with pytest.raises(RepairableException, match="subordinate"):
        gate(number=1, action="run", change_id=record.id)
    with user_scope.user_scope(""):
        with pytest.raises(RepairableException, match="no identifiable actor"):
            gate(action="run", change_id=record.id)
    monkeypatch.setattr(azure_read, "run_refusal", lambda record, actor: 1 / 0)
    with pytest.raises(RepairableException, match="could not complete its checks"):
        gate(action="run", change_id=record.id)
    with pytest.raises(RepairableException, match="No agent context"):
        asyncio.run(AzureReadGate(agent=None).execute(tool_name="azure_read", tool_args={"action": "run", "change_id": record.id}))
