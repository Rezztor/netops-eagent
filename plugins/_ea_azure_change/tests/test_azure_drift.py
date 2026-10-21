"""The drift report through the change plugin: what the agent can list, the accept card (state only, no number, no document),
the approval that writes the executor's state and marks the lines seen, the draft that is refused until then, and the proof
that nothing here can put anything back or run a change. Stand-in executor, stored inventory in PostgreSQL, no Azure."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

from helpers import files, plugins, user_scope
from helpers.errors import RepairableException

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.tests import register
from plugins._ea_ansible.helpers.change_model import APPLIED, APPROVED, AWAITING_APPROVAL, CLOSED, FAILED, REJECTED, SUPERSEDED, ChangeRecord
from plugins._ea_azure.helpers import store
from plugins._ea_azure.tests import azure_read_tenant as t
from plugins._ea_azure_change.api.azure_approve import AzureApprove
from plugins._ea_azure_change.extensions.python.tool_execute_before._95_azure_change_gate import AzureChangeGate
from plugins._ea_azure_change.helpers import card, conditions, records
from plugins._ea_azure_change.tests.fake_executor import SUB, FakeExecutor, plan_answer
from plugins._ea_azure_change.tools.azure_apply import AzureApply
from plugins._ea_azure_change.tools.azure_change import AzureChange

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

CONFIG = {"executor_url": "unix:///run/cloud-socket/cloud.sock", "timeout_seconds": 5, "apply_wait_seconds": 5, "guardrails": {}}
VALUES = dict(name="vnet-own", region="uaenorth", address_space=[{"cidr": "10.20.0.0/16"}], tags={"env": "test", "owner": "netops", "costcentre": "1"})
DRAFT = dict(action="draft", template="vnet", subscription=SUB, resource_group="rg-test", purpose="Network for the test environment.", **VALUES)
VNET = f"/subscriptions/{SUB}/resourcegroups/rg-test/providers/microsoft.network/virtualnetworks/vnet-own"
REFRESH = "d" * 64
LINES = ["~ azurerm_virtual_network.vnet", "    - tags.env: test", "    + tags.env: prod"]


def now():
    return dt.datetime.now(dt.timezone.utc)


def vnet_row(tags):
    return t.res("vnet-own", "microsoft.network/virtualnetworks", "rg-test", SUB, "networking", tags=tags)


class Fake(FakeExecutor):
    """The stand-in plus the one call this phase adds."""

    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.accept_answer = {"ok": True, "plan_digest": REFRESH, "accepted": [VNET]}

    def _send(self, agent, method, path, body=None, *, key="", timeout=None):
        if path == "/v1/refresh/accept":
            self.calls.append((method, path, body, key))
            return 200, self.accept_answer
        return super()._send(agent, method, path, body, key=key, timeout=timeout)

    def refresh_answer(self, drifted=1):
        self.plan_answer = {**plan_answer(REFRESH, resources=[]), "drift": LINES if drifted else [],
                            "drifted": [{"address": "azurerm_virtual_network.vnet", "type": "azurerm_virtual_network", "native_id": VNET,
                                         "action": "update", "changed": {"tags.env": ["test", "prod"]}}] * drifted}


@pytest.fixture
def world(tmp_path, monkeypatch, database):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(plugins, "get_plugin_config", lambda *a, **k: dict(CONFIG))
    monkeypatch.setattr(plugins, "get_toggle_state", lambda name: "enabled")
    monkeypatch.setattr(records, "advice_for", lambda *a, **k: [])
    monkeypatch.setattr(records, "refresh_inventory", lambda actor: ("started", 1759478500.0))
    events, shown = [], []
    monkeypatch.setattr(change_store, "audit", SimpleNamespace(emit=lambda event, **kw: events.append((event, kw))))
    monkeypatch.setattr(card, "show", lambda agent, record, kvps: shown.append((record.id, kvps)))
    fake = Fake(monkeypatch)
    fake.events, fake.cards = events, shown
    with user_scope.user_scope("asha"):
        yield fake


def inventory(drifted=True):
    """A pull, the apply that made the VNet, the pull it asked for, then (drifted) somebody changing a tag in the portal."""
    acct = store.account("Tenant-1", "Contoso")
    store.apply_containers(acct, [{"id": SUB, "name": "Prod", "state": "Enabled"}], t.groups(SUB, ("rg-test", "rg-other")), now())
    t.pull(acct, SUB, [vnet_row({"env": "test"})], now())
    made = ChangeRecord(id="chg-made", requested_by="asha", kind="azure_change", playbook_name="vnet", variables=VALUES, limit=f"{SUB}/rg-test",
                        status=APPLIED, commit="c" * 40)
    change_store.save(made)
    assert records.record_managed(made, [VNET]) == 1
    t.pull(acct, SUB, [vnet_row({"env": "test"})], now())
    if drifted:
        t.pull(acct, SUB, [vnet_row({"env": "prod"})], now())
    return acct


def run(cls, **args) -> str:
    agent = SimpleNamespace(context=SimpleNamespace(id="chat1"), number=0)
    return asyncio.run(cls(agent=agent, name="t", method=None, args=args, message="", loop_data=None).execute()).message


def decide(record, decision="approve", **body):
    return asyncio.run(AzureApprove(None, None).process({"change_id": record.id, "decision": decision, "expected_hash": record.current_hash, **body}, None))


def offered(world, **args):
    world.refresh_answer()
    message = run(AzureChange, action="drift_accept", resource="vnet-own", **args)
    return change_store.load(world.cards[-1][0]), message


# --- the report -----------------------------------------------------------------------------------------------------

def test_the_report_lists_what_changed_outside_the_product_from_the_inventory_alone_and_says_nothing_is_put_back(world):
    inventory()
    message = run(AzureChange, action="drift")
    assert "vnet-own" in message and "tags.env was test, now prod" in message and "Nothing is put back" in message and "drift_accept" in message
    assert world.calls == []  # the stored inventory only: no executor, no Azure
    assert "No resource the product made has changed" in run(AzureChange, action="drift", resource_group="rg-other")


def test_nothing_drifted_before_anything_is_applied_or_after_an_explained_change(world):
    assert "No Azure inventory" in run(AzureChange, action="drift")
    inventory(drifted=False)
    assert "No resource the product made has changed" in run(AzureChange, action="drift")


# --- the draft guard ------------------------------------------------------------------------------------------------

def test_a_draft_in_a_workspace_with_unaccepted_drift_is_refused_with_no_record_and_no_card_until_it_is_accepted(world):
    inventory()
    message = run(AzureChange, **DRAFT)
    assert "changed in Azure outside the product" in message and "vnet-own" in message and "no card" in message.lower()
    assert world.cards == [] and [r.id for r in change_store.list_records()] == ["chg-made"]
    run(AzureChange, **{**DRAFT, "resource_group": "rg-other"})  # another workspace is not blocked
    assert len(world.cards) == 1
    record, _ = offered(world)
    assert decide(record)["ok"]
    world.plan_answer = plan_answer()
    run(AzureChange, **DRAFT)
    assert len(world.cards) == 3  # drafted now


def test_the_guard_covers_resume_and_edit_because_they_go_through_the_same_door(world):
    from plugins._ea_azure_change.api.azure_edit import AzureEdit

    record = change_store.save(ChangeRecord(requested_by="asha", status=AWAITING_APPROVAL, kind="azure_change", playbook_name="vnet", limit=f"{SUB}/rg-test",
                                            variables=VALUES, commit="c" * 40, plan_digest="e" * 64, expires_at=9e12))
    inventory()
    assert "changed in Azure outside" in run(AzureChange, action="resume", change_id=record.id)
    answer = asyncio.run(AzureEdit(None, None).process({"change_id": record.id, "variables": {**VALUES, "name": "vnet-x"}}, None))
    assert not answer["ok"] and "changed in Azure outside" in answer["error"]
    assert change_store.load(record.id).status == AWAITING_APPROVAL


def test_resource_problem_is_the_one_line_a_simple_action_needs(world):
    inventory()
    from plugins._ea_azure_change.helpers import drift

    assert "vnet-own" in drift.resource_problem(VNET.upper()) and drift.resource_problem("/subscriptions/x/other") is None


# --- the accept card ------------------------------------------------------------------------------------------------

def test_drift_accept_asks_the_executor_for_a_refresh_only_plan_and_shows_a_card_with_no_number_and_no_document(world):
    inventory()
    record, message = offered(world)
    body = next(c for c in world.calls if c[1] == "/v1/plan")[2]
    assert body["mode"] == "refresh_only" and body["source"] == {"template": "vnet", "variables": VALUES} and body["workspace"] == {"subscription": SUB, "resource_group": "rg-test"}
    assert (record.kind, record.playbook_name, record.status, record.plan_digest, record.tier) == ("azure_change", "drift_accept", AWAITING_APPROVAL, REFRESH, "change-low")
    assert record.reference == "" and record.documents == [] and record.commit == "" and record.rollback_commands == []
    assert record.variables["through"] == {VNET: store.drifted()[0]["through"]} and record.preflight["state_drift"] == 1
    assert LINES[0] in record.commands and any("vnet-own: " in c and "tags.env was test, now prod" in c for c in record.commands)
    kvps = world.cards[-1][1]
    assert kvps["endpoint"] == "plugins/_ea_azure_change/azure_approve" and "reference" not in kvps and "documents" not in kvps and "edit_endpoint" not in kvps
    assert "adds 0, changes 0 and removes 0" in message.lower() and "cannot approve" in message and "Nothing is put back" in message
    assert [p for p in world.paths()] == ["/v1/plan"]  # reading only; nothing was accepted or applied by drafting it
    assert "azure.change.drafted" in [e for e, _ in world.events]


def test_drift_accept_refuses_what_has_not_drifted_a_stranger_an_old_executor_and_a_failed_refresh_and_shows_no_card(world):
    inventory()
    assert "needs `resource`" in run(AzureChange, action="drift_accept")
    assert "has not drifted" in run(AzureChange, action="drift_accept", resource="nope")
    world.refresh_answer()
    world.plan_answer = {**plan_answer(), "drift": []}  # an executor that answers a refresh-only plan like any plan: no `drifted`
    assert "does not support refresh-only" in run(AzureChange, action="drift_accept", resource="vnet-own")
    world.plan_answer = {"ok": False, "stage": "plan", "refusal": "azure is unreachable"}
    assert "azure is unreachable" in run(AzureChange, action="drift_accept", resource="vnet-own")
    world.refresh_answer()
    world.plan_answer["summary"]["change"] = 1  # a "refresh-only" plan that lists a change is refused
    assert "must change nothing" in run(AzureChange, action="drift_accept", resource="vnet-own")
    assert world.cards == []


def test_a_resource_made_by_an_unknown_change_cannot_be_accepted_because_there_is_no_state_to_name(world):
    inventory()
    made = change_store.load("chg-made")
    made.kind = "azure_action"  # an action makes nothing, so there is no state that holds the resource
    change_store.save(made)
    assert "not on record as a template change" in run(AzureChange, action="drift_accept", resource="vnet-own") and world.cards == []


# --- the decision ---------------------------------------------------------------------------------------------------

def test_approving_needs_no_number_and_no_download_writes_the_state_with_the_approved_digest_and_marks_the_lines_accepted(world):
    inventory()
    record, _ = offered(world)
    assert store.drifted()
    answer = decide(record)
    assert answer["ok"] and answer["change"]["reference"] == "" and answer["change"]["approved_by"] == "asha"
    done = change_store.load(record.id)
    assert (done.status, done.reference, done.approved_by) == (CLOSED, "", "asha") and done.approved_hash
    call = next(c for c in world.calls if c[1] == "/v1/refresh/accept")
    assert call[2] == {"source": {"template": "vnet", "variables": VALUES}, "workspace": {"subscription": SUB, "resource_group": "rg-test"},
                       "plan_digest": REFRESH, "change_id": record.id, "actor": "asha"} and call[3] == "REZZTOR_CLOUD_JOB_TOKEN"
    assert store.drifted() == [] and store.search({"attention": "drifted"})[1] == 0
    events = [e for e, _ in world.events]
    assert "azure.change.approved" in events and "azure.drift.accepted" in events and "azure.change.closed" in events
    assert "/v1/apply" not in world.paths()
    assert "not awaiting approval" in decide(done)["error"]


def test_a_new_change_after_the_acceptance_is_drift_again_and_only_that_one(world):
    acct = inventory()
    record, _ = offered(world)
    assert decide(record)["ok"] and store.drifted() == []
    t.pull(acct, SUB, [vnet_row({"env": "other"})], now())
    [again] = store.drifted()
    assert again["lines"][-1].endswith("tags.env was prod, now other") and len(again["lines"]) == 1


def test_a_drift_only_the_inventory_saw_is_accepted_without_asking_the_executor_to_write_a_state(world):
    inventory()
    world.refresh_answer(drifted=0)  # the executor sees no attribute drift (a power state, a tag the template does not hold)
    run(AzureChange, action="drift_accept", resource="vnet-own")
    record = change_store.load(world.cards[-1][0])
    assert record.preflight["state_drift"] == 0 and decide(record)["ok"]
    assert "/v1/refresh/accept" not in world.paths() and store.drifted() == []


def test_reject_leaves_the_drift_alone_and_needs_no_hash(world):
    inventory()
    record, _ = offered(world)
    assert decide(record, "reject", expected_hash="")["ok"] and change_store.load(record.id).status == REJECTED
    assert store.drifted() and "/v1/refresh/accept" not in world.paths()


def test_a_wrong_hash_another_user_and_an_expired_card_accept_nothing(world):
    inventory()
    record, _ = offered(world)
    assert "no longer what would be accepted" in decide(record, expected_hash="0" * 64)["error"]
    with user_scope.user_scope("bilal"):
        assert "Only the engineer who requested" in decide(record)["error"]
    record.expires_at = 1.0
    change_store.save(record)
    assert "expired" in decide(record)["error"]
    assert store.drifted() and "/v1/refresh/accept" not in world.paths()


def test_when_azure_moved_since_the_card_the_executor_supersedes_it_and_nothing_is_marked_accepted(world):
    inventory()
    record, _ = offered(world)
    world.accept_answer = {"ok": False, "stage": "policy", "superseded": True, "refusal": "Azure is not as it was when the drift was shown, so nothing was accepted."}
    answer = decide(record)
    assert not answer["ok"] and "nothing was accepted" in answer["error"].lower()
    assert change_store.load(record.id).status == SUPERSEDED and store.drifted()


def test_an_executor_that_fails_or_is_gone_fails_the_acceptance_and_marks_nothing(world, monkeypatch):
    inventory()
    record, _ = offered(world)
    world.accept_answer = {"ok": False, "stage": "plan", "refusal": "accepting the state failed: boom"}
    assert not decide(record)["ok"] and change_store.load(record.id).status == FAILED and store.drifted()
    other, _ = offered(world)
    monkeypatch.setattr(records.executor_client, "_send", lambda *a, **k: (_ for _ in ()).throw(records.executor_client.ExecutorUnreachable("down")))
    assert "down" in decide(other)["error"] and change_store.load(other.id).status == FAILED and store.drifted()


# --- an acceptance is not a change and cannot become one ----------------------------------------------------------------

def test_an_acceptance_can_never_be_applied_by_the_tool_the_conditions_or_the_gate(world):
    inventory()
    record, _ = offered(world)
    assert "Only an approved change" in run(AzureApply, change_id=record.id)
    forced = change_store.load(record.id)
    forced.status, forced.approved_by, forced.approved_hash = APPROVED, "asha", forced.current_hash  # even if something set it approved
    register.force(forced)
    assert conditions.first_failure(forced, "asha")  # no commit, no number, no document
    with pytest.raises(RepairableException):
        asyncio.run(AzureChangeGate(agent=SimpleNamespace(number=0)).execute(tool_name="azure_apply", tool_args={"change_id": forced.id}))
    run(AzureApply, change_id=forced.id)
    assert "/v1/apply" not in world.paths()


def test_resume_refuses_an_acceptance_and_nothing_here_can_put_anything_back(world):
    inventory()
    record, _ = offered(world)
    assert "drift_accept again" in run(AzureChange, action="resume", change_id=record.id)
    assert set(world.paths()) <= {"/v1/plan", "/healthz"}
    assert "Unknown action" in run(AzureChange, action="put_back", resource="vnet-own") and "drift_accept" in run(AzureChange, action="revert")
    assert [c[2]["mode"] for c in world.calls if c[1] == "/v1/plan"] == ["refresh_only"]


def test_an_acceptance_cannot_be_edited_it_has_no_values(world):
    from plugins._ea_azure_change.api.azure_edit import AzureEdit

    inventory()
    record, _ = offered(world)
    before = list(world.calls)
    answer = asyncio.run(AzureEdit(None, None).process({"change_id": record.id, "variables": VALUES}, None))
    assert not answer["ok"] and world.calls == before
