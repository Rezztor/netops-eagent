"""Adoption: the code made from inventory rows, the "importing N, changes 0" rule, and writing the imports into the state.
The fixture plan is written from OpenTofu's documented JSON format (its `_comment` cites the page), not recorded from a run.
Same stand-in `tofu` as the runner's tests; no network, no Azure."""
import copy
import json

import pytest

from cloud_executor import adopt, gate, rails, runner, templates
from cloud_executor.tests.support import HERE, RAILS, RESOURCE_GROUP, SUB, plan_fixture

PLAN = json.loads((HERE / "fixtures" / "adopt_import.json").read_text())
GROUP = "rg-payments-prod-uaen"
VNET = f"/subscriptions/{SUB}/resourceGroups/{GROUP}/providers/Microsoft.Network/virtualNetworks/vnet-a"
SUBNET = f"{VNET}/subnets/snet-app"


def rails_():
    return rails.validate({**RAILS, "resource_groups": {"existing": [GROUP], "new_prefix": "rg-"}, "resource_types": []}, [])


def items(**tags):
    return [{"id": VNET.lower(), "name": "vnet-a", "location": "uaenorth", "tags": tags or {"env": "prod", "owner": "netops"}},
            {"id": SUBNET.lower(), "name": "snet-app", "parent": "vnet-a"}]


def body(source=None, **extra):
    return {"mode": "import", "source": {"adopt": items()} if source is None else source, "workspace": RESOURCE_GROUP,
            "change_id": "adopt-1", "actor": "alice", **extra}


def asked(**extra):
    return adopt.plan(body(**extra), rails_())


def with_actions(actions, index=0):
    plan = copy.deepcopy(PLAN)
    plan["resource_changes"][index]["change"]["actions"] = actions
    return plan


def test_the_code_is_made_from_validated_values_and_passes_the_static_gate():
    src, ids = adopt.resolve({"adopt": items()}, SUB, GROUP, rails_())
    code = src.files["main.tf"]
    assert ids == [VNET.lower(), SUBNET.lower()] and gate.check_code(src.files) == []
    assert f'id = "{VNET}?api-version=2024-05-01"' in code.replace("  ", " ") or f'"{VNET}?api-version=2024-05-01"' in code
    assert '"Microsoft.Network/virtualNetworks/subnets@2024-05-01"' in code and f'parent_id = "{VNET}"' in code
    assert 'name      = "snet-app"' in code and "location" not in code.split("azapi_resource\" \"r2\"")[1]  # a subnet has none
    assert src.draft["may_change"] == [] and src.state_key.startswith(f"{SUB}/{GROUP}/adopt/")


def test_a_tag_value_cannot_become_code():
    nasty = {"note": 'x"} resource "azapi_resource" "evil" {', "tpl": "${var.x} %{if true}"}
    src, _ = adopt.resolve({"adopt": items(**nasty)}, SUB, GROUP, rails_())
    code = src.files["main.tf"]
    assert code.count("\nresource \"") == 2 and "$${var.x}" in code and "%%{if" in code and '\\"} resource' in code
    with pytest.raises(runner.Refusal, match="file"):  # the static gate reads strings too, so a tag that spells a denied call is refused
        adopt.resolve({"adopt": items(tpl='${file("/etc/passwd")}')}, SUB, GROUP, rails_())


@pytest.mark.parametrize("change, why", [
    (lambda i: i[0].update(id=i[0]["id"].replace(SUB, "99999999-2222-3333-4444-555555555555")), "workspace"),
    (lambda i: i[0].update(id=i[0]["id"].replace("virtualnetworks", "virtualnetworks/x/extensions")), "adopted"),
    (lambda i: i[0].update(id=i[0]["id"].replace("microsoft.network/virtualnetworks", "microsoft.keyvault/vaults")), "adopted"),
    (lambda i: i[0].update(name="vnet-b"), "name"),
    (lambda i: i[0].update(name='vnet-a"'), "name"),
    (lambda i: i[0].update(location="UAE North; rm"), "location"),
    (lambda i: i[0].update(tags={"k": 1}), "tags"),
    (lambda i: i[0].update(tags={f"k{n}": "v" for n in range(51)}), "tags"),
    (lambda i: i.append(dict(i[0])), "twice"),
    (lambda i: i.__setitem__(0, "not an object"), "object"),
])
def test_a_resource_that_cannot_be_adopted_is_refused_before_any_plan(cloud, change, why):
    listed = items()
    change(listed)
    answer = adopt.plan(body({"adopt": listed}), rails_())
    assert answer["ok"] is False and answer["stage"] == "gate" and why in answer["refusal"] and cloud.calls() == []


