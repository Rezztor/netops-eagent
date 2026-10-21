"""The waste rules over a stored tenant, the `waste` and `drifted` attention filters, `without: tag:<name>`, the
endpoint and the chat tool. Needs PostgreSQL. The price list is `fake_prices.py` (rows copied from the live answer);
no test reaches Microsoft."""

import ast
import asyncio
import json
from pathlib import Path

import pytest

from helpers import api_policy, rbac
from plugins._ea_azure.api.azure_waste import AzureWaste
from plugins._ea_azure.helpers import prices, shape, store, waste
from plugins._ea_azure.tests import azure_read_tenant as t
from plugins._ea_azure.tests.fake_prices import FakePrices
from plugins._ea_azure.tools import azure_inventory

DISK, VM, PLAN = "microsoft.compute/disks", "microsoft.compute/virtualmachines", "microsoft.web/serverfarms"
PIP, SNAP = "microsoft.network/publicipaddresses", "microsoft.compute/snapshots"
NOW = t.dt.datetime.now(t.dt.timezone.utc)


def tenant_rows():
    off = t.res("vm-off", state="stopped", state_detail="PowerState/stopped",
                props={"size": "Standard_D2s_v5", "os_type": "Linux"})
    on = t.res("vm-on")
    gone = t.res("vm-gone", state="stopped", state_detail="PowerState/deallocated")
    busy = t.res("plan-busy", PLAN, "rg-app", category="web", sku={"name": "P1v3", "tier": "PremiumV3"}, props={"sites": 2})
    plan_sku = {"name": "P1v3", "tier": "PremiumV3"}
    nat_used = t.net("nat-used", "microsoft.network/natgateways")
    return [
        t.res("disk-orphan", DISK, category="storage", sku={"name": "Premium_LRS"},
              props={"disk_state": "Unattached", "size_gb": 100}),
        t.res("disk-aks", DISK, category="storage", props={"disk_state": "Unattached"},
              tags={"kubernetes.io-created-for-pvc-name": "data"}),
        t.res("disk-behind", DISK, category="storage", props={"disk_state": "Reserved"}, relations=[t.rel(gone, "managed_by")]),
        t.res("disk-fine", DISK, category="storage", props={"disk_state": "Attached"}, relations=[t.rel(on, "managed_by")]),
        t.res("disk-dev", DISK, category="storage", sku={"name": "Premium_LRS"}, tags={"Environment": "Dev"},
              props={"disk_state": "Attached"}, relations=[t.rel(busy, "managed_by")]),
        off, on, gone, busy,
        t.res("plan-empty", PLAN, "rg-app", category="web", sku=plan_sku, props={"sites": 0, "linux": True}),
        t.res("plan-free", PLAN, "rg-app", category="web", sku={"name": "F1", "tier": "Free"}, props={"sites": 0}),
        t.res("plan-unknown", PLAN, "rg-app", category="web", sku=plan_sku, props={}),
        t.res("app-off", "microsoft.web/sites", "rg-app", category="web", state="stopped", relations=[t.rel(busy, "plan")]),
        t.res("app-on", "microsoft.web/sites", "rg-app", category="web", relations=[t.rel(busy, "plan")]),
        t.net("nat-idle", "microsoft.network/natgateways"), nat_used,
        t.net("nsg-idle", "microsoft.network/networksecuritygroups"),
        t.net("rt-idle", "microsoft.network/routetables"),
        t.net("pe-dead", "microsoft.network/privateendpoints", props={"connection_states": ["Disconnected"]}),
        t.net("pe-ok", "microsoft.network/privateendpoints", props={"connection_states": ["Approved"]}),
        t.res("sn-nat", t.SUBNET, "rg-net", category="networking", tags={}, relations=[t.rel(nat_used, "nat_gateway")]),
    ]


@pytest.fixture
def loaded(database):
    acct = store.account("Tenant-1", "Contoso")
    subs = [{"id": t.SUB, "name": "Prod", "state": "Enabled"}]
    store.apply_containers(acct, subs, t.groups(t.SUB, ("rg-net", "rg-app", "rg-vm")), NOW)
    t.pull(acct, t.SUB, tenant_rows(), NOW)
    return acct


@pytest.fixture
def fake(monkeypatch):
    with FakePrices() as f:
        monkeypatch.setattr(prices, "HOST", f.url)
        monkeypatch.setattr(prices, "_down_until", 0.0)
        yield f


def fired():
    out: dict[str, set[str]] = {}
    for f in store.waste():
        out.setdefault(f["rule"], set()).add(f["name"])
    return out


