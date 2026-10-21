"""`azure_advise` against a stored tenant: each check with its evidence, the refusals, the redactor, the price (stand-in
for prices.azure.com) and the promise that Azure itself is never contacted. Needs PostgreSQL."""

import ast
import asyncio
import datetime as dt
import json
import re
from ipaddress import ip_network as net
from pathlib import Path

import pytest

from helpers import db, rbac, tool_permissions
from plugins._ea_azure.helpers import advise, prices, store
from plugins._ea_azure.tests import azure_read_tenant as t
from plugins._ea_azure.tests.fake_prices import FakePrices
from plugins._ea_azure.tools import azure_advise
from plugins._ea_azure.tools.azure_advise import AzureAdvise

ASOF = re.compile(r"^Azure inventory as of \S+ \(\d+ days? ago\)\.")
ARGS = {"purpose": "payments web app", "env": "prod", "region": "uaenorth", "needs": ["vnet", "web app"], "address_size": "24"}


def ask(**args):
    tool = AzureAdvise.__new__(AzureAdvise)
    tool.args = args
    return asyncio.new_event_loop().run_until_complete(tool.execute()).message


def advice(**args):
    head, body = ask(**{**ARGS, **args}).split("\n\n", 1)
    assert ASOF.match(head), head
    return json.loads(body)


def found(body, check, nth=0):
    return [f for f in body["findings"] if f["check"] == check][nth]


def estate():
    """Prod: a hub and a web network, a route table, a local gateway, a hub of a virtual WAN, web apps, a plan, a VM, storage,
    and a network that was removed in the second pull. The on-prem inventory is added by `onprem`."""
    hub = t.net("vnet-hub-uaenorth-001", t.VNET, tags={"env": "prod", "owner": "netops"}, props={
        "address_space": ["10.0.0.0/16"], "dns_servers": ["10.0.0.4"],
        "peerings": [{"name": "to-far", "state": "Connected", "remote_address_space": ["10.1.0.0/16"], "allow_gateway_transit": True}]},
        children=[t.res("AzureFirewallSubnet", t.SUBNET, "rg-net", category="networking", tags={},
                        props={"address_prefixes": ["10.0.254.0/26"]})])
    web = t.net("vnet-web-uaenorth-002", t.VNET, tags={"env": "prod", "owner": "netops"}, props={"address_space": ["10.2.0.0/16"]})
    rt = t.net("rt-hub", "microsoft.network/routetables", props={"routes": [
        {"name": "default", "prefix": "0.0.0.0/0", "next_hop_type": "VirtualAppliance", "next_hop_ip": "10.0.254.4"},
        {"name": "to-onprem", "prefix": "192.168.0.0/16", "next_hop_type": "VirtualNetworkGateway"}]})
    lgw = t.net("lgw-dc", "microsoft.network/localnetworkgateways", props={"address_space": ["172.16.0.0/16"]})
    vhub = t.net("vhub1", "microsoft.network/virtualhubs", props={"address_prefix": "10.4.0.0/24"})
    app1 = t.res("app-shop-prod-001", "microsoft.web/sites", "rg-app", category="web", tags={"env": "prod"}, props={"zones": []})
    app2 = t.res("app-blog-prod-002", "microsoft.web/sites", "rg-app", category="web", tags={"env": "prod", "owner": "web"})
    plan = t.res("asp-shop-prod-001", "microsoft.web/serverfarms", "rg-app", category="web", tags={"env": "prod"},
                 sku={"name": "P1v3"}, props={"linux": True})
    vm = t.res("vm-build-prod-001", props={"size": "Standard_D2s_v5", "os_type": "Linux", "zones": ["1"]}, tags={"env": "prod"})
    st = [t.res(f"st{e}{n:03d}", "microsoft.storage/storageaccounts", "rg-app", category="storage", tags={"env": "prod"})
          for e, n in (("prod", 1), ("test", 2))]
    log = t.res("log-shared-uaenorth", "microsoft.operationalinsights/workspaces", "rg-app", category="monitoring")
    old = t.net("vnet-payments-prod-uaenorth-001", t.VNET, props={"address_space": ["10.9.0.0/16"]})
    return [hub, web, rt, lgw, vhub, app1, app2, plan, vm, *st, log], old


