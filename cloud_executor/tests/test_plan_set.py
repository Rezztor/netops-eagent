"""`runner.plan_set` and `POST /v1/plan-set`: a change set planned whole, step by step (ADR 0073 to 0075).

The stand-in `tofu` replays one plan per `plan -out` call (`plans`). The plans are written from the documented shape of
`tofu show -json`, not recorded from a run; the tags are the same everywhere so only what a test changes moves a digest.
"""
import copy
import re

import pytest

from cloud_executor import arm, gate, rails, runner, store, templates
from cloud_executor.tests.support import SUB

CATALOGUE = templates.load()
AZURERM = "registry.opentofu.org/hashicorp/azurerm"
TAGS = {"env": "prod", "owner": "netops", "costcentre": "cc-1001"}
NET = f"/subscriptions/{SUB}/resourceGroups"


def good_rails(**over):
    types = ["azurerm_resource_group", "azurerm_virtual_network", "azurerm_subnet", "azurerm_virtual_network_peering",
             "azurerm_public_ip"]
    return rails.validate({"subscriptions": [SUB], "regions": ["uaenorth"], "resource_types": types,
                           "resource_groups": {"existing": [], "new_prefix": "rg-"}, **over}, [])


def create(rtype, name, group=None, actions=("create",), **attrs):
    after = {"name": name, **({"resource_group_name": group} if group else {}), **attrs}
    return {"address": f"{rtype}.{name}", "mode": "managed", "type": rtype, "name": name, "provider_name": AZURERM,
            "change": {"actions": list(actions), "before": None if actions == ("create",) else after, "after": after,
                       "after_unknown": {"id": True}, "before_sensitive": False, "after_sensitive": {}}}


def plan(*changes):
    return {"format_version": "1.2", "resource_changes": list(changes),
            "configuration": {"provider_config": {"azurerm": {"full_name": AZURERM}}}}


# A hub, a spoke and a peering and a subnet in the spoke: six steps, each with the plan it gets.
def group_step(name):
    return ({"template": "resource_group", "resource_group": name, "variables": {"region": "uaenorth", "tags": TAGS}},
            plan(create("azurerm_resource_group", name, location="uaenorth", tags=TAGS)))


def vnet_step(group, name, cidr):
    return ({"template": "vnet", "resource_group": group, "variables": {
        "name": name, "region": "uaenorth", "address_space": [{"cidr": cidr}], "tags": TAGS}},
            plan(create("azurerm_virtual_network", name, group, location="uaenorth", address_space=[cidr], tags=TAGS)))


def peering_step(group, local, remote_group, remote):
    both = [create("azurerm_virtual_network_peering", f"to-{remote}", group, virtual_network_name=local,
                   remote_virtual_network_id=f"{NET}/{remote_group}/providers/Microsoft.Network/virtualNetworks/{remote}"),
            create("azurerm_virtual_network_peering", f"to-{local}", remote_group, virtual_network_name=remote,
                   remote_virtual_network_id=f"{NET}/{group}/providers/Microsoft.Network/virtualNetworks/{local}")]
    return ({"template": "peering", "resource_group": group, "variables": {
        "local_vnet": local, "remote_vnet": remote, "remote_resource_group": remote_group}}, plan(*both))


def subnet_step(group, vnet, name, prefix):
    return ({"template": "subnet", "resource_group": group, "variables": {"vnet_name": vnet, "name": name, "prefix": prefix}},
            plan(create("azurerm_subnet", name, group, virtual_network_name=vnet, address_prefixes=[prefix])))


def hub_and_spoke():
    return [group_step("rg-hub"), group_step("rg-spoke"), vnet_step("rg-hub", "vnet-hub", "10.0.0.0/16"),
            vnet_step("rg-spoke", "vnet-spoke", "10.1.0.0/16"), peering_step("rg-hub", "vnet-hub", "rg-spoke", "vnet-spoke"),
            subnet_step("rg-spoke", "vnet-spoke", "snet-app", "10.1.1.0/24")]


