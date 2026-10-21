"""Refresh-only: the drift a plan lists, the digest over it, and accepting it into the state. The fixture plan is written
from OpenTofu's documented JSON format (its `_comment` cites the page), not recorded from a run. Same stand-in `tofu` as
the runner's tests; no network, no Azure."""
import copy
import json

import pytest

from cloud_executor import refresh, runner, templates
from cloud_executor.tests.support import HERE, RAILS, RESOURCE_GROUP, RG_SOURCE, plan_fixture

CATALOGUE = templates.load()
DRIFT = json.loads((HERE / "fixtures" / "refresh_only.json").read_text())


def rails_():
    from cloud_executor import rails

    return rails.validate({**RAILS, "resource_types": ["azurerm_resource_group"]}, [])


def body(**extra):
    return {"mode": "refresh_only", "source": RG_SOURCE, "workspace": RESOURCE_GROUP, "change_id": "acc-1", "actor": "alice", **extra}


def asked(**extra):
    return refresh.plan(body(**extra), rails_(), CATALOGUE)


def test_the_drift_is_listed_with_values_masked_and_a_resource_gone_from_azure_is_said_so(cloud):
    cloud.script(plan=DRIFT)
    answer = asked()
    assert answer["ok"], answer
    group, vnet = answer["drifted"]
    assert group["changed"] == {"tags.env": ["prod", "test"]} and group["action"] == "update"
    assert group["native_id"].endswith("/resourcegroups/rg-payments-prod-uaen") and vnet["action"] == "delete"
    assert vnet["changed"]["shared_key"][0] == "(sensitive)"
    assert answer["drift"][:3] == ["~ azurerm_resource_group.this", "    - tags.env: prod", "    + tags.env: test"]
    assert answer["drift"][-1] == "! azurerm_virtual_network.vnet  no longer in Azure"
    assert "hunter2" not in json.dumps(answer)
    assert answer["summary"] == {"add": 0, "change": 0, "import": 0, "lines": [], "resources": []}


def test_it_only_reads_it_refreshes_only_and_leaves_no_plan_file(cloud):
    cloud.script(plan=DRIFT)
    asked()
    calls = cloud.calls()
    assert [c["args"][0] for c in calls] == ["init", "workspace", "validate", "plan", "show"]
    assert "-refresh-only" in calls[3]["args"] and calls[0]["env"]["ARM_CLIENT_ID"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert list(cloud.runs.iterdir()) == []


def test_the_digest_covers_the_drift_so_new_drift_is_a_new_digest_and_it_is_never_an_apply_digest(cloud):
    cloud.script(plan=DRIFT)
    first = asked()["plan_digest"]
    assert first == asked()["plan_digest"]
    moved = copy.deepcopy(DRIFT)
    moved["resource_drift"][0]["change"]["after"]["tags"]["env"] = "dev"
    cloud.script(plan=moved)
    assert asked()["plan_digest"] != first
    cloud.script(plan=plan_fixture())
    apply_digest = runner.plan({**body(), "mode": "apply"}, rails_(), CATALOGUE)["plan_digest"]
    assert apply_digest != first


def test_a_refresh_with_no_drift_says_so_and_still_has_a_digest(cloud):
    quiet = copy.deepcopy(DRIFT)
    quiet["resource_drift"] = []
    cloud.script(plan=quiet)
    answer = asked()
    assert answer["ok"] and answer["drifted"] == [] and answer["drift"] == [] and len(answer["plan_digest"]) == 64


@pytest.mark.parametrize("actions", [["create"], ["update"], ["delete"], ["delete", "create"], ["forget"]])
def test_a_refresh_only_plan_that_would_change_anything_is_refused(cloud, actions):
    bad = copy.deepcopy(DRIFT)
    bad["resource_changes"][0]["change"]["actions"] = actions
    cloud.script(plan=bad)
    answer = asked()
    assert answer["ok"] is False and answer["stage"] == "policy" and "change nothing" in answer["refusal"]


@pytest.mark.parametrize("change", [{"mode": "apply"}, {"mode": "destroy"}, {"source": {"template": "no_such", "variables": {}}},
                                    {"workspace": {"subscription": "x", "resource_group": "y"}}])
def test_only_a_refresh_only_request_the_gate_accepts_is_planned(cloud, change):
    answer = asked(**change)
    assert answer["ok"] is False and answer["stage"] == "gate" and cloud.calls() == []


def test_accept_applies_the_saved_refresh_plan_only_when_the_digest_is_the_approved_one_and_records_it_first(cloud):
    cloud.script(plan=DRIFT)
    digest = asked()["plan_digest"]
    done = refresh.accept(body(plan_digest=digest), rails_(), CATALOGUE)
    assert done["ok"] and done["plan_digest"] == digest
    assert [a.rsplit("/", 1)[-1] for a in done["accepted"]] == ["rg-payments-prod-uaen", "vnet-payments"]  # both ids, lower-cased
    assert [c["args"][0] for c in cloud.calls()][-2:] == ["show", "apply"] and cloud.calls()[-1]["args"][-1] == "plan.bin"
    assert "-refresh-only" in next(c for c in cloud.calls()[::-1] if c["args"][0] == "plan")["args"]
    assert cloud.applied[0]["reference"] == "" and cloud.applied[0]["plan_digest"] == digest and cloud.applied[0]["change_id"] == "acc-1"
    assert cloud.calls()[-1]["env"]["ARM_CLIENT_ID"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # the planner, never the applier


def test_accept_refuses_when_azure_moved_since_the_drift_was_shown_and_applies_nothing(cloud):
    cloud.script(plan=DRIFT)
    digest = asked()["plan_digest"]
    moved = copy.deepcopy(DRIFT)
    moved["resource_drift"][0]["change"]["after"]["tags"]["env"] = "dev"
    cloud.script(plan=moved)
    done = refresh.accept(body(plan_digest=digest), rails_(), CATALOGUE)
    assert done["ok"] is False and done["superseded"] is True and "apply" not in [c["args"][0] for c in cloud.calls()]
    assert cloud.applied == []
    assert refresh.accept(body(plan_digest="0" * 64), rails_(), CATALOGUE)["ok"] is False


def test_a_change_id_is_accepted_once_and_a_failing_apply_is_an_answer_with_no_secret(cloud):
    cloud.script(plan=DRIFT)
    digest = asked()["plan_digest"]
    assert refresh.accept(body(plan_digest=digest), rails_(), CATALOGUE)["ok"]
    again = refresh.accept(body(plan_digest=digest), rails_(), CATALOGUE)
    assert again["ok"] is False and "already" in again["refusal"]
    cloud.script(fail={"apply": "boom postgresql://eagent_cloud:db-secret-password@postgres"})
    failed = refresh.accept(body(plan_digest=digest, change_id="acc-2"), rails_(), CATALOGUE)
    assert failed["ok"] is False and failed["stage"] == "plan" and "db-secret-password" not in failed["refusal"]


def test_the_module_cannot_put_anything_back_it_has_no_apply_of_changes_and_no_second_role():
    text = (HERE.parent / "refresh.py").read_text(encoding="utf-8")
    assert "destroy" not in text.lower() and '"applier"' not in text and "-replace" not in text and "-target" not in text