@pytest.mark.parametrize("source", [{"adopt": []}, {"adopt": "x"}, {"template": "vnet", "variables": {}}, {"code": "x"},
                                    {"adopt": items(), "code": "resource x {}"}])
def test_only_a_list_of_resources_to_adopt_is_a_source(cloud, source):
    answer = adopt.plan(body(source), rails_())
    assert answer["ok"] is False and answer["stage"] == "gate" and cloud.calls() == []


def test_the_subscription_must_be_one_the_guard_rails_allow(cloud):
    allowed = {**rails_(), "subscriptions": []}
    assert adopt.plan(body(), allowed)["refusal"].startswith("The subscription")


@pytest.mark.parametrize("change", [{"mode": "apply"}, {"mode": "refresh_only"}, {"mode": "destroy"}])
def test_only_an_import_request_is_planned(cloud, change):
    answer = asked(**change)
    assert answer["ok"] is False and answer["stage"] == "gate" and cloud.calls() == []


def test_importing_two_and_changing_none_is_a_plan_the_planner_makes_and_leaves_no_file(cloud):
    cloud.script(plan=PLAN)
    answer = asked()
    assert answer["ok"], answer
    assert answer["summary"]["import"] == 2 and answer["summary"]["add"] == 0 and answer["summary"]["change"] == 0
    assert [r["action"] for r in answer["summary"]["resources"]] == ["import", "import"]
    assert answer["summary"]["lines"][0].startswith("= azapi_resource.r1  import ") and answer["adopting"] == [VNET.lower(), SUBNET.lower()]
    assert answer["cost"]["monthly_usd"] == 0.0 and answer["drift"] == [] and len(answer["plan_digest"]) == 64
    assert {"label": "Importing 2, changes 0", "ok": True, "detail": "every resource is an import with no change"} in answer["checks"]
    calls = cloud.calls()
    assert [c["args"][0] for c in calls] == ["init", "workspace", "validate", "plan", "show"]
    assert calls[0]["env"]["ARM_CLIENT_ID"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # the planner, never the applier
    assert "main.tf" in calls[0]["files"] and list(cloud.runs.iterdir()) == []


@pytest.mark.parametrize("actions", [["update"], ["create"], ["delete"], ["delete", "create"], ["create", "delete"], ["forget"], ["read"]])
def test_a_plan_that_would_add_change_replace_or_delete_anything_is_refused(cloud, actions):
    cloud.script(plan=with_actions(actions))
    answer = asked()
    assert answer["ok"] is False and answer["stage"] == "policy"


def test_an_update_is_refused_with_what_would_change_so_the_engineer_can_see_why(cloud):
    plan = with_actions(["update"])
    plan["resource_changes"][0]["change"]["after"] = {**plan["resource_changes"][0]["change"]["after"], "tags": {"env": "test"}}
    cloud.script(plan=plan)
    answer = asked()
    assert answer["ok"] is False and answer["stage"] == "policy" and "tags" in answer["refusal"]


def test_an_import_that_also_updates_is_refused_even_where_the_policy_allows_the_change(cloud):
    plan = with_actions(["update"])
    plan["resource_changes"][0]["change"]["after"]["tags"] = {"env": "prod", "owner": "netops", "extra": "x"}
    cloud.script(plan=plan)
    answer = asked()
    assert answer["ok"] is False and answer["stage"] == "policy"


def test_what_is_imported_must_be_exactly_what_was_asked_for(cloud):
    extra = copy.deepcopy(PLAN)
    third = copy.deepcopy(PLAN["resource_changes"][0])
    third["address"] = "azapi_resource.r3"
    third["change"]["importing"]["id"] = third["change"]["importing"]["id"].replace("vnet-a", "vnet-z")
    extra["resource_changes"].append(third)
    cloud.script(plan=extra)
    assert asked()["ok"] is False
    short = copy.deepcopy(PLAN)
    short["resource_changes"].pop()
    cloud.script(plan=short)
    answer = asked()
    assert answer["ok"] is False and "Adoption must import 2 and change 0" in answer["refusal"]


def test_a_resource_in_the_plan_that_is_not_an_import_is_refused(cloud):
    plan = copy.deepcopy(PLAN)
    del plan["resource_changes"][1]["change"]["importing"]
    cloud.script(plan=plan)
    assert asked()["ok"] is False


def test_a_resource_group_the_rails_do_not_list_is_refused(cloud):
    cloud.script(plan=PLAN)
    answer = adopt.plan(body(), {**rails_(), "resource_groups": {"existing": [], "new_prefix": "rg-"}})
    assert answer["ok"] is False and answer["stage"] == "policy" and "not one this product may change" in answer["refusal"]


def test_the_digest_is_stable_covers_the_resources_and_is_never_an_apply_digest(cloud):
    cloud.script(plan=PLAN)
    first = asked()["plan_digest"]
    assert first == asked()["plan_digest"]
    again = adopt.plan(body({"adopt": items(env="dev")}), rails_())
    assert again["ok"] and again["plan_digest"] != first
    cloud.script(plan=plan_fixture())
    other = runner.plan({**body(), "mode": "apply", "source": {"template": "resource_group", "variables": {
        "region": "uaenorth", "tags": {"env": "prod", "owner": "netops", "costcentre": "cc-1001"}}}},
                         rails.validate({**RAILS, "resource_types": ["azurerm_resource_group"]}, []), templates.load())
    assert other["ok"] and other["plan_digest"] != first


def test_apply_writes_the_imports_only_for_the_approved_digest_records_it_first_and_runs_as_the_planner(cloud):
    cloud.script(plan=PLAN)
    digest = asked()["plan_digest"]
    done = adopt.apply(body(plan_digest=digest), rails_())
    assert done == {"ok": True, "plan_digest": digest, "adopted": [VNET.lower(), SUBNET.lower()]}
    assert [c["args"][0] for c in cloud.calls()][-2:] == ["show", "apply"] and cloud.calls()[-1]["args"][-1] == "plan.bin"
    assert cloud.applied[0]["reference"] == "" and cloud.applied[0]["plan_digest"] == digest and cloud.applied[0]["change_id"] == "adopt-1"
    assert "import {" in cloud.applied[0]["code"]
    assert cloud.calls()[-1]["env"]["ARM_CLIENT_ID"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_apply_refuses_when_azure_moved_since_it_was_shown_and_applies_nothing(cloud):
    cloud.script(plan=PLAN)
    digest = asked()["plan_digest"]
    cloud.script(plan=with_actions(["update"]))
    done = adopt.apply(body(plan_digest=digest), rails_())
    assert done["ok"] is False and cloud.applied == [] and "apply" not in [c["args"][0] for c in cloud.calls()]
    cloud.script(plan=PLAN)
    moved = adopt.apply(body({"adopt": items(env="dev")}, plan_digest=digest), rails_())
    assert moved["ok"] is False and moved["superseded"] is True and cloud.applied == []
    assert adopt.apply(body(plan_digest="0" * 64), rails_())["superseded"] is True


def test_a_change_id_adopts_once_and_a_failing_apply_is_an_answer_with_no_secret(cloud):
    cloud.script(plan=PLAN)
    digest = asked()["plan_digest"]
    assert adopt.apply(body(plan_digest=digest), rails_())["ok"]
    again = adopt.apply(body(plan_digest=digest), rails_())
    assert again["ok"] is False and "already" in again["refusal"]
    cloud.script(fail={"apply": "boom postgresql://eagent_cloud:db-secret-password@postgres"})
    failed = adopt.apply(body(plan_digest=digest, change_id="adopt-2"), rails_())
    assert failed["ok"] is False and failed["stage"] == "plan" and "db-secret-password" not in failed["refusal"]


def test_the_module_changes_nothing_in_azure_it_cannot_destroy_replace_or_use_the_change_identity():
    text = (HERE.parent / "adopt.py").read_text(encoding="utf-8")
    assert "destroy" not in text.lower() and '"applier"' not in text and "-replace" not in text and "-target" not in text
    assert "-destroy" not in text and "delete(" not in text


def test_the_application_can_use_the_type_table_without_the_executor_runtime():
    assert adopt.describe(SUBNET)["arm_type"] == "Microsoft.Network/virtualNetworks/subnets" and adopt.describe(VNET.lower() + "/x") is None
    assert adopt.describe(f"/subscriptions/{SUB}/resourceGroups/{GROUP}/providers/Microsoft.KeyVault/vaults/kv") is None
    assert not any(gate._type_denied(t) for t in adopt.ARM_TYPES)  # nothing the gate denies is adoptable
