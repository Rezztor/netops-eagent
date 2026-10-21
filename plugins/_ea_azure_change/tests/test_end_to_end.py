"""Azure operations from the chat as one story, against the stand-in executor: the build contract's phase 2 "Verification
(coordinator)" paragraph step by step, then the same for `azure_action` (start), a suggestion, adoption and drift acceptance.

The steps are already pinned one at a time in `test_azure_change_flow.py`, `test_azure_action.py`, `test_azure_adopt.py` and
`test_azure_drift.py`; this file reuses their fixtures and helpers and adds the one thing they cannot: the whole order, so a
step that works alone but breaks the next one shows up here. Not repeated here (see the file named): the refusal of every
removal row of the plan policy (`cloud_executor/tests/test_gate.py`), every document's text (`test_document.py`), the
answer shapes the screens read (`test_answer_shapes.py`). No test contacts Microsoft or needs a credential."""

from __future__ import annotations

import asyncio
import io
import time
import zipfile
from types import SimpleNamespace

import pytest

from helpers import files, plugins, rbac, tool_permissions, user_scope
from helpers.api_policy import required_permission_for
from helpers.errors import RepairableException

from plugins._ea_ansible.extensions.python.tool_execute_before import _96_local_model_gate as local_gate
from plugins._ea_ansible.helpers import change_store, config as ansible_config
from plugins._ea_ansible.tests import register
from plugins._ea_ansible.helpers.change_model import APPLIED, APPROVED, AWAITING_APPROVAL, CLOSED, SUPERSEDED
from plugins._ea_azure.helpers import store
from plugins._ea_azure_change.api.azure_document import AzureDocument
from plugins._ea_azure_change.api.azure_edit import AzureEdit
from plugins._ea_azure_change.helpers import card, records
from plugins._ea_azure_change.tests import test_azure_action as act, test_azure_adopt as adopting, test_azure_drift as drifting
from plugins._ea_azure_change.tests.fake_executor import DIGEST_A, DIGEST_B, SUB, plan_answer, resource
from plugins._ea_azure_change.tests.test_azure_change_flow import approved, decide, download, draft, gate, run, world  # noqa: F401 (fixture)
from plugins._ea_azure_change.tools.azure_action import AzureAction
from plugins._ea_azure_change.tools.azure_apply import AzureApply
from plugins._ea_azure_change.tools.azure_change import AzureChange
from plugins._model_config.helpers import model_config

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

DIGEST_C = "c" * 64
SUBNET = resource("azurerm_subnet.s", type_="azurerm_subnet", name="snet-a")
VNET = resource("azurerm_virtual_network.vnet")
DISK = {"native_id": "/subscriptions/x/resourcegroups/rg/providers/microsoft.compute/disks/disk-old", "name": "disk-old"}


def load(change_id):
    return change_store.load(change_id)


def shape(record):
    """Which documents a record holds, as (version, format) pairs."""
    return {(d["version"], d["format"]) for d in record.documents}


# --- azure_change, in the order of the contract's paragraph -------------------------------------------------------------

