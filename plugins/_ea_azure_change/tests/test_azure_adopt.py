"""Adoption through the change plugin: what is offered from the stored inventory, the card ("importing N, changes 0", no number, no
document), the approval that writes the executor's state and lists the resources as managed, and the proof that an adoption can
never run as a change and that a plan that changes anything makes no card. Stand-in executor, stored inventory in PostgreSQL."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

from helpers import files, plugins, user_scope
from helpers.errors import RepairableException

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import APPLIED, APPROVED, AWAITING_APPROVAL, CANCELLED, FAILED, REJECTED, SUPERSEDED, ChangeRecord
from plugins._ea_azure.helpers import store
from plugins._ea_azure.tests import azure_read_tenant as t
from plugins._ea_azure_change.api.azure_approve import AzureApprove
from plugins._ea_azure_change.extensions.python.tool_execute_before._95_azure_change_gate import AzureChangeGate
from plugins._ea_azure_change.helpers import adopt, card, conditions, records
from plugins._ea_azure_change.tests.fake_executor import SUB, FakeExecutor, resource
from plugins._ea_azure_change.tools.azure_apply import AzureApply
from plugins._ea_azure_change.tools.azure_change import AzureChange

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

CONFIG = {"executor_url": "unix:///run/cloud-socket/cloud.sock", "timeout_seconds": 5, "apply_wait_seconds": 5, "guardrails": {}}
ADOPT = {"action": "adopt", "subscription": SUB, "resource_group": "rg-test"}
DIGEST = "e" * 64
BASE = f"/subscriptions/{SUB}/resourcegroups/rg-test/providers/microsoft.network"
VNET, SUBNET, NSG = f"{BASE}/virtualnetworks/vnet-a", f"{BASE}/virtualnetworks/vnet-a/subnets/snet-app", f"{BASE}/networksecuritygroups/nsg-a"
VAULT = f"/subscriptions/{SUB}/resourcegroups/rg-test/providers/microsoft.keyvault/vaults/kv-a"


def adopt_answer(n=2, digest=DIGEST, **over):
    resources = [resource(f"azapi_resource.r{i}", "import", "azapi_resource", f"r{i}") for i in range(1, n + 1)]
    return {"ok": True, "plan_digest": digest, "duration_s": 1, "adopting": [],
            "summary": {"add": 0, "change": 0, "import": n, "lines": [f"= {r['address']}  import /subscriptions/x/{i}" for i, r in enumerate(resources)],
                        "resources": resources},
            "checks": [{"label": "Providers", "ok": True}, {"label": f"Importing {n}, changes 0", "ok": True}],
            "cost": {"monthly_usd": 0.0, "items": [], "unknown": [], "priced_at": ""}, "drift": [], **over}


class Fake(FakeExecutor):
    """The stand-in plus the one call this phase adds."""

    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.plan_answer = adopt_answer()
        self.adopt_answer = {"ok": True, "plan_digest": DIGEST, "adopted": [VNET, SUBNET]}

    def _send(self, agent, method, path, body=None, *, key="", timeout=None):
        if path == "/v1/adopt/apply":
            self.calls.append((method, path, body, key))
            return 200, self.adopt_answer
        return super()._send(agent, method, path, body, key=key, timeout=timeout)


@pytest.fixture
def world(tmp_path, monkeypatch, database):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(plugins, "get_plugin_config", lambda *a, **k: dict(CONFIG))
    monkeypatch.setattr(plugins, "get_toggle_state", lambda name: "enabled")
    monkeypatch.setattr(records, "refresh_inventory", lambda actor: ("started", 1759478500.0))
    events, shown = [], []
    monkeypatch.setattr(change_store, "audit", SimpleNamespace(emit=lambda event, **kw: events.append((event, kw))))
    monkeypatch.setattr(card, "show", lambda agent, record, kvps: shown.append((record.id, kvps)))
    fake = Fake(monkeypatch)
    fake.events, fake.cards = events, shown
    with user_scope.user_scope("asha"):
        yield fake


def inventory(managed=()):
    """One pull: a VNet with a subnet, an NSG, a key vault (a type adoption does not take); `managed` are listed as the product's."""
    now = dt.datetime.now(dt.timezone.utc)
    acct = store.account("Tenant-1", "Contoso")
    store.apply_containers(acct, [{"id": SUB, "name": "Prod", "state": "Enabled"}], t.groups(SUB, ("rg-test", "rg-other")), now)
    subnet = t.res("snet-app", "microsoft.network/virtualnetworks/subnets", "rg-test", SUB, "networking", native_id=SUBNET, tags={})
    shaped = [t.res("vnet-a", "microsoft.network/virtualnetworks", "rg-test", SUB, "networking", tags={"env": "prod"}, children=[subnet]),
              t.res("nsg-a", "microsoft.network/networksecuritygroups", "rg-test", SUB, "networking"),
              t.res("kv-a", "microsoft.keyvault/vaults", "rg-test", SUB, "identity_security"),
              t.res("other", "microsoft.network/virtualnetworks", "rg-other", SUB, "networking")]
    t.pull(acct, SUB, shaped, now)
    if managed:
        made = ChangeRecord(id="chg-made", requested_by="asha", kind="azure_change", playbook_name="nsg_rule", limit=f"{SUB}/rg-test", status=APPLIED)
        assert records.record_managed(made, list(managed)) == len(managed)