def onprem():
    with db.transaction(db.NO_SCOPE) as conn:
        for name, ip in (("sw1", "10.3.0.5"), ("old-sw", "10.7.0.1")):
            conn.execute("INSERT INTO inventory_device (name, mgmt_ip, status) VALUES (%s, %s, %s)",
                         (name, ip, "decommissioned" if name == "old-sw" else "active"))
        dev = conn.execute("SELECT id FROM inventory_device WHERE name = 'sw1'").fetchone()[0]
        conn.execute("INSERT INTO inventory_interface (device_id, name, ip_prefixes) VALUES (%s, 'Vlan50', %s)", (dev, ["192.168.50.1/24"]))


@pytest.fixture
def tenant(database):
    acct = store.account("Tenant-1", "Contoso")
    now = dt.datetime.now(dt.timezone.utc)
    store.apply_containers(acct, [{"id": t.SUB, "name": "Prod", "state": "Enabled"}], t.groups(t.SUB, ("rg-net", "rg-app", "rg-vm")), now)
    rows, old = estate()
    t.pull(acct, t.SUB, [*rows, old], now - dt.timedelta(days=3))
    t.pull(acct, t.SUB, rows, now)  # the payments network is gone
    onprem()
    return acct


@pytest.fixture
def price_list(monkeypatch):
    with FakePrices() as fake:
        monkeypatch.setattr(prices, "HOST", fake.url)
        monkeypatch.setattr(prices, "_down_until", 0.0)
        yield fake


def test_nothing_pulled_yet_says_only_that(database):
    assert ask(**ARGS) == "No Azure inventory has been pulled yet."


@pytest.mark.parametrize("args, says", [
    ({"purpose": ""}, "purpose is needed"), ({"purpose": "x" * 81}, "purpose is needed"), ({"env": ""}, "env is needed"),
    ({"env": "prod; drop"}, "env is needed"), ({"region": "uae north'"}, "region must be"),
    ({"needs": ["spaceship"]}, "needs is at most 8 of: app service"), ({"needs": ["vm"] * 9}, "needs is at most 8"),
    ({"address_size": "33"}, "address_size must be"), ({"address_size": "/x"}, "address_size must be"),
])
def test_a_bad_argument_is_refused_with_a_sentence(tenant, args, says):
    out = ask(**{**ARGS, **args})
    assert says in out and "Azure inventory as of" not in out


def test_an_unknown_subscription_is_refused_naming_the_known_ones(tenant):
    assert "No subscription Nope in the stored inventory; it holds: Prod." == ask(**ARGS, subscription="Nope")


def test_a_subscription_is_accepted_by_its_guid_as_well_as_its_name(tenant):
    """The change tool asks for the GUID, so the agent often has only that (seen on the real tenant, 2026-10-03)."""
    by_name, by_guid = advice(**ARGS, subscription="Prod"), advice(**ARGS, subscription=t.SUB.upper())
    assert [f["check"] for f in by_guid["findings"]] == [f["check"] for f in by_name["findings"]]