@pytest.fixture
def run(cloud):
    """`run(steps)` plans a set of `(step, plan)` pairs and returns the answer."""
    def go(pairs, rails_=None, lookup=None, **body):
        cloud.script(plans=[None] * saved_plans(cloud) + [p for _, p in pairs])  # the stand-in numbers plans across the test
        return runner.plan_set({"subscription": SUB, "change_id": "set-1", "actor": "alice", **body,
                                "steps": [copy.deepcopy(s) for s, _ in pairs]}, rails_ or good_rails(), CATALOGUE, lookup)
    return go


def saved_plans(cloud):
    return sum(1 for c in cloud.calls() if c["args"][0] == "plan" and any(a.startswith("-out=") for a in c["args"]))


def tfo(cloud, *args):
    return [c["args"][0] for c in cloud.calls() if c["args"][0] in args]


def test_a_six_step_hub_and_spoke_is_accepted_and_every_step_gets_a_real_plan(cloud, run):
    answer = run(hub_and_spoke())
    assert answer["ok"], answer
    assert [s["n"] for s in answer["steps"]] == [1, 2, 3, 4, 5, 6]
    assert all(re.fullmatch(r"[0-9a-f]{64}", s["plan_digest"]) for s in answer["steps"])
    assert len({s["plan_digest"] for s in answer["steps"]}) == 6
    assert answer["set_digest"] == gate.set_digest(SUB, [
        {"template": t, "resource_group": g, "plan_digest": s["plan_digest"]}
        for (t, g), s in zip([("resource_group", "rg-hub"), ("resource_group", "rg-spoke"), ("vnet", "rg-hub"), ("vnet", "rg-spoke"),
                              ("peering", "rg-hub"), ("subnet", "rg-spoke")], answer["steps"])])
    assert [s["summary"]["add"] for s in answer["steps"]] == [1, 1, 1, 1, 2, 1]
    assert answer["cost"]["monthly_usd"] == 0 and answer["cost"]["unknown"] == []
    assert [c["label"] for c in answer["checks"]] == ["Cost of the set", "Cost across approved changes", "Number of resources in the set"]
    assert all(c["ok"] for c in answer["checks"])
    assert tfo(cloud, "plan", "apply") == ["plan"] * 6  # six plans, and nothing was applied
    assert list(cloud.runs.iterdir()) == []  # no plan file survives a step
    assert cloud.applied == []


def test_each_step_digest_is_the_digest_a_single_change_of_it_would_get(cloud, run):
    """The set adds nothing to a step's digest, so the step planned alone (after its parents exist) is the same."""
    pairs = hub_and_spoke()
    answer = run(pairs)
    cloud.script(plans=[None] * saved_plans(cloud) + [pairs[0][1]])
    single = runner.plan({"mode": "apply", "workspace": {"subscription": SUB, "resource_group": "rg-hub"},
                          "source": {"template": "resource_group", "variables": pairs[0][0]["variables"]}, "change_id": "x"},
                         good_rails(), CATALOGUE)
    assert single["ok"] and single["plan_digest"] == answer["steps"][0]["plan_digest"]


def test_the_set_digest_changes_when_two_steps_swap_a_value_changes_or_a_step_is_dropped(run):
    base = run(hub_and_spoke())["set_digest"]
    swapped = hub_and_spoke()
    swapped[0], swapped[1] = swapped[1], swapped[0]
    assert run(swapped)["set_digest"] != base
    changed = hub_and_spoke()
    changed[0][0]["variables"]["tags"] = {**TAGS, "project": "payments"}
    changed[0][1]["resource_changes"][0]["change"]["after"]["tags"] = {**TAGS, "project": "payments"}
    assert run(changed)["set_digest"] != base
    assert run(hub_and_spoke()[:-1])["set_digest"] != base
    assert run(hub_and_spoke())["set_digest"] == base  # and the same set is the same digest


def refused(answer, step, stage=None, text=""):
    assert answer["ok"] is False and answer["step"] == step, answer
    assert stage is None or answer["stage"] == stage
    assert text in answer["refusal"], answer["refusal"]