def test_a_change_from_the_first_draft_to_the_as_applied_document(world):  # noqa: F811
    # draft: a record, an issued PDF and Word copy, a card, and the agent told to stop
    first, message = draft(world)
    assert first.status == AWAITING_APPROVAL and shape(first) == {("issued", "pdf"), ("issued", "docx")} and "Stop here" in message

    # redraft: the engineer changes a value; a new digest means a new record and a new document, and the old one is void
    world.plan_answer = plan_answer(DIGEST_B, resources=[VNET, SUBNET])
    edited = asyncio.run(AzureEdit(None, None).process({"change_id": first.id, "commands": 'name = "vnet-other"\nregion = "uaenorth"'}, None))
    second = load(edited["change_id"])
    assert edited["ok"] and load(first.id).status == SUPERSEDED and second.plan_digest == DIGEST_B
    assert "void" in download(first)["error"] and "not awaiting approval" in decide(first, reference="CR-12345")["error"]

    # Approve is refused before any download, and then, with the document in hand, with no number and with `cr 12`
    assert "never downloaded" in decide(second, reference="CR-12345")["error"]
    pdf, word = download(second, "pdf"), download(second, "docx")
    assert pdf.status_code == word.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert "word/document.xml" in zipfile.ZipFile(io.BytesIO(word.data)).namelist()  # opens as a Word file
    for bad in (None, "", "cr 12"):
        assert "CR or SR" in decide(load(second.id), reference=bad)["error"]
    assert load(second.id).status == AWAITING_APPROVAL and "/v1/apply" not in world.paths()

    # resume the next day: same plan, same digest, the same document and card, no new record
    day = 86400
    waiting = load(second.id)
    waiting.created_at, waiting.expires_at = waiting.created_at - day, waiting.expires_at - day
    change_store.save(waiting)
    before = len(change_store.list_records())
    assert "same digest" in run(AzureChange, action="resume", change_id=second.id)
    assert len(change_store.list_records()) == before and world.cards[-1][0] == second.id and load(second.id).documents == waiting.documents

    # resume after the plan changed: superseded, a new record with a new document naming the difference
    world.plan_answer = plan_answer(DIGEST_C, resources=[VNET, SUBNET, resource("azurerm_network_security_group.nsg", type_="azurerm_network_security_group", name="nsg-a")])
    message = run(AzureChange, action="resume", change_id=second.id)
    third = load(world.cards[-1][0])
    assert load(second.id).status == SUPERSEDED and third.id != second.id and third.plan_digest == DIGEST_C
    assert "nsg-a" in message and shape(third) == {("issued", "pdf"), ("issued", "docx")} and "void" in download(second)["error"]

    # approve with a valid number (lower case, as typed) after downloading the new document; the number is stored upper-cased
    assert "never downloaded" in decide(third, reference="CR-12345")["error"]
    assert download(third).status_code == 200
    answer = decide(load(third.id), reference="cr-12345")
    assert answer["ok"] and answer["change"]["reference"] == "CR-12345" and load(third.id).status == APPROVED

    # apply, verify, the as-applied document; the executor saw one apply, with the approved digest and the number
    assert "applied and verified" in run(AzureApply, action="apply", change_id=third.id)
    done = load(third.id)
    assert done.status == APPLIED and done.verified is True and {("applied", "pdf"), ("applied", "docx")} <= shape(done)
    applied = asyncio.run(AzureDocument(None, None).process({"change_id": done.id, "version": "applied", "format": "pdf"}, None))
    assert applied.status_code == 200 and applied.data.startswith(b"%PDF")
    (sent,) = [b for _m, p, b, _k in world.calls if p == "/v1/apply"]
    assert (sent["plan_digest"], sent["reference"], sent["change_id"]) == (DIGEST_C, "CR-12345", done.id)

    # a second apply of the same change never reaches the executor: the gate and the tool both refuse
    with pytest.raises(RepairableException, match="not 'approved'"):
        gate(change_id=done.id)
    assert "Only an approved change" in run(AzureApply, change_id=done.id)
    assert len([p for p in world.paths() if p == "/v1/apply"]) == 1
    names = [e for e, _ in world.events]
    for event in ("drafted", "document_issued", "superseded", "approved", "applied"):
        assert f"azure.change.{event}" in names, event


def test_what_is_refused_on_the_way_and_never_reaches_the_executor(world):  # noqa: F811
    # an expired draft, a wrong hash, a second decision (more: test_approve_refuses_a_wrong_hash_an_expired_draft_...)
    late, _ = draft(world)
    download(late)
    late.expires_at = time.time() - 1
    change_store.save(late)
    assert "expired" in decide(late, reference="SR-1234")["error"]
    fresh, _ = draft(world)
    download(fresh)
    assert "no longer what would run" in decide(fresh, reference="SR-1234", expected_hash="0" * 64)["error"]

    # an approval that lapsed after it was given is refused at the gate
    ready = approved(world)
    lapsed = load(ready.id)
    lapsed.expires_at = time.time() - 1
    register.force(lapsed)
    with pytest.raises(RepairableException, match="expired"):
        gate(change_id=ready.id)

    # a subordinate agent, and a call with no signed-in actor
    again = approved(world)
    with pytest.raises(RepairableException, match="subordinate"):
        gate(number=1, change_id=again.id)
    with user_scope.user_scope(None), pytest.raises(RepairableException, match="actor"):
        gate(change_id=again.id)
    assert "/v1/apply" not in world.paths()


