"""A small already-shaped tenant, stored through `store` with no network, for the read API and tool tests.

Two pulls of Prod (the second one adds, changes and removes) and one of Dev, which the second pull did not read.
"""

from __future__ import annotations

import datetime as dt

from plugins._ea_azure.helpers import store

SUB, SUB2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
VNET, SUBNET = "microsoft.network/virtualnetworks", "microsoft.network/virtualnetworks/subnets"
OPEN = {"name": "ssh", "priority": 100, "direction": "Inbound", "access": "Allow", "protocol": "Tcp",
        "sources": ["*"], "source_ports": ["*"], "destinations": ["*"], "destination_ports": ["22"]}
CLOSED = {**OPEN, "name": "lan", "sources": ["10.0.0.0/8"]}


def res(name, type="microsoft.compute/virtualmachines", rg="rg-vm", sub=SUB, category="compute", **kw):
    return {"native_id": f"/subscriptions/{sub}/resourcegroups/{rg}/providers/{type}/{name}".lower(), "name": name,
            "type": type, "kind": "", "region": "uaenorth", "subscription": sub, "resource_group": rg, "sku": {},
            "tags": {"env": "prod"}, "props": {}, "state": "ok", "state_detail": "", "category": category,
            "relations": [], "children": [], **kw}


def rel(target, kind, **props):
    return {"kind": kind, "to_native_id": target["native_id"] if isinstance(target, dict) else target, "props": props}


def net(name, type, **kw):
    return res(name, type, rg="rg-net", category="networking", **kw)


def prod(second=False):
    rt = net("rt1", "microsoft.network/routetables", props={"routes": [
        {"name": "default", "prefix": "0.0.0.0/0", "next_hop_type": "VirtualAppliance",
         "next_hop_ip": "10.0.254.4" if not second else "10.0.254.5"}]})
    nsg = net("nsg1", "microsoft.network/networksecuritygroups", props={"rules": [OPEN, CLOSED]})
    nsg2 = net("nsg2", "microsoft.network/networksecuritygroups", props={"rules": [CLOSED]})
    pip = net("pip1", "microsoft.network/publicipaddresses", props={"ip": "20.1.1.1"})
    web = res("sn-web", SUBNET, "rg-net", category="networking", tags={}, props={"address_prefixes": ["10.0.1.0/24"]},
              relations=[rel(rt, "route_table"), rel(nsg, "nsg")])
    data = res("sn-data", SUBNET, "rg-net", category="networking", tags={}, relations=[rel(nsg2, "nsg")])
    appsn = res("sn-app", SUBNET, "rg-net", category="networking", tags={},
                relations=[rel(rt, "route_table"), rel(nsg2, "nsg")])
    vnet = net("vnet1", VNET, tags={"env": "prod", "owner": "netops"}, props={"address_space": ["10.0.0.0/16"]},
               relations=[rel("/subscriptions/other/resourcegroups/x/providers/microsoft.network/virtualnetworks/far",
                              "peering", name="p", state="Connected")], children=[web, data, appsn])
    nic1 = res("nic1", "microsoft.network/networkinterfaces", category="networking",
               relations=[rel(web, "subnet"), rel(pip, "public_ip"), rel(nsg, "nsg")])
    nic2 = res("nic2", "microsoft.network/networkinterfaces", category="networking", relations=[rel(data, "subnet")])
    vm1 = res("vm1", state="stopped", state_detail="PowerState/deallocated", relations=[rel(nic1, "nic")])
    vm2 = res("vm2", state="stopped" if second else "running", relations=[rel(nic2, "nic")])
    plan = res("plan1", "microsoft.web/serverfarms", "rg-app", category="web")
    app1 = res("app1", "microsoft.web/sites", "rg-app", category="web", kind="app", state="running",
               props={"public_network_access": "Enabled", "https_only": True},
               relations=[rel(plan, "plan"), rel(appsn, "vnet_integration")])
    app2 = res("app2", "microsoft.web/sites", "rg-app", category="web", kind="functionapp,linux", state="stopped",
               relations=[rel(plan, "plan")])
    open_store = res("stor1", "microsoft.storage/storageaccounts", "rg-app", category="storage", tags={},
                     props={"public_network_access": "Enabled", "default_network_action": "Allow",
                            "allow_blob_public_access": True})
    shut_store = res("stor2", "microsoft.storage/storageaccounts", "rg-app", category="storage",
                     props={"public_network_access": "Enabled", "default_network_action": "Deny"})
    vault = res("kv1", "microsoft.keyvault/vaults", "rg-app", category="identity_security",
                props={"public_network_access": "Enabled", "default_network_action": "Deny"})
    cont = res("ca1", "microsoft.app/containerapps", "rg-app", category="containers", props={"ingress_external": True})
    everything = [vnet, rt, nsg, nsg2, pip, nic1, nic2, vm1, vm2, plan, app1, app2, open_store, shut_store, vault, cont]
    if second:  # added newvm, removed oldstor, changed rt1 (routes) and vm2 (state)
        return [*everything, res("newvm")]
    return [*everything, res("oldstor", "microsoft.storage/storageaccounts", "rg-app", category="storage")]


def dev():
    return [res("vm3", rg="rg-dev", sub=SUB2), res("app3", "microsoft.web/sites", "rg-dev", sub=SUB2, category="web",
                                                    kind="app", state="running")]


def groups(sub, names):
    return [{"id": f"/subscriptions/{sub}/resourceGroups/{n}", "subscription": sub, "name": n} for n in names]


def pull(acct, sub, shaped, now, *, status="succeeded", counts=None):
    run = store.inv.start_run("azure", "test")
    store.apply_subscription(acct, sub, shaped, run_id=run, now=now)
    store.inv.finish_run(run, status, counts or {})
    return run


def load():
    """Returns the account id. Pull 1 reads both subscriptions a week ago; pull 2 reads only Prod, now."""
    acct = store.account("Tenant-1", "Contoso")
    now = dt.datetime.now(dt.timezone.utc)
    week = now - dt.timedelta(days=7)
    subs = [{"id": SUB, "name": "Prod", "state": "Enabled"}, {"id": SUB2, "name": "Dev", "state": "Enabled"}]
    store.apply_containers(acct, subs, groups(SUB, ("rg-net", "rg-app", "rg-vm")) + groups(SUB2, ("rg-dev",)), week)
    pull(acct, SUB, prod(), week)
    pull(acct, SUB2, dev(), week)
    store.apply_containers(acct, subs, groups(SUB, ("rg-net", "rg-app", "rg-vm")) + groups(SUB2, ("rg-dev",)), now)
    pull(acct, SUB, prod(second=True), now, status="partial", counts={"per_subscription": [
        {"id": SUB, "name": "Prod", "status": "ok"}, {"id": SUB2, "name": "Dev", "status": "failed"}]})
    return acct
