"""`azure_inventory` against a stored tenant (see azure_read_tenant.py): each owner question, the as-of line,
the limits, the refusals and the redactor. Needs PostgreSQL."""

import ast
import asyncio
import json
import re
from pathlib import Path

import pytest

from helpers import rbac, tool_permissions
from plugins._ea_azure.helpers import categories, store
from plugins._ea_azure.tests import azure_read_tenant as t
from plugins._ea_azure.tools import azure_inventory
from plugins._ea_azure.tools.azure_inventory import AzureInventory

TOOLS = Path(azure_inventory.__file__).parent
ASOF = re.compile(r"^Azure inventory as of \S+ \(\d+ days? ago\)\.")


def ask(**args):
    tool = AzureInventory.__new__(AzureInventory)
    tool.args = args
    return asyncio.new_event_loop().run_until_complete(tool.execute()).message


def data(**args):
    head, body = ask(**args).split("\n\n", 1)
    assert ASOF.match(head), head
    return json.loads(body)


@pytest.fixture
def tenant(database):
    return t.load()


def test_nothing_pulled_yet_says_only_that(database):
    assert ask() == "No Azure inventory has been pulled yet."
    assert ask(resource="vm1") == "No Azure inventory has been pulled yet."


def test_every_answer_starts_with_the_age_and_names_what_was_not_read(tenant):
    head = ask(count_by="state").split("\n\n", 1)[0]
    assert ASOF.match(head)
    assert re.search(r"\nNot read in that pull: Dev \(last read \d{4}-\d\d-\d\dT", head)  # its own, older date
    assert ask(resource="vm1").startswith("Azure inventory as of")
    assert ask(query="zzz-nothing").startswith("Azure inventory as of")
    assert tool_permissions.required_permission("azure_inventory") == rbac.PERM_INVENTORY_VIEW


def test_a_subscription_that_was_read_is_not_listed_as_missed(tenant):
    acct = store.account("Tenant-1")
    t.pull(acct, t.SUB2, t.dev(), t.dt.datetime.now(t.dt.timezone.utc), counts={"per_subscription": [
        {"id": t.SUB2, "name": "Dev", "status": "ok"}, {"id": t.SUB, "name": "Prod", "status": "ok"}]})
    assert "Not read" not in ask(count_by="state")


def test_how_many_app_services_in_a_group_and_how_many_are_running(tenant):
    body = data(type="app service", subscription="Prod", resource_group="rg-app", count_by="state")
    assert body == {"total": 2, "groups": [{"key": "running", "count": 1}, {"key": "stopped", "count": 1}]}
    listed = data(type="app service", subscription="Prod", resource_group="rg-app", state="stopped")
    assert listed["total"] == 1 and listed["resources"][0]["name"] == "app2"
    assert listed["resources"][0]["label"] == "Function app" and listed["resources"][0]["subscription"] == "Prod"
    assert data(type="web app", count_by="subscription")["groups"] == [
        {"key": "Prod", "count": 2}, {"key": "Dev", "count": 1}]


def test_what_is_the_vnet_and_what_is_the_subnet(tenant):
    nets = data(type="virtual network")
    assert nets["total"] == 1 and nets["resources"][0]["name"] == "vnet1" and nets["resources"][0]["tags"]["owner"] == "netops"
    page = data(resource="vnet1")
    assert page["props"]["address_space"] == ["10.0.0.0/16"]
    assert {c["name"]: c["props"].get("address_prefixes") for c in page["children"]}["sn-web"] == ["10.0.1.0/24"]
    assert page["links_out"] == [{"kind": "peering", "name": "far", "props": {"name": "p", "state": "Connected"},
                                  "not_in_inventory": page["links_out"][0]["not_in_inventory"]}]


def test_what_route_is_configured_and_is_it_configured_or_not(tenant):
    subnets = {c["name"]: c for c in data(resource="vnet1")["children"]}
    assert subnets["sn-web"]["route_table"] == {"name": "rt1", "routes": [
        {"name": "default", "prefix": "0.0.0.0/0", "next_hop_type": "VirtualAppliance", "next_hop_ip": "10.0.254.5"}]}
    assert subnets["sn-web"]["nsg"] == {"name": "nsg1"}
    assert subnets["sn-data"]["route_table"] is None and "route_table" in subnets["sn-data"]  # spelled out
    assert subnets["sn-data"]["nat_gateway"] is None
    alone = data(resource="sn-data")  # the subnet's own page says it too
    assert alone["route_table"] is None and alone["nsg"] == {"name": "nsg2"} and alone["parent"] == "vnet1"
    assert data(resource="sn-app")["route_table"]["routes"][0]["name"] == "default"