def test_a_step_that_is_free_code_or_has_anything_but_the_three_fields_is_refused(cloud, run):
    pairs = hub_and_spoke()[:2]
    pairs[1][0].pop("template")
    pairs[1][0]["code"] = 'resource "azurerm_resource_group" "x" {}'
    refused(run(pairs, good_rails(allow_free_code=True)), 2, "gate", "Free code is never a step")
    claim = hub_and_spoke()[:2]
    claim[1][0]["pending_groups"] = ["rg-x"]
    refused(run(claim), 2, "gate", "nothing else")
    assert tfo(cloud, "apply") == [] and cloud.applied == []


def test_one_step_and_thirteen_steps_are_refused_before_anything_runs(cloud, run):
    refused(run(hub_and_spoke()[:1]), 0, "gate", "2 to 12 steps")
    many = [group_step(f"rg-n{i}") for i in range(13)]
    refused(run(many), 0, "gate", "2 to 12 steps")
    assert run(many[:12])["ok"], "twelve is the limit and is accepted"
    refused(runner.plan_set({"subscription": SUB, "steps": "x"}, good_rails(), CATALOGUE), 0, "gate")
    assert cloud.calls() and tfo(cloud, "apply") == []


def test_a_subscription_the_rails_do_not_name_is_refused(run):
    refused(run(hub_and_spoke(), subscription="00000000-0000-0000-0000-000000000000"), 0, "gate", "subscription")
    refused(run(hub_and_spoke(), subscription="not a guid"), 0, "gate", "subscription")


def test_two_steps_with_one_state_key_are_refused_at_the_second(run):
    refused(run([group_step("rg-hub"), group_step("rg-hub")]), 2, "gate", "same thing as step 1")


def test_two_steps_that_make_the_same_thing_are_refused_at_the_second(run):
    first, second = vnet_step("rg-fixed", "vnet-hub", "10.0.0.0/16"), vnet_step("rg-fixed", "vnet-hub", "10.0.0.0/16")
    second[0]["variables"]["tags"] = {**TAGS, "project": "other"}  # another state key, the same network
    refused(run([group_step("rg-fixed"), first, second]), 3, "policy", "an earlier step makes")


def test_a_step_in_a_group_that_is_not_existing_made_or_pending_is_refused(run):
    refused(run([vnet_step("rg-nowhere", "vnet-a", "10.0.0.0/16"), vnet_step("rg-nowhere", "vnet-b", "10.1.0.0/16")]),
            1, "policy", "rg-nowhere is not one this product may change")
    # the group's step comes later, so it is not pending yet: order is the engineer's, never rearranged
    refused(run([vnet_step("rg-later", "vnet-a", "10.0.0.0/16"), group_step("rg-later")]), 1, "policy", "rg-later")


def test_a_pending_group_claimed_in_the_request_is_ignored(run):
    answer = run([vnet_step("rg-claimed", "vnet-a", "10.0.0.0/16"), vnet_step("rg-claimed", "vnet-b", "10.1.0.0/16")],
                 pending={"groups": ["rg-claimed"], "ids": []}, groups_ok=["rg-claimed"])
    refused(answer, 1, "policy", "rg-claimed")


def test_existing_groups_and_made_groups_are_in_scope(cloud, run):
    pairs = [vnet_step("rg-live", "vnet-a", "10.0.0.0/16"), vnet_step("rg-live", "vnet-b", "10.1.0.0/16")]
    assert run(pairs, good_rails(resource_groups={"existing": ["rg-live"], "new_prefix": "rg-"}))["ok"]
    refused(run(pairs), 1, "policy", "rg-live")
    cloud.applied.append({"workspace": f"{SUB}/rg-live/resource_group/abc", "change_id": "chg-made"})
    refused(run(pairs), 1, "policy", "rg-live")  # a row with no ledger entry: the apply did not end well
    store.commit_usd("chg-made", 0)
    assert run(pairs)["ok"]


