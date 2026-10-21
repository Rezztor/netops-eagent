"""The four read endpoints against a stored tenant (see azure_read_tenant.py). Needs PostgreSQL."""

import asyncio

import pytest

from helpers import api_policy, rbac
from plugins._ea_azure.api.azure_history import AzureHistory
from plugins._ea_azure.api.azure_resource import AzureResource
from plugins._ea_azure.api.azure_resources import AzureResources
from plugins._ea_azure.api.azure_tree import AzureTree
from plugins._ea_azure.helpers import categories, config, store
from plugins._ea_azure.tests import azure_read_tenant as t


def call(handler, **input):
    return asyncio.new_event_loop().run_until_complete(handler(app=None, thread_lock=None).process(input, None))


def names(**filters):
    out = call(AzureResources, limit=200, **filters)
    assert out["ok"], out
    return {r["name"] for r in out["resources"]}


def one(ref):
    out = call(AzureResource, id=ref)
    assert out["ok"], out
    return out


@pytest.fixture
def tenant(database, monkeypatch):
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    monkeypatch.setattr(config, "get_config", lambda: {"tenant_id": "TENANT"})
    return t.load()


def test_every_endpoint_is_gated_on_inventory_view():
    for name in ("azure_tree", "azure_resources", "azure_resource", "azure_history"):
        assert api_policy.required_permission_for(f"plugins/_ea_azure/{name}") == rbac.PERM_INVENTORY_VIEW


def test_tree_counts_add_up_at_every_level_and_leave_out_removed(tenant):
    out = call(AzureTree)
    subs = {s["name"]: s for s in out["tree"]["subscriptions"]}
    assert {n: s["count"] for n, s in subs.items()} == {"Prod": 20, "Dev": 2}  # oldstor is removed
    for s in subs.values():
        assert s["count"] == sum(g["count"] for g in s["groups"]) == sum(s["categories"].values())
        for g in s["groups"]:
            assert g["count"] == sum(g["categories"].values())
    assert {g["name"]: g["count"] for g in subs["Prod"]["groups"]} == {"rg-net": 8, "rg-vm": 5, "rg-app": 7}
    assert subs["Prod"]["categories"] == {"networking": 10, "compute": 3, "web": 3, "storage": 2,
                                          "identity_security": 1, "containers": 1}
    assert out["attention"]["stopped_failed"] == 3 and out["attention"]["no_tags"] == 4
    assert out["attention"]["changed"] == 3  # rt1 changed, vm2 changed, newvm added; oldstor is gone
    assert out["attention"]["exposed"] >= 5
    assert out["enabled"] is True and out["as_of"]["last_pull"]["status"] == "partial"
    assert [s["readable"] for s in out["as_of"]["subscriptions"]] == [True, True]


def test_filters_each_narrow_the_list(tenant):
    assert len(names()) == 22
    assert names(q="plan1") == {"plan1"} and names(q="MICROSOFT.KEYVAULT") == {"kv1"}
    assert names(subscription="dev") == {"vm3", "app3"} and names(subscription=t.SUB2) == {"vm3", "app3"}
    assert names(resource_group="rg-dev") == {"vm3", "app3"}
    assert names(subscription="Prod", resource_group="rg-app") == {"plan1", "app1", "app2", "stor1", "stor2", "kv1", "ca1"}
    assert names(category="storage") == {"stor1", "stor2"} and names(category="containers") == {"ca1"}
    assert names(type="microsoft.web/sites") == {"app1", "app2", "app3"}
    assert names(type="Microsoft.Web/Sites", state="stopped") == {"app2"}
    assert names(state="stopped") == {"vm1", "vm2", "app2"} and names(state="running") == {"app1", "app3"}
    assert names(region="UAENORTH") == names() and names(region="westeurope") == set()
    assert names(tag="owner") == {"vnet1"} and names(tag="owner=netops") == {"vnet1"} and names(tag="owner=x") == set()
    assert names(without="tags") == {"sn-web", "sn-data", "sn-app", "stor1"}
    assert names(type="microsoft.network/virtualnetworks/subnets", without="route_table") == {"sn-data"}
    assert names(without="security_group", type="microsoft.network/networkinterfaces") == {"nic2"}
    assert names(attention="stopped_failed") == {"vm1", "vm2", "app2"}
    assert {"nic1", "nsg1", "app1", "stor1", "ca1"} <= names(attention="exposed") and "stor2" not in names(attention="exposed")
    assert "vm1" in names(attention="exposed") and "vm2" not in names(attention="exposed")  # through its network card
    assert names(attention="changed") == names(changed_since="last_pull") == {"rt1", "vm2", "newvm"}
    assert "oldstor" in names(include_removed="yes") and "oldstor" in names(include_removed=True)
    assert "oldstor" not in names()
    assert names(include_removed=True, changed_since="last_pull") == {"rt1", "vm2", "newvm", "oldstor"}