def run(cls, number=0, **args) -> str:
    agent = SimpleNamespace(context=SimpleNamespace(id="chat1"), number=number)
    return asyncio.run(cls(agent=agent, name="t", method=None, args=args, message="", loop_data=None).execute()).message


def decide(record, decision="approve", **body):
    return asyncio.run(AzureApprove(None, None).process({"change_id": record.id, "decision": decision, "expected_hash": record.current_hash, **body}, None))


def offered(world, **over):
    message = run(AzureChange, **{**ADOPT, **over})
    return change_store.load(world.cards[-1][0]), message


# --- what is offered ------------------------------------------------------------------------------------------------

def test_the_resources_come_from_the_stored_inventory_and_the_executor_is_asked_to_import_them(world):
    inventory()
    world.plan_answer = adopt_answer(3)
    record, message = offered(world)
    plan = next(c for c in world.calls if c[1] == "/v1/plan")
    assert plan[2]["mode"] == "import" and plan[3] == "REZZTOR_CLOUD_JOB_TOKEN"
    assert plan[2]["workspace"] == {"subscription": SUB, "resource_group": "rg-test"}
    sent = plan[2]["source"]["adopt"]
    assert sorted(i["id"] for i in sent) == sorted([NSG, VNET, SUBNET])
    vnet = next(i for i in sent if i["id"] == VNET)
    assert vnet == {"id": VNET, "name": "vnet-a", "location": "uaenorth", "tags": {"env": "prod"}, "parent": ""}
    assert next(i for i in sent if i["id"] == SUBNET)["parent"] == "vnet-a"
    assert VAULT not in str(sent) and "other" not in str(sent)  # an unadoptable type and another group are never sent
    assert "Left out, type that cannot be adopted: kv-a" in message


def test_the_record_is_state_only_no_commit_no_reference_no_document_and_the_card_says_importing_n_changes_0(world):
    inventory()
    world.plan_answer = adopt_answer(3)
    record, message = offered(world)
    assert (record.kind, record.playbook_name, record.status, record.plan_digest, record.tier) == ("azure_change", "adopt", AWAITING_APPROVAL, DIGEST, "change-low")
    assert record.commit == "" and record.reference == "" and record.documents == [] and record.approved_by == ""
    kvps = world.cards[-1][1]
    assert kvps["endpoint"] == "plugins/_ea_azure_change/azure_approve" and "reference" not in kvps and "documents" not in kvps
    assert "Imports 3, adds 0, changes 0, removes 0" in kvps["consequence"] and "Azure is not touched" in kvps["consequence"]
    assert "Stop here" in message and "no CR or SR number is needed" in message and "importing 3, changes 0" in message
    assert ("azure.change.drafted" in [e for e, _ in world.events]) and not [e for e, _ in world.events if "document" in e]