def test_which_subnets_have_no_route_table(tenant):
    body = data(type="subnet", without="route_table")
    assert body["total"] == 1 and [r["name"] for r in body["resources"]] == ["sn-data"]
    assert body["resources"][0]["parent"] == "vnet1" and body["resources"][0]["label"] == "Subnet"
    assert data(type="subnet", count_by="subscription")["groups"] == [{"key": "Prod", "count": 3}]


def test_what_changed_since_the_last_pull(tenant):
    body = data(changed_since="last_pull", count_by="category")
    assert body["total"] == 3 and {g["key"]: g["count"] for g in body["groups"]} == {"compute": 2, "networking": 1}
    listed = data(changed_since="last_pull", include_removed="yes")
    assert {r["name"] for r in listed["resources"]} == {"rt1", "vm2", "newvm", "oldstor"}
    assert [r for r in listed["resources"] if r["name"] == "oldstor"][0]["removed_at"]
    assert data(changed_since="2020-01-01")["total"] == 3


def test_stopped_untagged_and_the_counts_by_each_group(tenant):
    assert {r["name"] for r in data(state="stopped")["resources"]} == {"vm1", "vm2", "app2"}
    assert {r["name"] for r in data(without="tags")["resources"]} == {"sn-web", "sn-data", "sn-app", "stor1"}
    assert {r["name"] for r in data(tag="owner=netops")["resources"]} == {"vnet1"}
    assert data(count_by="subscription")["groups"] == [{"key": "Prod", "count": 20}, {"key": "Dev", "count": 2}]
    assert data(count_by="category")["total"] == 22
    types = data(count_by="type", subscription="Dev")["groups"]
    assert {g["key"]: g["label"] for g in types} == {"microsoft.compute/virtualmachines": "Virtual machine",
                                                     "microsoft.web/sites": "Web app"}
    assert {g["key"] for g in data(count_by="resource_group")["groups"]} == {
        "rg-net (Prod)", "rg-vm (Prod)", "rg-app (Prod)", "rg-dev (Dev)"}
    assert data(count_by="region")["groups"] == [{"key": "uaenorth", "count": 22}]


def test_one_resource_carries_its_links_exposure_and_changes(tenant):
    vm = data(resource="vm1")
    assert vm["exposure"] == ["public_ip"] and vm["links_out"][0]["name"] == "nic1" and vm["state_detail"] == "PowerState/deallocated"
    app = data(resource="app1")
    assert {x["kind"]: x["name"] for x in app["links_out"]} == {"plan": "plan1", "vnet_integration": "sn-app"}
    assert app["exposure"] == ["public_access"] and app["subscription"] == "Prod" and app["resource_group"] == "rg-app"
    rt = data(resource="rt1")
    assert [(c["change"], list(c["fields"])) for c in rt["changes"]] == [("changed", ["props.routes"])]
    assert {x["name"] for x in rt["links_in"]} == {"sn-web", "sn-app"}
    assert data(resource=store.get("vm3")["native_id"])["name"] == "vm3"
    assert "No resource matches nothing" in ask(resource="nothing") and ask(resource="nothing").startswith("Azure inventory as of")


def test_an_ambiguous_name_lists_the_candidates(tenant):
    t.pull(store.account("Tenant-1"), t.SUB2, [*t.dev(), t.res("dup", rg="rg-dev", sub=t.SUB2),
                                               t.res("dup", "microsoft.web/sites", "rg-dev", sub=t.SUB2, category="web")],
           t.dt.datetime.now(t.dt.timezone.utc))
    answer = ask(resource="dup")
    assert "Several resources are named dup" in answer and answer.count('"native_id"') == 2


def test_a_list_stops_at_25_and_says_how_many_there_are(tenant):
    t.pull(store.account("Tenant-1"), t.SUB2, [*t.dev(), *[t.res(f"extra{i:02}", rg="rg-dev", sub=t.SUB2) for i in range(30)]],
           t.dt.datetime.now(t.dt.timezone.utc))
    body = data(subscription="Dev")
    assert body["total"] == 32 and len(body["resources"]) == 25
    assert body["note"] == "Showing the first 25 of 32; narrow the query."
    assert "note" not in data(subscription="Dev", query="extra0")  # 10 of them: nothing to say