def test_facets_offer_the_scope_not_what_is_already_picked(tenant):
    out = call(AzureResources, type="microsoft.web/sites", limit=200)
    facets = out["facets"]
    assert out["total"] == 3
    types = {f["type"]: f for f in facets["types"]}
    assert types["microsoft.web/sites"]["count"] == 3 and len(types) > 5  # the type picked does not empty the list
    assert types["microsoft.web/sites"]["label"] == categories.label_of("microsoft.web/sites")
    assert facets["regions"] == [{"key": "uaenorth", "count": 22}]
    assert {s["key"]: s["count"] for s in facets["states"]} == {"ok": 17, "stopped": 3, "running": 2}
    assert facets["tag_names"] == ["env", "owner"]
    narrowed = call(AzureResources, category="storage")["facets"]  # a scope filter does narrow them
    assert [f["type"] for f in narrowed["types"]] == ["microsoft.storage/storageaccounts"]
    assert narrowed["tag_names"] == ["env"]


def test_paging_clamps_and_a_bad_filter_is_an_answer_not_a_crash(tenant):
    out = call(AzureResources, limit=5, offset=20)
    assert len(out["resources"]) == 2 and out["total"] == 22
    assert len(call(AzureResources, limit=9999)["resources"]) == 22
    assert len(call(AzureResources, limit="junk", offset="x")["resources"]) == 22
    bad = call(AzureResources, without="everything")
    assert bad["ok"] is False and "route_table" in bad["error"]
    assert call(AzureResources, attention="nope")["ok"] is False and call(AzureResources, changed_since="soon")["ok"] is False


def test_resource_page_of_a_network(tenant):
    out = one("vnet1")
    assert out["resource"]["label"] == "Virtual network" and out["resource"]["props"]["address_space"] == ["10.0.0.0/16"]
    assert "links_out" not in out["resource"]
    subnets = {c["name"]: c for c in out["children"]}
    assert set(subnets) == {"sn-web", "sn-data", "sn-app"}
    assert {x["kind"]: x["name"] for x in subnets["sn-web"]["links_out"]} == {"route_table": "rt1", "nsg": "nsg1"}
    assert {x["kind"] for x in subnets["sn-data"]["links_out"]} == {"nsg"}  # no route table
    (peering,) = out["links_out"]
    assert peering["kind"] == "peering" and peering["id"] is None and peering["props"]["state"] == "Connected"
    assert out["exposure"] == [] and out["changes"] == []
    assert out["portal_url"] == f"https://portal.azure.com/#@TENANT/resource{out['resource']['native_id']}"
    route_table = one("rt1")
    assert route_table["resource"]["props"]["routes"][0]["next_hop_ip"] == "10.0.254.5"
    assert [(c["change"], list(c["fields"])) for c in route_table["changes"]] == [("changed", ["props.routes"])]
    assert {x["name"] for x in route_table["links_in"]} == {"sn-web", "sn-app"}