def test_the_free_block_skips_azure_peerings_routes_gateways_hubs_and_the_on_prem_inventory(tenant):
    b = found(advice(), "address_block")
    free = b["evidence"]["first_free_block_in_each_range"]
    # 10.0 hub, 10.1 peering, 10.2 web, 10.3.0.5 on-prem host, 10.4.0.0/24 virtual hub: the first free /24 is 10.3.1.0
    assert free == {"10.0.0.0/8": "10.3.1.0/24", "172.16.0.0/12": "172.17.0.0/24", "192.168.0.0/16": None}
    assert "First free /24: 10.3.1.0/24 (in 10.0.0.0/8)" in b["summary"]
    froms = {n["from"] for n in b["evidence"]["networks_counted"]}
    assert {"virtual network vnet-hub-uaenorth-001", "peering to-far of vnet-hub-uaenorth-001", "route to-onprem in rt-hub",
            "local gateway lgw-dc", "virtual hub vhub1", "on-prem sw1 management address", "on-prem sw1 Vlan50"} <= froms
    assert not any("old-sw" in f for f in froms) and not any("default" in f for f in froms)  # decommissioned; 0.0.0.0/0 is no network
    assert b["evidence"]["usable_addresses_as_a_subnet"] == 251 and len(b["evidence"]["not_counted"]) == 3
    big = found(advice(address_size="16"), "address_block")
    assert big["evidence"]["first_free_block_in_each_range"]["10.0.0.0/8"] == "10.5.0.0/16"
    huge = advice(address_size="12")
    assert found(huge, "address_size")["level"] == "warn"


def test_a_vnet_need_defaults_to_a_24_and_no_vnet_no_size_means_no_address_finding(tenant):
    assert found(advice(address_size=""), "address_block")["evidence"]["asked_size"] == "/24"
    assert not [f for f in advice(address_size="", needs=["web app"])["findings"] if f["check"] == "address_block"]


def test_a_name_follows_what_the_estate_does_and_is_checked_against_azures_rules(tenant):
    body = advice(needs=["vnet", "web app", "storage account", "key vault"])
    names = {f["summary"].split(":")[0]: re.search(r"`(.+?)`", f["summary"]).group(1) for f in body["findings"] if f["check"] == "naming"}
    assert names["Virtual network"] == "vnet-payments-web-uaenorth-003"  # 001 and 002 exist; these networks leave the environment out
    assert names["Web app"] == "app-payments-web-prod-003"  # this estate's web apps leave the region out
    assert names["Storage account"] == "stpaymentsweb" + "prod003" and "-" not in names["Storage account"]  # letters and digits only
    assert names["Key vault"] == "kv-payments-web-prod-001"  # no vault in the estate: the Cloud Adoption Framework default, minus the region to fit 24
    basis = {f["summary"].split(":")[0]: f["evidence"]["basis"] for f in body["findings"] if f["check"] == "naming"}
    assert basis["Virtual network"] == "the names already in use" and basis["Key vault"].startswith("Cloud Adoption Framework default")
    rules = found(body, "name_rules", 2)
    assert "3 to 24 characters, unique in all of Azure" in rules["summary"] and rules["level"] == "info"


def test_a_name_that_does_not_fit_loses_its_region_then_its_environment(tenant):
    f = found(advice(needs=["key vault"]), "naming")  # no vault in the estate: the default would be 34 characters, a vault allows 24
    assert f["summary"].startswith("Key vault: suggest `kv-payments-web-prod-001`") and f["level"] == "info"
    assert f["evidence"]["left_out_to_fit"] == ["region"]
    long = found(advice(needs=["key vault"], purpose="international payments", env="production"), "naming")
    assert "`kv-international-001`" in long["summary"] and long["evidence"]["left_out_to_fit"] == ["region", "env"]