def test_a_standard_user_can_neither_ask_nor_approve_nor_fetch_a_document_nor_set_the_rails():
    for tool in ("azure_change", "azure_apply", "azure_action", "azure_read"):
        assert not tool_permissions.is_allowed(tool, rbac.ROLE_USER), tool
        assert tool_permissions.is_allowed(tool, rbac.ROLE_POWER), tool
    wanted = {"azure_approve": rbac.PERM_NETWORK_APPROVE, "azure_edit": rbac.PERM_TOOL_NETWORK, "azure_document": rbac.PERM_TOOL_NETWORK,
              "azure_change_config": rbac.PERM_PLUGINS_MANAGE, "azure_changes": rbac.PERM_INVENTORY_VIEW}
    for handler, permission in wanted.items():
        assert required_permission_for(f"plugins/_ea_azure_change/{handler}") == permission, handler
    assert not rbac.has_permission(rbac.ROLE_USER, rbac.PERM_NETWORK_APPROVE) and not rbac.has_permission(rbac.ROLE_USER, rbac.PERM_TOOL_NETWORK)
    assert not rbac.has_permission(rbac.ROLE_POWER, rbac.PERM_PLUGINS_MANAGE) and rbac.has_permission(rbac.ROLE_ADMIN, rbac.PERM_PLUGINS_MANAGE)


@pytest.mark.parametrize("tool", ["azure_change", "azure_apply", "azure_action"])
def test_require_local_model_with_a_hosted_model_stops_every_azure_tool_and_a_local_model_lets_it_through(monkeypatch, tool):
    """The switch's own cases are in `plugins/_ea_ansible/tests/test_net_local_model.py`; this is that gate meeting these tools."""
    hosted, local = {"provider": "openai", "name": "gpt", "api_base": ""}, {"provider": "other", "name": "m", "api_base": "http://ollama:11434"}
    monkeypatch.setattr(ansible_config, "get_config", lambda agent=None: {"require_local_model": True})
    monkeypatch.setattr(model_config, "get_utility_model_config", lambda a: local)
    agent = SimpleNamespace(context=SimpleNamespace(root_agent=None))
    ext = local_gate.LocalModelGate.__new__(local_gate.LocalModelGate)
    ext.agent = agent
    monkeypatch.setattr(model_config, "get_chat_model_config", lambda a: hosted)
    with pytest.raises(RepairableException, match="not local"):
        asyncio.run(ext.execute(tool_name=tool))
    monkeypatch.setattr(model_config, "get_chat_model_config", lambda a: local)
    asyncio.run(ext.execute(tool_name=tool))


# --- the other three paths, on one stand-in that answers all of them ----------------------------------------------------

@pytest.fixture
def everything(tmp_path, monkeypatch, database):
    """The action, adoption and drift stand-ins in one (each `_send` answers its own path and passes the rest on)."""

    class Fake(drifting.Fake, adopting.Fake, act.ActionExecutor):
        pass

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(plugins, "get_plugin_config", lambda *a, **k: dict(act.CONFIG))
    monkeypatch.setattr(plugins, "get_toggle_state", lambda name: "enabled")
    monkeypatch.setattr(records, "advice_for", lambda *a, **k: [])
    monkeypatch.setattr(records, "refresh_inventory", lambda actor: ("started", 1759478500.0))
    real_get, fixed = store.get, {"vm-web-01": act.VM, act.RID: act.VM, "disk-old": DISK}
    monkeypatch.setattr(store, "get", lambda ref: fixed[ref] if ref in fixed else real_get(ref))
    events, shown = [], []
    monkeypatch.setattr(change_store, "audit", SimpleNamespace(emit=lambda event, **kw: events.append((event, kw))))
    monkeypatch.setattr(card, "show", lambda agent, record, kvps: shown.append((record.id, kvps)))
    fake = Fake(monkeypatch)
    fake.events, fake.cards = events, shown
    with user_scope.user_scope("asha"):
        yield fake