def test_named_resources_are_the_only_ones_adopted_and_a_wrong_or_ambiguous_name_is_refused(world):
    inventory()
    world.plan_answer = adopt_answer(1)
    offered(world, resources=["nsg-a"])
    assert [i["id"] for i in next(c for c in world.calls if c[1] == "/v1/plan")[2]["source"]["adopt"]] == [NSG]
    n = len(world.cards)
    assert "not a live resource" in run(AzureChange, **ADOPT, resources="nope")
    assert "not a live resource" in run(AzureChange, **ADOPT, resources=["other"])  # a resource of another group is not this group's
    assert len(world.cards) == n


def test_what_the_product_already_manages_is_left_out_and_nothing_adoptable_means_no_call_and_no_card(world):
    inventory(managed=[NSG])
    world.plan_answer = adopt_answer(2)
    _, message = offered(world)
    sent = next(c for c in world.calls if c[1] == "/v1/plan")[2]["source"]["adopt"]
    assert NSG not in [i["id"] for i in sent] and "Left out, already managed by the product: nsg-a" in message
    world.calls.clear()
    inventory(managed=[NSG, VNET, SUBNET])
    message = run(AzureChange, **ADOPT)
    assert "Nothing in `rg-test` can be adopted" in message and world.calls == []


def test_a_bad_scope_an_empty_group_and_a_missing_inventory_are_said_plainly(world):
    assert "GUID" in run(AzureChange, **{**ADOPT, "subscription": "sub"})
    assert "stored inventory has no live resources" in run(AzureChange, **ADOPT)
    inventory()
    assert "stored inventory has no live resources" in run(AzureChange, **{**ADOPT, "resource_group": "rg-none"})
    assert world.calls == [] and world.cards == []


def test_too_many_resources_for_one_change_ask_for_a_part_at_a_time(world, monkeypatch):
    inventory()
    monkeypatch.setitem(records.RAIL_DEFAULTS, "max_resources", 2)
    assert "more than the limit of 2" in run(AzureChange, **ADOPT)
    assert world.calls == [] and world.cards == []


# --- a plan that changes anything makes no card -----------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    {"ok": False, "stage": "policy", "refusal": "Adoption must import 3 and change 0; this plan does not"},
    {**adopt_answer(3), "summary": {**adopt_answer(3)["summary"], "add": 1}},
    {**adopt_answer(3), "summary": {**adopt_answer(3)["summary"], "change": 1}},
    adopt_answer(2),
    {**adopt_answer(3), "plan_digest": ""},
    {**adopt_answer(3), "checks": [{"label": "Plan policy", "ok": False, "detail": "nope"}]},
    {**adopt_answer(3), "summary": {**adopt_answer(3)["summary"], "resources": [resource("azapi_resource.r1", "create"), *adopt_answer(3)["summary"]["resources"][1:]]}},
    {**adopt_answer(3), "summary": {**adopt_answer(3)["summary"], "resources": [resource("azapi_resource.r1", "delete"), *adopt_answer(3)["summary"]["resources"][1:]]}},
])
def test_a_plan_that_is_not_importing_n_changes_0_makes_no_record_and_no_card(world, answer):
    inventory()
    world.plan_answer = answer
    message = run(AzureChange, **ADOPT)
    assert "no card" in message.lower() and world.cards == [] and change_store.list_records() == []


def test_the_card_failing_to_show_cancels_the_record(world, monkeypatch):
    inventory()
    world.plan_answer = adopt_answer(3)
    monkeypatch.setattr(card, "show", lambda *a: (_ for _ in ()).throw(RuntimeError("no chat")))
    message = run(AzureChange, **ADOPT)
    (record,) = change_store.list_records()
    assert record.status == CANCELLED and "cancelled" in message


# --- the decision ------------------------------------------------------------------------------------------------------

def test_approving_needs_no_number_no_document_writes_the_state_and_lists_the_resources_as_managed(world):
    inventory()
    world.plan_answer = adopt_answer(3)
    record, _ = offered(world)
    world.adopt_answer = {"ok": True, "plan_digest": DIGEST, "adopted": [i["id"] for i in record.variables["adopt"]]}
    answer = decide(record)  # no reference, no download
    assert answer["ok"] and answer["change"]["status"] == APPLIED and answer["change"]["reference"] == ""
    call = next(c for c in world.calls if c[1] == "/v1/adopt/apply")
    assert call[2]["plan_digest"] == DIGEST and call[2]["change_id"] == record.id and call[2]["source"] == {"adopt": record.variables["adopt"]}
    assert "reference" not in call[2] and call[3] == "REZZTOR_CLOUD_JOB_TOKEN"
    done = change_store.load(record.id)
    assert done.status == APPLIED and done.approved_by == "asha" and done.reference == "" and done.applied_at
    assert adopt._managed() == {i["id"] for i in record.variables["adopt"]}
    events = [e for e, _ in world.events]
    assert "azure.change.approved" in events and "azure.adopt.applied" in events and "/v1/apply" not in world.paths()
    assert [i for i in events if "document" in i] == []