def test_a_count_stops_at_50_groups_and_a_resource_at_60_links(tenant):
    many = [t.res(f"r{i:02}", f"microsoft.fake{i}/thing", rg="rg-dev", sub=t.SUB2, category="other") for i in range(55)]
    hub = t.net("hub", t.VNET, children=[t.res(f"s{i:02}", t.SUBNET, "rg-net", category="networking", relations=[]) for i in range(70)])
    now = t.dt.datetime.now(t.dt.timezone.utc)
    t.pull(store.account("Tenant-1"), t.SUB2, [*t.dev(), *many], now)
    t.pull(store.account("Tenant-1"), t.SUB, [*t.prod(second=True), hub], now)
    groups = data(count_by="type")
    assert len(groups["groups"]) == 50 and groups["total"] == 22 + 55 + 1 + 70
    assert groups["note"].startswith("Showing the largest 50 of ")
    page = data(resource="hub")
    assert len(page["children"]) == 60 and page["children_not_shown"] == 10
    assert len(page["children"]) + len(page["links_out"]) + len(page["links_in"]) <= 60
    assert data(resource="vnet1").get("children_not_shown") is None


@pytest.mark.parametrize("args, allowed", [
    ({"category": "bogus"}, "compute, web, containers"), ({"state": "asleep"}, "running, stopped, failed, ok, unknown"),
    ({"without": "owner"}, "route_table, security_group, tags"), ({"count_by": "colour"}, "subscription, resource_group"),
    ({"type": "gadget"}, "app service"),
])
def test_an_unknown_value_is_refused_with_the_allowed_ones(tenant, args, allowed):
    out = ask(**args)
    assert allowed in out and "Azure inventory as of" not in out
    assert "must be one of" in ask(category="Bogus") and ask(category="Storage").startswith("Azure inventory as of")
    assert "changed_since must be" in ask(changed_since="whenever")


def test_whatever_azure_named_passes_the_redactor_and_a_planted_marker_never_comes_back(tenant):
    leaky = t.res("leaky", rg="rg-dev", sub=t.SUB2, tags={"note": "password PLANTED-SECRET-1 for the box"},
                  props={"host_names": ["password PLANTED-SECRET-2"]})
    t.pull(store.account("Tenant-1"), t.SUB2, [*t.dev(), leaky], t.dt.datetime.now(t.dt.timezone.utc))
    for args in ({"query": "leaky"}, {"resource": "leaky"}, {"subscription": "Dev"}, {"tag": "note"}):
        out = ask(**args)
        assert "PLANTED-SECRET" not in out and "leaky" in out, args
    assert "[REDACTED:password]" in ask(resource="leaky")


def test_the_tool_cannot_reach_the_network():
    imported = set()
    for node in ast.walk(ast.parse((TOOLS / "azure_inventory.py").read_text())):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{node.module}.{a.name}" for a in node.names}
    assert imported <= {
        "__future__.annotations", "datetime", "json", "typing.Any", "helpers.tool.Response", "helpers.tool.Tool",
        "plugins._ea_azure.helpers.categories", "plugins._ea_azure.helpers.exposure", "plugins._ea_azure.helpers.store",
        "plugins._ea_azure.helpers.waste", "plugins._ea_azure.helpers.signals", "plugins._ea_inventory.helpers.redact",
    }, imported
    assert categories and store  # the modules it does import are the ones above


def test_a_change_records_author_is_never_handed_to_the_model_when_it_is_a_person():
    from plugins._ea_azure.tools import azure_inventory as tool

    person = {"changed_by": "someone@example.com", "changed_by_type": "User", "operation": "write"}
    app = {"changed_by": "2cf9eb86-0000-4000-8000-9a63135dfa8c", "changed_by_type": "Application"}
    assert tool._clean([person])[0]["changed_by"] == tool.PERSON
    assert tool._clean({"changed_by": "x@example.com"})["changed_by"] == tool.PERSON  # type missing, still a sign-in name
    assert tool._clean(app)["changed_by"] == app["changed_by"]