def test_each_rule_fires_on_what_it_describes_and_on_nothing_else(loaded):
    assert fired() == {
        "unattached_disk": {"disk-orphan"},  # not the AKS volume, not an attached disk, not a reserved one
        "disk_behind_stopped_vm": {"disk-behind"},
        "stopped_not_deallocated_vm": {"vm-off"},  # a deallocated machine is not billed for compute
        "empty_app_service_plan": {"plan-empty"},  # not Free, not one whose app count is unknown
        "stopped_web_app_on_paid_plan": {"app-off"},
        "unused_nat_gateway": {"nat-idle"}, "unused_nsg": {"nsg-idle"}, "unused_route_table": {"rt-idle"},
        "private_endpoint_disconnected": {"pe-dead"},
        "premium_in_nonprod": {"disk-dev"},
    }


def test_a_finding_has_the_rule_the_evidence_and_the_inventory_date(loaded):
    [f] = [f for f in store.waste() if f["rule"] == "unattached_disk"]
    assert f["evidence"] == "disk state Unattached, 100 GB, Premium_LRS, no owner VM"
    assert f["as_of"] == store.as_of()["last_pull"]["started_at"] and f["subscription"] == "Prod"
    assert "disk-dev" in {x["name"] for x in store.waste({"category": "storage"})}
    assert store.waste({"category": "web"}) and {x["name"] for x in store.waste({"category": "web"})} <= {"plan-empty", "app-off"}
    assert store.waste({"resource_group": "rg-vm"}) and all(x["resource_group"] == "rg-vm" for x in store.waste({"resource_group": "rg-vm"}))


def test_a_rule_whose_property_is_not_stored_is_inert_until_the_allow_list_has_it(loaded, monkeypatch):
    rows = [t.res("pip-idle", PIP, category="networking", sku={"name": "Standard"}, props={"allocation": "Static"}),
            t.res("pip-nic", PIP, category="networking"), t.res("pip-bound", PIP, category="networking")]
    nic = t.res("nic1", "microsoft.network/networkinterfaces", category="networking", relations=[t.rel(rows[1], "public_ip")])
    rows[2]["relations"] = [t.rel("/x/ipconfigurations/c", "pip_user")]
    t.pull(loaded, t.SUB, [*tenant_rows(), *rows, nic], NOW + t.dt.timedelta(hours=1))
    monkeypatch.delitem(shape.RELATION_PATHS, PIP, raising=False)  # not stored (yet)
    assert not waste.active("unused_public_ip") and "unused_public_ip" not in fired()
    monkeypatch.setitem(shape.RELATION_PATHS, PIP, [("pip_user", "ipConfiguration.id"), ("pip_nat", "natGateway.id"),
                                                    ("pip_prefix", "publicIPPrefix.id")])
    assert waste.active("unused_public_ip")
    assert fired()["unused_public_ip"] == {"pip-idle"}  # one used through a card, one through its own link


def test_the_rules_that_read_the_allow_list_work_with_the_paths_it_stores_today(loaded):
    """No patching: the real `shape.ALLOW` and `RELATION_PATHS`, so a renamed path or key turns a rule inert here."""
    net = "microsoft.network/"
    cx = t.res("cx", net + "connections", category="networking", relations=[t.rel("/gw-used", "vnet_gateway")])
    gw = lambda n, **kw: t.res(n, net + "virtualnetworkgateways", category="networking", props={"gateway_type": "Vpn"}, **kw)  # noqa: E731
    used = gw("gw-used")
    used["native_id"] = "/gw-used"
    rows = [gw("gw-idle"), used, t.res("gw-p2s", net + "virtualnetworkgateways", category="networking",
                                       props={"vpn_client_pool": ["172.16.0.0/24"]}), cx,
            t.res("lb-idle", net + "loadbalancers", category="networking"),
            t.res("lb-busy", net + "loadbalancers", category="networking", props={"backend_ip_configs": ["/nic/ipconfig1"]}),
            t.res("ddos-idle", net + "ddosprotectionplans", category="networking"),
            t.res("ddos-busy", net + "ddosprotectionplans", category="networking", relations=[t.rel("/v", "protects")]),
            t.res("zone-idle", net + "privatednszones", category="networking", props={"vnet_links": 0}),
            t.res("zone-busy", net + "privatednszones", category="networking", props={"vnet_links": 2}),
            t.res("zone-unknown", net + "privatednszones", category="networking")]
    t.pull(loaded, t.SUB, [*tenant_rows(), *rows], NOW + t.dt.timedelta(hours=1))
    got = fired()
    assert got["gateway_without_connection"] == {"gw-idle"}
    assert got["lb_without_backend"] == {"lb-idle"} and got["ddos_plan_unused"] == {"ddos-idle"}
    assert got["private_dns_zone_unlinked"] == {"zone-idle"}  # a count that was never stored is not a zero