@pytest.fixture
def azure(monkeypatch):
    """What a GET of a parent answers: the IDs (lower case) that exist."""
    exists, asked = set(), []
    monkeypatch.setattr(arm, "token", lambda role: f"token-{role}")

    def get(token, path):
        asked.append((token, path))
        return {"id": path} if path.split("?")[0].lower() in exists else None

    monkeypatch.setattr(arm, "get", get)
    return exists, asked


def test_a_parent_must_be_in_azure_or_made_by_an_earlier_step(azure, run):
    exists, asked = azure
    pairs = [group_step("rg-hub"), vnet_step("rg-hub", "vnet-hub", "10.0.0.0/16"),
             peering_step("rg-hub", "vnet-hub", "rg-elsewhere", "vnet-gone")]
    refused(run(pairs), 3, "policy", "vnet-gone is in the resource group rg-elsewhere")  # not a group this product may change
    both = good_rails(resource_groups={"existing": ["rg-elsewhere"], "new_prefix": "rg-"})
    refused(run(pairs, both), 3, "policy", "vnet-gone does not exist, and no earlier step makes it.")
    assert asked == [("token-planner", f"{NET}/rg-elsewhere/providers/microsoft.network/virtualnetworks/vnet-gone?api-version=2024-05-01".lower())]
    exists.add(f"{NET}/rg-elsewhere/providers/microsoft.network/virtualnetworks/vnet-gone".lower())
    assert run(pairs, both)["ok"]  # the one in Azure is asked for; vnet-hub is pending, so it is not asked for
    assert all("vnet-hub" not in path for _, path in asked)


def test_a_parent_that_azure_cannot_answer_for_is_a_refusal(azure, run, monkeypatch):
    def broken(token, path):
        raise arm.ArmError("Azure answered 500")

    monkeypatch.setattr(arm, "get", broken)
    both = good_rails(resource_groups={"existing": ["rg-live"], "new_prefix": "rg-"})
    pairs = [subnet_step("rg-live", "vnet-live", "snet-a", "10.0.1.0/24"), subnet_step("rg-live", "vnet-live", "snet-b", "10.0.2.0/24")]
    refused(run(pairs, both), 1, "policy", "Azure could not be asked")


def test_a_set_whose_sum_is_over_the_ceiling_is_refused_though_each_step_is_under_it(run):
    def pip(name):
        return ({"template": "public_ip", "resource_group": "rg-live", "variables": {"name": name, "region": "uaenorth", "tags": TAGS}},
                plan(create("azurerm_public_ip", name, "rg-live", location="uaenorth", sku="Standard", allocation_method="Static", tags=TAGS)))

    price = lambda *a, **k: {"usd_month": 3.65}  # noqa: E731 - the fake price list
    live = {"existing": ["rg-live"], "new_prefix": "rg-"}
    over = run([pip("pip-a"), pip("pip-b")], good_rails(resource_groups=live, usd_month_per_change=5), price)
    refused(over, 0, "cost", "7.30 USD a month is more than the 5.00 USD limit for one change")
    total = run([pip("pip-a"), pip("pip-b")], good_rails(resource_groups=live, usd_month_total=7), price)
    refused(total, 0, "cost", "across approved changes")
    count = run([pip("pip-a"), pip("pip-b")], good_rails(resource_groups=live, max_resources=1), price)
    refused(count, 0, "policy", "2 resources is more than the limit of 1")
    ok = run([pip("pip-a"), pip("pip-b")], good_rails(resource_groups=live), price)
    assert ok["ok"] and ok["cost"]["monthly_usd"] == 7.3 and [i["step"] for i in ok["cost"]["items"]] == [1, 2]


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"], ["forget"]])
def test_a_delete_in_any_step_refuses_the_set_at_that_step(cloud, run, actions):
    pairs = hub_and_spoke()
    pairs[3][1]["resource_changes"][0]["change"]["actions"] = actions
    refused(run(pairs), 4, "policy", "never deletes")
    assert tfo(cloud, "apply") == []