def test_resource_page_of_an_app_and_of_a_machine(tenant):
    app = one("app1")
    assert {x["kind"]: x["name"] for x in app["links_out"]} == {"plan": "plan1", "vnet_integration": "sn-app"}
    assert app["resource"]["label"] == "Web app" and app["exposure"] == ["public_access"]
    assert one("app2")["resource"]["label"] == "Function app"
    assert {x["name"] for x in one("plan1")["links_in"]} == {"app1", "app2"}
    vm = one("vm1")
    assert [(x["kind"], x["name"]) for x in vm["links_out"]] == [("nic", "nic1")]
    assert vm["exposure"] == ["public_ip"] and one("vm2")["exposure"] == []
    nic = one("nic1")
    assert {x["kind"]: x["name"] for x in nic["links_out"]} == {"subnet": "sn-web", "public_ip": "pip1", "nsg": "nsg1"}
    assert [x["name"] for x in nic["links_in"]] == ["vm1"] and nic["exposure"] == ["public_ip"]
    assert one("sn-web")["resource"]["parent"]["name"] == "vnet1"
    assert one("nsg1")["exposure"] == ["open_rule"] and one("nsg2")["exposure"] == []


def test_a_resource_is_found_by_row_id_native_id_or_unique_name_and_a_shared_name_is_ambiguous(tenant):
    found = one("vm3")["resource"]
    assert one(found["id"])["resource"]["name"] == one(found["native_id"].upper())["resource"]["name"] == "vm3"
    acct = store.account("Tenant-1")
    t.pull(acct, t.SUB2, [*t.dev(), t.res("dup", rg="rg-dev", sub=t.SUB2),
                          t.res("dup", "microsoft.web/sites", "rg-dev", sub=t.SUB2, category="web")],
           t.dt.datetime.now(t.dt.timezone.utc))
    out = call(AzureResource, id="dup")
    assert out["ok"] is False and len(out["candidates"]) == 2 and {c["name"] for c in out["candidates"]} == {"dup"}
    assert call(AzureResource, id="nothing")["ok"] is False and call(AzureResource)["ok"] is False


def test_history_lists_pulls_and_the_changes_of_one(tenant):
    runs = call(AzureHistory)["runs"]
    assert [r["status"] for r in runs] == ["partial", "succeeded", "succeeded"]
    out = call(AzureHistory, run_id=runs[0]["id"])
    kinds = {c["resource"]["name"]: c for c in out["changes"]}
    assert out["total"] == 4 and {n: c["change"] for n, c in kinds.items()} == {
        "rt1": "changed", "vm2": "changed", "newvm": "added", "oldstor": "removed"}
    assert kinds["vm2"]["fields"]["state"] == {"old": "running", "new": "stopped"}
    assert kinds["rt1"]["fields"]["props.routes"]["old"][0]["next_hop_ip"] == "10.0.254.4"
    assert kinds["rt1"]["resource"]["subscription"] == "Prod" and kinds["oldstor"]["resource"]["removed_at"]
    assert call(AzureHistory, run_id=runs[1]["id"])["total"] == 0  # a baseline records no added rows
    paged = call(AzureHistory, run_id=runs[0]["id"], limit=1, offset=1)
    assert len(paged["changes"]) == 1 and paged["total"] == 4
    assert call(AzureHistory, since="2020-01-01")["total"] == 4
    assert call(AzureHistory, since="yesterday-ish")["ok"] is False


def test_stored_data_is_still_served_with_the_plugin_off(tenant, monkeypatch):
    monkeypatch.setattr(config, "is_enabled", lambda: False)
    tree, listed = call(AzureTree), call(AzureResources)
    assert tree["enabled"] is listed["enabled"] is False and tree["tree"]["subscriptions"] and listed["total"] == 22
    assert one("vnet1")["enabled"] is False and call(AzureHistory)["enabled"] is False and call(AzureHistory)["runs"]


def test_nothing_pulled_yet_is_an_empty_answer(database, monkeypatch):
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    assert call(AzureTree)["tree"] == {"account": None, "subscriptions": []}
    assert call(AzureResources)["total"] == 0 and call(AzureHistory)["runs"] == []