@pytest.mark.parametrize("type_, name, ok", [
    ("microsoft.storage/storageaccounts", "stpay001", True), ("microsoft.storage/storageaccounts", "St-pay", False),
    ("microsoft.storage/storageaccounts", "ab", False), ("microsoft.storage/storageaccounts", "a" * 25, False),
    ("microsoft.keyvault/vaults", "kv-pay-1", True), ("microsoft.keyvault/vaults", "kv--pay", False),
    ("microsoft.keyvault/vaults", "1kv", False), ("microsoft.keyvault/vaults", "kv-pay-", False),
    ("microsoft.network/virtualnetworks", "vnet_a.b-1", True), ("microsoft.network/virtualnetworks", "-vnet", False),
    ("microsoft.network/virtualnetworks", "vnet-", False), ("microsoft.network/virtualnetworks", "v", False),
    ("microsoft.web/sites", "app-1", True), ("microsoft.web/sites", "app-", False), ("microsoft.web/sites", "app_1", False),
    ("microsoft.compute/virtualmachines", "vm-1", True), ("microsoft.compute/virtualmachines", "vm-", False),
    ("microsoft.compute/virtualmachines", "vm.1", False), ("microsoft.sql/servers", "sql-pay", True),
    ("microsoft.sql/servers", "SQL-pay", False), ("microsoft.sql/servers/databases", "db one", True),
    ("microsoft.sql/servers/databases", "db?", False), ("microsoft.sql/servers/databases", "db.", False),
])
def test_azures_name_rules_accept_and_refuse(type_, name, ok):
    assert (advise._rule_problems(type_, name) == []) is ok


def test_a_look_alike_name_including_a_removed_one_is_a_question(tenant):
    f = found(advice(purpose="payments", env="prod"), "duplicates")
    assert f["level"] == "warn"
    twin = f["evidence"][0]
    assert twin["name"] == "vnet-payments-prod-uaenorth-001" and twin["removed_at"]


def test_tags_region_zones_hub_and_the_things_not_read(tenant):
    body = advice()
    tags = {r["tag"]: r for r in found(body, "tags")["evidence"]}
    assert tags["env"]["values"] == ["prod"] and tags["owner"]["percent"] < tags["env"]["percent"]
    assert "env" in found(body, "tags")["summary"]
    region = found(body, "region")
    assert region["level"] == "info" and {"region": "uaenorth", "resources": 13} in region["evidence"]
    assert found(advice(region="westeurope"), "region")["level"] == "warn"
    hub = found(body, "hub")["evidence"]
    assert hub["networks_with_hub_subnets"] == {"vnet-hub-uaenorth-001": ["AzureFirewallSubnet"]}
    assert hub["peerings_with_gateway_transit"] == [{"network": "vnet-hub-uaenorth-001", "peering": "to-far"}]
    assert hub["default_route_to_an_appliance"][0]["next_hop"] == "10.0.254.4" and hub["networks_with_custom_dns"]
    assert {"zones": "1", "resources": 1} in found(advice(needs=["vm"]), "zones")["evidence"]
    assert found(body, "policy_locks_budget")["level"] == "not_checked"
    assert found(body, "log_workspaces")["evidence"] == [{"name": "log-shared-uaenorth", "region": "uaenorth"}]
    assert found(body, "reserved_subnets")["evidence"]["GatewaySubnet"] == "/27 or larger"


def test_every_finding_but_a_price_carries_the_pull_date(tenant, price_list):
    body = advice(needs=["vnet", "vm", "app service plan"])
    assert all(f.get("as_of") for f in body["findings"] if f["check"] != "list_price")
    assert all("fetched_at" in p for f in body["findings"] if f["check"] == "list_price" for p in f["evidence"])


def test_list_price_of_the_skus_in_use_from_the_public_list_and_the_wording_that_says_what_it_is(tenant, price_list):
    body = advice(needs=["vm", "app service plan"])
    vm, plan = [f for f in body["findings"] if f["check"] == "list_price"]
    assert vm["evidence"][0]["usd_month"] == 86.14 and plan["evidence"][0]["usd_month"] == 139.43  # 730 hours; Linux plan
    assert "list price from Microsoft's public price list, 730 hours a month, before data transfer" in vm["summary"]
    assert {r["query"]["$filter"].split("'")[1] for r in price_list.received} == {"uaenorth"}


def test_the_price_is_unknown_when_microsoft_cannot_be_reached_and_the_rest_still_answers(tenant, monkeypatch):
    monkeypatch.setattr(prices, "HOST", "http://127.0.0.1:9")
    monkeypatch.setattr(prices, "_down_until", 0.0)
    body = advice(needs=["vm", "app service plan", "vnet"])
    price = found(body, "list_price")
    assert price["level"] == "not_checked" and price["summary"].startswith("Price unknown")
    assert price["evidence"][0]["price"] == "unknown" and found(body, "address_block")["level"] == "info"