def test_a_step_that_changes_nothing_is_refused(run):
    pairs = [vnet_step("rg-live", "vnet-a", "10.0.0.0/16"), vnet_step("rg-live", "vnet-b", "10.1.0.0/16")]
    pairs[1][1]["resource_changes"][0]["change"]["actions"] = ["no-op"]
    refused(run(pairs, good_rails(resource_groups={"existing": ["rg-live"], "new_prefix": "rg-"})), 2, "policy", "changes nothing")


def test_a_step_that_stops_at_init_validate_or_plan_names_its_stage_and_step(cloud, run):
    cloud.script(fail={"validate": "boom"})
    refused(run(hub_and_spoke()), 1, "validate")


def test_a_template_that_does_not_exist_and_values_that_fail_are_refused_at_their_step(run):
    pairs = hub_and_spoke()
    pairs[2][0]["template"] = "no_such_template"
    refused(run(pairs), 3, "gate", "no template named")
    pairs = hub_and_spoke()
    pairs[3][0]["variables"]["name"] = "not-a-vnet"
    refused(run(pairs), 4, "gate", "name")


def test_each_step_plans_in_the_steps_own_workspace_lock(cloud, run):
    held = []

    class Lock:
        def __init__(self, sub, group):
            self.key = (sub, group)

        def __enter__(self):
            held.append(self.key)

        def __exit__(self, *exc):
            return False

    cloud.script(plans=[p for _, p in hub_and_spoke()])
    body = {"subscription": SUB, "change_id": "s", "steps": [s for s, _ in hub_and_spoke()]}
    assert runner.plan_set(body, good_rails(), CATALOGUE, lock=Lock)["ok"]
    assert held == [(SUB, "rg-hub"), (SUB, "rg-spoke"), (SUB, "rg-hub"), (SUB, "rg-spoke"), (SUB, "rg-hub"), (SUB, "rg-spoke")]


def test_the_executor_that_cannot_say_what_it_made_plans_nothing(cloud, run, monkeypatch):
    def broken(sub, makers):
        raise RuntimeError("no database")

    monkeypatch.setattr(store, "made_groups", broken)
    refused(run(hub_and_spoke()), 1, "policy", "could not read which resource groups it has made")


# ------------------------------------------------------------------------------------------------- over HTTP

def test_plan_set_over_http_needs_the_job_token_and_takes_one_admission(cloud, monkeypatch):
    from cloud_executor import app as cloud_app

    monkeypatch.setattr(cloud_app, "_catalogue", None)
    rails.save({"subscriptions": [SUB], "regions": ["uaenorth"], "resource_types": ["azurerm_resource_group", "azurerm_virtual_network"],
                "resource_groups": {"existing": [], "new_prefix": "rg-"}}, [])
    monkeypatch.setattr(cloud_app, "_submissions", cloud_app.deque())
    client = cloud_app.app.test_client()
    pairs = [group_step("rg-hub"), vnet_step("rg-hub", "vnet-hub", "10.0.0.0/16")]
    cloud.script(plans=[p for _, p in pairs])
    body = {"subscription": SUB, "change_id": "set-1", "actor": "alice", "steps": [s for s, _ in pairs]}
    assert client.post("/v1/plan-set", json=body).status_code == 401
    assert client.post("/v1/plan-set", json=body, headers={"Authorization": "Bearer admin-token"}).status_code == 401
    job = {"Authorization": "Bearer job-token"}
    assert client.post("/v1/plan-set", json={"subscription": SUB}, headers=job).status_code == 400
    assert len(cloud_app._submissions) == 0
    answer = client.post("/v1/plan-set", json=body, headers=job)
    assert answer.status_code == 200 and answer.get_json()["ok"] and len(cloud_app._submissions) == 1  # one for the whole set
    assert "token" not in answer.get_data(as_text=True).lower()
    for _ in range(5):
        cloud.script(plans=[None] * saved_plans(cloud) + [p for _, p in pairs])
        client.post("/v1/plan-set", json=body, headers=job)
    assert client.post("/v1/plan-set", json=body, headers=job).status_code == 429  # the seventh call in a minute