def test_an_old_snapshot_needs_its_date_to_be_stored(loaded, monkeypatch):
    monkeypatch.setitem(shape.ALLOW, SNAP, {"created": "timeCreated", "size_gb": "diskSizeGB"})
    old, new = [(NOW - t.dt.timedelta(days=d)).isoformat() for d in (120, 10)]
    t.pull(loaded, t.SUB, [*tenant_rows(), t.res("snap-old", SNAP, props={"created": old, "size_gb": 32}),
                           t.res("snap-new", SNAP, props={"created": new}), t.res("snap-undated", SNAP)],
           NOW + t.dt.timedelta(hours=1))
    assert fired()["old_snapshot"] == {"snap-old"}
    assert "120 days ago, 32 GB" in [f for f in store.waste() if f["name"] == "snap-old"][0]["evidence"]


def test_attention_waste_and_drifted_and_without_a_tag(loaded):
    waste_names = {f["name"] for f in store.waste()}
    found, total = store.search({"attention": "waste"}, 100)
    assert {r["name"] for r in found} == waste_names and total == len(waste_names)
    assert store.counts({"attention": "waste", "category": "networking"}, "state")[1] == 4
    assert store.search({"attention": "drifted"})[1] == 0  # nothing is managed yet
    assert {"waste", "drifted"} <= set(store.ATTENTION)
    owned = {r["name"] for r in store.search({"without": "tag:owner"}, 100)[0]}
    assert "disk-orphan" in owned and "sn-nat" not in owned  # a subnet cannot carry a tag
    assert store.search({"without": "tag:ENV"}, 100)[1] == store.search({"without": "tag:env"}, 100)[1]
    assert store.search({"without": "tag:Environment"}, 100)[1] == store.search({"without": "tag:environment"}, 100)[1]
    for bad in ("tag:", "tag", "owner"):
        with pytest.raises(store.BadFilter, match="tag:<name>"):
            store.search({"without": bad})


def test_the_endpoint_prices_what_the_list_can_and_totals_it(loaded, fake):
    assert api_policy.required_permission_for("plugins/_ea_azure/azure_waste") == rbac.PERM_INVENTORY_VIEW
    out = asyncio.new_event_loop().run_until_complete(AzureWaste(app=None, thread_lock=None).process({}, None))
    by = {f["name"]: f for f in out["findings"] if f["rule"] in ("unattached_disk", "stopped_not_deallocated_vm", "empty_app_service_plan")}
    assert out["ok"] and out["as_of"] == store.as_of()["last_pull"]["started_at"]
    assert by["disk-orphan"]["usd_month"] == 21.5 and by["vm-off"]["usd_month"] == 86.14 and by["plan-empty"]["usd_month"] == 139.43
    assert out["total_usd_month"] == round(21.5 + 86.14 + 139.43, 2) and out["priced"] == 3
    assert out["unpriced"] == len(out["findings"]) - 3 and all("_price" not in f for f in out["findings"])
    assert len({r["query"]["$filter"] for r in fake.received}) == len(fake.received)  # one ask per SKU, then the cache


def test_without_a_price_list_the_findings_stand_and_say_so(loaded, monkeypatch):
    with FakePrices() as f:
        f.status = 500
        monkeypatch.setattr(prices, "HOST", f.url)
        monkeypatch.setattr(prices, "_down_until", 0.0)
        out = asyncio.new_event_loop().run_until_complete(AzureWaste(app=None, thread_lock=None).process({"subscription": "Prod"}, None))
    assert out["ok"] and out["findings"] and out["priced"] == 0 and out["total_usd_month"] == 0
    assert not any("usd_month" in f for f in out["findings"])


def test_chat_asks_for_waste_through_the_inventory_tool(loaded, fake):
    def ask(**args):
        tool = azure_inventory.AzureInventory.__new__(azure_inventory.AzureInventory)
        tool.args = args
        return asyncio.new_event_loop().run_until_complete(tool.execute()).message

    head, body = ask(attention="waste").split("\n\n", 1)
    out = json.loads(body)
    assert head.startswith("Azure inventory as of") and out["total"] == len(store.waste())
    assert out["total_usd_month"] == 247.07 and "a pointer to look, not a verdict" in out["note"].lower()
    first = out["findings"][0]
    assert first["rule"] == "disk_behind_stopped_vm" and {"title", "evidence", "name"} <= set(first)
    assert json.loads(ask(attention="waste", category="networking").split("\n\n", 1)[1])["total"] == 4
    assert json.loads(ask(attention="waste", count_by="category").split("\n\n", 1)[1])["total"] == out["total"]
    assert ask(attention="nope").startswith("attention must be one of") and "waste" in ask(attention="nope")
    assert json.loads(ask(without="tag:owner", count_by="state").split("\n\n", 1)[1])["total"] > 0
    assert ask(without="tag:").startswith("without must be one of")
    assert "tag:<name>" in ask(without="nope")


def test_importing_the_rules_does_not_import_the_price_client():
    top = [n for n in ast.parse(Path(waste.__file__).read_text()).body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert {a.name for n in top for a in n.names} == {"annotations", "datetime", "namedtuple", "Any", "Callable", "shape"}
