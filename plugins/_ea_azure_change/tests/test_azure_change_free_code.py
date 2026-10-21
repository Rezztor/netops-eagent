"""Free code through the change tools, against the stand-in executor: the switch (off by default, and who can turn it on),
the early static check, always `change-high`, the code on the card whole, the exact code sent to the executor at plan and
at apply, and the plan the executor refuses. No test contacts Microsoft or needs a credential."""

from __future__ import annotations

import json

import pytest

from helpers import plugins
from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL, SUPERSEDED
from plugins._ea_azure_change.helpers import card, records
from plugins._ea_azure_change.tests.fake_executor import DIGEST_A, DIGEST_B, SUB, plan_answer
from plugins._ea_azure_change.tests.test_azure_change_flow import CONFIG, AzureApply, AzureChange, AzureEdit, approved, asyncio, decide, download, draft, run, world  # noqa: F401

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

CODE = 'resource "azurerm_resource_group" "x" {\n  name     = "rg-free"\n  location = "uaenorth"\n}\n'
FREE = dict(action="draft", code=CODE, subscription=SUB, resource_group="rg-free", purpose="A resource group for the free-code test.")


@pytest.fixture
def on(world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(plugins, "get_plugin_config", lambda *a, **k: {**CONFIG, "guardrails": {"allow_free_code": True}})
    return world


def sent_code(world):
    return [b["source"] for _m, p, b, _k in world.calls if p in ("/v1/plan", "/v1/apply")]


def test_free_code_is_refused_while_switched_off_and_says_who_can_turn_it_on(world):
    message = run(AzureChange, **FREE)
    assert "switched off" in message and "plugins.manage" in message and "Allow free code" in message
    assert not any(p.startswith("/v1/") for p in world.paths()) and not world.cards


@pytest.mark.parametrize("bad,why", [
    ('resource "azurerm_resource_group" "x" {\n provisioner "local-exec" { command = "id" }\n}', "provisioner"),
    ('data "http" "x" { url = "https://x" }', "data source http"),
    ('resource "azurerm_resource_group" "x" {', "could not be read"),
    ("x" * (records.CODE_MAX + 1), "approval card shows"),
    ("bad\x00code", "control character"),
    ("   ", "OpenTofu"),
    (["resource"], "OpenTofu"),
])
def test_the_code_check_runs_before_any_plan_is_asked_for(on, bad, why):
    assert why in run(AzureChange, **{**FREE, "code": bad})
    assert "/v1/plan" not in on.paths() and not on.cards


def test_a_free_code_draft_is_change_high_with_the_code_whole_on_the_card_and_the_exact_text_planned(on):
    message = run(AzureChange, **FREE)
    record = change_store.load(on.cards[-1][0])
    assert record.kind == "azure_change" and record.playbook_name == "free" and record.status == AWAITING_APPROVAL
    assert record.tier == "change-high" and "free code" in record.preflight["risk_reasons"][0]
    assert "\n".join(record.commands) == CODE and records.code_of(record) == CODE
    assert sent_code(on) == [{"code": CODE}] and "Tier: change-high" in message
    kvps = on.cards[-1][1]
    assert kvps["code"] == CODE and "commands" not in kvps and "edit_endpoint" not in kvps and kvps["tier"] == "change-high"
    assert any(row["value"] == "Free code" for row in json.loads(kvps["details"]))
    assert "no reviewed back-out" in " ".join(record.rollback_commands)


def test_free_code_is_change_high_even_when_it_is_small_free_and_the_plan_is_one_resource(on):
    on.plan_answer = plan_answer(monthly=0.0)
    run(AzureChange, **FREE)
    assert change_store.load(on.cards[-1][0]).tier == "change-high"


def test_a_free_draft_needs_a_purpose_and_no_template(on):
    assert "purpose" in run(AzureChange, **{**FREE, "purpose": ""})
    assert "no `template`" in run(AzureChange, **{**FREE, "template": "vnet"})
    assert not on.cards


def test_a_plan_the_executor_refuses_gives_no_card(on):
    on.plan_answer = {"ok": False, "stage": "policy", "refusal": "azurerm_resource_group.x would be delete."}
    message = run(AzureChange, **FREE)
    assert "would be delete" in message and "no card" in message and not on.cards


def test_code_too_long_for_the_card_is_refused_before_planning_and_the_two_limits_agree():
    from plugins._ea_interaction.helpers import cards

    assert records.CODE_MAX == cards.VALUE_BUDGET
    with pytest.raises(cards.CardRejected):
        cards._extras(None, None, None, None, None, code="x" * (cards.VALUE_BUDGET + 1))
    assert cards._extras(None, None, None, None, None, code="x" * cards.VALUE_BUDGET)["code"]


def test_a_free_change_is_approved_and_applied_with_the_exact_code_and_a_changed_plan_supersedes_it(on):
    on.plan_answer = plan_answer(DIGEST_A)
    run(AzureChange, **FREE)
    record = change_store.load(on.cards[-1][0])
    assert getattr(download(record), "status_code", None) == 200
    assert decide(change_store.load(record.id), reference="cr-12345")["ok"]
    out = run(AzureApply, action="apply", change_id=record.id)
    assert "applied" in out
    assert sent_code(on)[-1] == {"code": CODE} and "template" not in sent_code(on)[-1]
    # resume plans the same text again; another digest supersedes
    run(AzureChange, **FREE)
    again = change_store.load(on.cards[-1][0])
    assert again.id != record.id and again.status == AWAITING_APPROVAL
    run(AzureChange, action="resume", change_id=again.id)
    assert change_store.load(again.id).status == AWAITING_APPROVAL and sent_code(on)[-1] == {"code": CODE}
    on.plan_answer = plan_answer(DIGEST_B)
    run(AzureChange, action="resume", change_id=again.id)
    assert change_store.load(again.id).status == SUPERSEDED
    assert sent_code(on)[-1] == {"code": CODE}


def test_the_switch_turned_off_after_approval_stops_the_apply(on, monkeypatch):
    run(AzureChange, **FREE)
    record = change_store.load(on.cards[-1][0])
    assert getattr(download(record), "status_code", None) == 200
    assert decide(change_store.load(record.id), reference="cr-12345")["ok"]
    monkeypatch.setattr(plugins, "get_plugin_config", lambda *a, **k: {**CONFIG, "guardrails": {"allow_free_code": False}})
    out = run(AzureApply, action="apply", change_id=record.id)
    assert "switched off" in out and not [b for _m, p, b, _k in on.calls if p == "/v1/apply"]


def test_a_free_change_is_edited_with_code_and_a_template_change_is_not(on):
    run(AzureChange, **FREE)
    record = change_store.load(on.cards[-1][0])
    edit = lambda **body: asyncio.run(AzureEdit(None, None).process({"change_id": record.id, **body}, None))  # noqa: E731
    assert not edit(variables={"a": 1})["ok"]
    assert not edit(code='provider "azurerm" {}')["ok"]
    on.plan_answer = plan_answer(DIGEST_B)
    done = edit(code=CODE.replace("rg-free", "rg-free2"))
    assert done["ok"] and done["change_id"] != record.id and change_store.load(record.id).status == SUPERSEDED
    assert "rg-free2" in sent_code(on)[-1]["code"]
    assert "code" in done["card"]


def test_a_template_draft_is_unchanged_by_the_switch(on):
    from plugins._ea_azure_change.tests.test_azure_change_flow import DRAFT

    on.plan_answer = plan_answer()
    record, _ = draft(on, **DRAFT)
    assert record.playbook_name == "vnet" and records.code_of(record) is None and records.source_of(record)["template"] == "vnet"
    assert "code" not in on.cards[-1][1] and "edit_endpoint" in on.cards[-1][1]