def test_whatever_azure_named_passes_the_redactor_and_a_planted_marker_never_comes_back(tenant):
    leaky = t.res("password PLANTED-SECRET-1", tags={"note": "password PLANTED-SECRET-2 for the box"})
    t.pull(tenant, t.SUB, [*estate()[0], leaky], dt.datetime.now(dt.timezone.utc))
    out = ask(**{**ARGS, "purpose": "password PLANTED-SECRET-3 box", "needs": ["vm", "vnet"]})
    assert "PLANTED-SECRET" not in out and "[REDACTED:password]" in out


def test_the_tool_is_gated_on_inventory_view_and_matched_by_the_local_model_rule():
    assert tool_permissions.required_permission("azure_advise") == rbac.PERM_INVENTORY_VIEW
    from plugins._ea_ansible.helpers import local_model
    assert local_model.returns_device_data("azure_advise")


def test_it_never_contacts_azure_only_the_public_price_list(tenant, price_list):
    advice(needs=["vm", "app service plan", "vnet"])
    assert {r["path"] for r in price_list.received} == {"/api/retail/prices"}  # and no other host exists in this test
    plugin = Path(azure_advise.__file__).parents[1]

    def imports(path):
        out = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                out |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                out |= {f"{node.module}.{a.name}" for a in node.names}
        return out

    tool, helper = imports(plugin / "tools" / "azure_advise.py"), imports(plugin / "helpers" / "advise.py")
    assert tool <= {"__future__.annotations", "asyncio", "json", "re", "typing.Any", "helpers.tool.Response", "helpers.tool.Tool",
                    "plugins._ea_azure.helpers.advise", "plugins._ea_azure.helpers.store",
                    "plugins._ea_azure.tools.azure_inventory._clean", "plugins._ea_azure.tools.azure_inventory._header"}, tool
    assert helper <= {"__future__.annotations", "collections", "ipaddress", "os", "re", "typing.Any", "typing.NamedTuple",
                      "plugins._ea_azure.helpers.categories", "plugins._ea_azure.helpers.prices", "plugins._ea_azure.helpers.store",
                      "plugins._ea_inventory.helpers.store"}, helper
    users = [p.name for p in (plugin / "helpers").glob("*.py") if "requests" in imports(p) or "import requests" in p.read_text()]
    assert sorted(users) == ["client.py", "cost.py", "live.py", "logs.py", "prices.py"]  # the modules that reach the network (cost: ADR 0060, live reads: ADR 0067, log queries: ADR 0069); advise is none


@pytest.mark.parametrize("pool, size, used, want", [
    ("10.0.0.0/8", 24, [], "10.0.0.0/24"),
    ("10.0.0.0/8", 24, ["10.0.0.0/24", "10.0.1.0/24"], "10.0.2.0/24"),
    ("10.0.0.0/8", 16, ["10.0.0.0/24"], "10.1.0.0/16"),  # the block must be aligned, so a /24 inside 10.0/16 costs the whole /16
    ("10.0.0.0/8", 24, ["10.0.0.5/32"], "10.0.1.0/24"),
    ("10.0.0.0/8", 24, ["10.0.0.0/9", "10.128.0.0/9"], None),
    ("192.168.0.0/16", 24, ["192.168.0.0/16"], None),
    ("192.168.0.0/16", 8, [], None),  # a /8 does not fit in a /16
    ("172.16.0.0/12", 12, ["172.16.0.0/16"], None),
    ("172.16.0.0/12", 13, ["172.16.0.0/16"], "172.24.0.0/13"),
])
def test_first_free_block(pool, size, used, want):
    got = advise._free(net(pool), size, [net(u) for u in used])
    assert (str(got) if got else None) == want