def test_starting_a_vm_is_a_change_with_its_own_number_document_and_gate_and_stopping_one_is_not_an_action(everything):
    world = everything
    for name in ("vm_stop", "vm_deallocate", "vm_restart", "vm_delete"):
        assert "no action named" in run(AzureAction, action="draft", name=name, resource="vm-web-01", purpose="x")
    assert world.action_calls("preview") == [] and world.cards == []
    record, _ = act.draft(world)
    assert record.kind == "azure_action" and record.status == AWAITING_APPROVAL and shape(record) == {("issued", "pdf"), ("issued", "docx")}
    assert "never downloaded" in decide(record, reference="CR-12345")["error"]
    assert download(record).status_code == 200
    assert "CR or SR" in decide(load(record.id), reference="cr 12")["error"]
    assert decide(load(record.id), reference="cr-12345")["ok"]
    assert "applied and verified" in run(AzureAction, action="apply", change_id=record.id)
    done = load(record.id)
    assert done.status == APPLIED and {("applied", "pdf"), ("applied", "docx")} <= shape(done)
    (ran,) = world.action_calls("run")
    assert (ran["reference"], ran["plan_digest"]) == ("CR-12345", DIGEST_A)
    assert "'applied'" in run(AzureAction, action="apply", change_id=record.id) and len(world.action_calls("run")) == 1


def test_a_removal_or_a_stop_is_a_suggestion_that_nothing_can_run(everything):
    world = everything
    assert "Unknown action" in run(AzureChange, action="delete", change_id="x")
    run(AzureChange, action="suggest", kind="unattached_disk", resources=["disk-old"], finding="unattached for 40 days")
    record = load(world.cards[-1][0])
    assert (record.kind, record.status, record.tier) == ("azure_suggestion", CLOSED, "suggestion") and "endpoint" not in world.cards[-1][1]
    assert "not an Azure change" in decide(record, reference="CR-12345")["error"] and "not an Azure change" in run(AzureApply, change_id=record.id)
    with pytest.raises(RepairableException):
        gate(change_id=record.id)
    assert world.calls == []  # nothing reached the executor: more in test_a_suggestion_is_a_closed_record_with_a_runbook_...


def test_adoption_imports_n_changes_0_and_anything_else_is_refused(everything):
    world = everything
    adopting.inventory()
    good = adopting.adopt_answer(3)
    for bad in ({**good, "summary": {**good["summary"], "change": 1}}, {**good, "summary": {**good["summary"], "add": 1}}):
        world.plan_answer = bad
        assert "no card" in run(AzureChange, **adopting.ADOPT).lower() and world.cards == []
    world.plan_answer = good
    record, message = adopting.offered(world)
    assert record.playbook_name == "adopt" and record.reference == "" and record.documents == [] and "importing 3, changes 0" in message
    with pytest.raises(RepairableException):
        gate(change_id=record.id)  # an adoption cannot be applied as a change
    world.adopt_answer = {"ok": True, "plan_digest": adopting.DIGEST, "adopted": [i["id"] for i in record.variables["adopt"]]}
    assert decide(record)["ok"] and load(record.id).status == APPLIED  # no number, no document: nothing in Azure changes
    assert "/v1/apply" not in world.paths()


def test_drift_is_shown_and_accepted_never_put_back_and_blocks_a_draft_until_it_is_accepted(everything):
    world = everything
    drifting.inventory()
    world.plan_answer = plan_answer()
    run(AzureChange, **drifting.DRAFT)
    assert world.cards == []  # refused: unaccepted drift in that workspace
    record, message = drifting.offered(world)
    assert record.playbook_name == "drift_accept" and record.reference == "" and record.documents == [] and "Nothing is put back" in message
    with pytest.raises(RepairableException):
        gate(change_id=record.id)  # an acceptance cannot be applied
    assert decide(record)["ok"] and load(record.id).status == CLOSED and store.drifted() == []
    assert "/v1/apply" not in world.paths() and "/v1/action" not in world.paths()
    world.plan_answer = plan_answer()
    run(AzureChange, **drifting.DRAFT)
    assert load(world.cards[-1][0]).kind == "azure_change"  # accepted: drafting works again