def test_rejecting_needs_nothing_and_a_wrong_hash_is_refused(world):
    inventory()
    world.plan_answer = adopt_answer(3)
    record, _ = offered(world)
    assert "no longer what would be adopted" in asyncio.run(AzureApprove(None, None).process(
        {"change_id": record.id, "decision": "approve", "expected_hash": "0" * 64}, None))["error"]
    assert change_store.load(record.id).status == AWAITING_APPROVAL and "/v1/adopt/apply" not in world.paths()
    assert decide(record, "reject")["change"]["status"] == REJECTED and adopt._managed() == set()


def test_only_the_requester_can_decide_and_only_while_it_awaits_approval(world):
    inventory()
    world.plan_answer = adopt_answer(3)
    record, _ = offered(world)
    with user_scope.user_scope("someone-else"):
        assert "Only the engineer who requested" in decide(record)["error"]
    assert decide(record)["ok"]
    assert "not awaiting approval" in decide(change_store.load(record.id))["error"]


@pytest.mark.parametrize("answer, status", [
    ({"ok": False, "stage": "policy", "superseded": True, "refusal": "Azure is not as it was"}, SUPERSEDED),
    ({"ok": False, "stage": "plan", "refusal": "adopting into the state failed"}, FAILED),
])
def test_when_the_executor_refuses_nothing_is_listed_as_managed(world, answer, status):
    inventory()
    world.plan_answer = adopt_answer(3)
    record, _ = offered(world)
    world.adopt_answer = answer
    result = decide(record)
    assert result["ok"] is False and "Nothing was adopted" in result["error"]
    assert change_store.load(record.id).status == status and adopt._managed() == set()


def test_an_executor_that_cannot_be_reached_fails_the_record_and_lists_nothing(world, monkeypatch):
    inventory()
    world.plan_answer = adopt_answer(3)
    record, _ = offered(world)
    monkeypatch.setattr(world, "_send", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    from plugins._ea_azure_change.helpers import executor_client

    monkeypatch.setattr(executor_client, "_send", lambda *a, **k: (_ for _ in ()).throw(executor_client.ExecutorUnreachable("down")))
    result = decide(record)
    assert result["ok"] is False and change_store.load(record.id).status == FAILED and adopt._managed() == set()


# --- an adoption can never run as a change ------------------------------------------------------------------------------

def test_an_adoption_record_can_never_be_applied_even_when_forced_to_approved(world):
    inventory()
    world.plan_answer = adopt_answer(3)
    record, _ = offered(world)
    assert change_store.approve(record, "asha")
    assert conditions.first_failure(record, "asha")  # no commit, no reference, no document: the first condition refuses
    assert "reviewed template" in conditions.first_failure(record, "asha")
    with pytest.raises(RepairableException):
        asyncio.run(AzureChangeGate(agent=SimpleNamespace(number=0)).execute(tool_name="azure_apply", tool_args={"action": "apply", "change_id": record.id}))
    run(AzureApply, action="apply", change_id=record.id)
    assert "/v1/apply" not in world.paths() and "/v1/adopt/apply" not in world.paths()


def test_an_adoption_cannot_be_resumed_it_is_offered_again(world):
    inventory()
    world.plan_answer = adopt_answer(3)
    record, _ = offered(world)
    assert "call adopt again" in run(AzureChange, action="resume", change_id=record.id)


def test_the_plugin_sends_adoption_through_the_executor_client_only_and_has_no_removal_in_it():
    from pathlib import Path

    text = Path(adopt.__file__).read_text(encoding="utf-8")
    assert "http.client" not in text and "requests" not in text and "urllib" not in text
    assert "destroy" not in text.lower() and "-replace" not in text and "executor_client.apply(" not in text
