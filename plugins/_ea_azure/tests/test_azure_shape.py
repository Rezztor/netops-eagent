"""The allow-list: what is kept from a Resource Graph row, and what never is. No database, no network."""

import json

import pytest

from plugins._ea_azure.helpers import shape
from plugins._ea_azure.tests.fake_azure import FakeAzure
from plugins._ea_azure.tests.fixtures.tenant import SUB_PROD, hand_tenant


@pytest.fixture(scope="module")
def shaped():
    return {s["name"]: s for r in hand_tenant()["resources"] if (s := shape.reduce(r))}


def keys(shaped, name):
    return set(shaped[name]["props"])


def test_each_type_in_the_props_table_keeps_exactly_the_listed_keys(shaped):
    assert keys(shaped, "vnet-prod-hub") == {"address_space", "dns_servers", "peerings"}
    assert keys(shaped, "rt-app") == {"bgp_propagation", "routes"}
    assert keys(shaped, "nsg-app") == {"rules"}
    assert keys(shaped, "pip-lb-prod") == {"ip", "allocation", "version", "fqdn"}
    assert keys(shaped, "nic-vm-prod-1") == {"ip_configs", "ip_forwarding", "accelerated"}
    assert keys(shaped, "vm-prod-1") == {"size", "os_type", "image", "zones"}
    assert keys(shaped, "app-portal-prod") == {"host_names", "default_host_name", "https_only", "public_network_access",
                                               "outbound_ips", "availability_state", "runtime"}
    assert keys(shaped, "asp-prod") == {"workers", "sites", "linux", "zone_redundant", "status"}
    assert keys(shaped, "stprodlogs001") == {"access_tier", "public_network_access", "allow_blob_public_access",
                                             "default_network_action", "min_tls", "https_only"}
    assert keys(shaped, "sql-prod-01") == {"version", "public_network_access", "host_name"}
    assert keys(shaped, "kv-prod-001") == {"sku", "rbac", "public_network_access", "default_network_action",
                                           "soft_delete", "purge_protection"}
    assert keys(shaped, "aks-prod") == {"version", "private_cluster", "fqdn", "node_pools"}
    assert keys(shaped, "ca-worker") == {"ingress_external", "fqdn"}
    assert keys(shaped, "redis-prod") == {"version", "public_network_access", "host_name"}
    assert keys(shaped, "cosmos-prod") == {"public_network_access", "host_name"}


def test_values_are_read_from_the_right_place(shaped):
    nsg = shaped["nsg-app"]["props"]["rules"][0]
    assert nsg == {"name": "allow-ssh-any", "priority": 100, "direction": "Inbound", "access": "Allow", "protocol": "Tcp",
                   "sources": ["*"], "source_ports": ["*"], "destinations": ["*"], "destination_ports": ["22"]}
    assert shaped["rt-app"]["props"]["bgp_propagation"] is True and shaped["rt-dev"]["props"]["bgp_propagation"] is False
    assert shaped["rt-app"]["props"]["routes"][0] == {"name": "to-firewall", "prefix": "0.0.0.0/0",
                                                      "next_hop_type": "VirtualAppliance", "next_hop_ip": "10.10.0.4"}
    assert shaped["app-portal-prod"]["props"]["outbound_ips"] == ["20.1.1.1", "20.1.1.2"]
    assert shaped["vm-prod-1"]["props"]["image"]["publisher"] == "canonical" and shaped["vm-prod-1"]["props"]["zones"] == ["1"]
    assert shaped["nic-vm-prod-1"]["props"]["ip_configs"] == [{"name": "ipconfig1", "private_ip": "10.10.1.4", "primary": True}]
    assert shaped["app-portal-prod"]["sku"] == {} and shaped["asp-prod"]["sku"] == {"name": "P1v3", "tier": "PremiumV3", "capacity": 2}


def test_an_unknown_type_keeps_the_envelope_only(shaped):
    w = shaped["widget-1"]
    assert (w["props"], w["category"], w["label"], w["state"]) == ({}, "other", "widgets", "ok")
    assert w["native_id"].endswith("/widgets/widget-1") and w["subscription"] and w["resource_group"] == "rg-shared"


def test_the_forbidden_paths_never_appear_in_the_allow_list():
    inside = ("osprofile", "customdata", "userdata", "appsettings", "connectionstring", "environmentvariables", "secret",
              "registries", "linuxprofile", "windowsprofile", "serviceprincipalprofile", "identityprofile",
              "typeproperties", "administratorlogin", "sharedkey", "password")
    exact = ("settings", "env", "definition", "parameters", "description")
    for path in shape.paths():
        for segment in path.lower().replace("[]", "").replace("|", ".").split("."):
            assert not any(b in segment for b in inside) and segment not in exact, path
            assert "key" not in segment or "keyvault" in segment, path


def test_the_forbidden_whole_objects_and_urls_are_never_listed_even_when_a_neighbour_path_is():
    """vpnClientConfiguration holds RADIUS and certificate data (only its address pool is allowed); an application
    gateway's certificate blocks and a snapshot's sourceUri (a storage URL) are never read."""
    every = [p.lower().replace("[]", "") for p in shape.paths()]
    for path in every:
        assert path != "vpnclientconfiguration" and path != "creationdata.sourceuri", path
        assert not path.startswith(("sslcertificates", "authenticationcertificates", "trustedrootcertificates")), path
        assert not path.startswith("vpnclientconfiguration.") or path == "vpnclientconfiguration.vpnclientaddresspool.addressprefixes", path
    assert "vpnclientconfiguration.vpnclientaddresspool.addressprefixes" in every
    assert "creationdata.sourceresourceid" in every


def _row(type_, name="x", **props):
    return {"id": f"/subscriptions/s/resourceGroups/g/providers/{type_}/{name}", "type": type_, "name": name,
            "subscriptionId": "s", "resourceGroup": "g", "properties": props}


def test_the_waste_checks_paths_are_stored_and_what_sits_beside_them_is_not():
    snap = shape.reduce(_row("microsoft.compute/snapshots", diskSizeGB=128, timeCreated="2026-01-01T00:00:00Z", incremental=True,
                             creationData={"sourceResourceId": "/subscriptions/s/resourceGroups/g/providers/Microsoft.Compute/disks/d",
                                           "sourceUri": "https://acct.blob.core.windows.net/c/v.vhd?sig=PLANTED-SECRET-1"}))
    assert snap["props"] == {"size_gb": 128, "time_created": "2026-01-01T00:00:00Z", "incremental": True}
    assert [(r["kind"], r["to_native_id"].rsplit("/", 1)[-1]) for r in snap["relations"]] == [("source", "d")]
    assert "PLANTED" not in json.dumps(snap)

    gw = shape.reduce(_row("microsoft.network/virtualnetworkgateways", sku={"name": "VpnGw1"}, gatewayType="Vpn",
                           vpnClientConfiguration={"vpnClientAddressPool": {"addressPrefixes": ["172.16.0.0/24"]},
                                                   "radiusServerSecret": "PLANTED-SECRET-2", "vpnClientRootCertificates": [{"name": "r"}]}))
    assert gw["props"]["vpn_client_pool"] == ["172.16.0.0/24"] and "PLANTED" not in json.dumps(gw)

    agw = shape.reduce(_row("microsoft.network/applicationgateways", sku={"name": "WAF_v2", "tier": "WAF_v2", "capacity": 2},
                            sslCertificates=[{"name": "c", "properties": {"data": "PLANTED-SECRET-3"}}],
                            backendAddressPools=[{"properties": {"backendAddresses": [{"ipAddress": "10.0.0.4"}, {"fqdn": "a.example.com"}]}}]))
    assert agw["props"] == {"sku_name": "WAF_v2", "sku_tier": "WAF_v2", "sku_capacity": 2,
                            "backend_addresses": ["10.0.0.4", "a.example.com"]}

    lb = shape.reduce(_row("microsoft.network/loadbalancers", inboundNatRules=[{"name": "ssh"}],
                           backendAddressPools=[{"properties": {"backendIPConfigurations": [{"id": "/x/nic/ipConfigurations/1"}]}}]))
    assert lb["props"] == {"backend_ip_configs": ["/x/nic/ipConfigurations/1"], "inbound_nat_rules": ["ssh"]}
    empty = shape.reduce(_row("microsoft.network/loadbalancers", backendAddressPools=[], inboundNatRules=[]))
    assert empty["props"] == {}

    pip = shape.reduce(_row("microsoft.network/publicipaddresses", ipAddress="1.2.3.4",
                            ipConfiguration={"id": "/x/networkInterfaces/n/ipConfigurations/c"}, natGateway={"id": "/x/natGateways/n"}))
    assert {r["kind"] for r in pip["relations"]} == {"ip_configuration", "nat_gateway_ip"}
    conn = shape.reduce(_row("microsoft.network/connections", virtualNetworkGateway1={"id": "/x/g1"}, localNetworkGateway2={"id": "/x/l2"}))
    assert {(r["kind"], r["to_native_id"]) for r in conn["relations"]} == {("vnet_gateway", "/x/g1"), ("local_gateway", "/x/l2")}
    assert shape.reduce(_row("microsoft.network/privatednszones", numberOfVirtualNetworkLinks=0, numberOfRecordSets=2))["props"] == {
        "vnet_links": 0, "record_sets": 2}


def test_zones_are_stored_for_every_type_and_a_long_list_is_cut():
    row = _row("microsoft.storage/storageaccounts")
    row["zones"] = ["1", "2"]
    assert shape.reduce(row)["props"] == {"zones": ["1", "2"]}
    many = _row("microsoft.network/loadbalancers", inboundNatRules=[{"name": f"r{i}"} for i in range(500)])
    assert len(shape.reduce(many)["props"]["inbound_nat_rules"]) == 100


def test_azures_own_rows_keep_their_listed_fields_and_nothing_else():
    t = hand_tenant()
    out = {k: [shape.signal(n, r) for r in t[k]] for k, n in (("advisor", "advisor"), ("policy", "policy"), ("changes", "change"), ("health", "health"))}
    advisor = [a for a in out["advisor"] if a]
    assert len(advisor) == 3 and out["advisor"][2] is None  # the dismissed one is dropped
    vm = advisor[0]
    assert vm["target"].endswith("/virtualmachines/vm-prod-2") and vm["at"].year == 2026
    assert vm["data"] == {"category": "Cost", "impact": "High", "risk": "Warning", "problem": "Right-size or shutdown underutilized virtual machines",
                          "solution": "Right-size or shutdown underutilized virtual machines", "impacted_type": "Microsoft.Compute/virtualMachines",
                          "impacted_name": "vm-prod-2", "type_id": "e10b1381-5f0a-47ff-8c7b-37bd4eaffdd9", "recommendation": "RightSizing",
                          "current_sku": "Standard_D4s_v5", "target_sku": "Standard_D2s_v5", "lookback_days": "7", "savings": "86.4",
                          "annual_savings": "1036.8", "currency": "USD"}   # deploymentId and maxCpuP95 are not on the list
    assert advisor[2]["target"] == f"/subscriptions/{SUB_PROD}"                # a subscription-level recommendation
    pol = out["policy"][0]
    assert pol["data"] == {"state": "NonCompliant", "assignment": "require-https", "definition": "404c3081-a854-4457-ae30-26a93ef643f9",
                           "action": "audit", "category": "Storage"}
    assert "PLANTED" not in json.dumps(out, default=str)
    health = [h for h in out["health"]]
    assert [h["data"].get("state") for h in health] == ["Available", "Unavailable", None]
    assert health[2]["data"]["reason"] == "Unexpected host failure" and health[0]["target"].endswith("/virtualmachines/vm-prod-1")
    assert shape.signal("advisor", {"id": "x", "properties": {}}) is None and shape.signal("policy", "nope") is None


def test_a_change_record_shows_values_only_for_paths_the_plugin_stores_and_says_who_changed_it():
    site, vm = (shape.signal("change", r) for r in hand_tenant()["changes"])
    assert site["data"]["changed_by"] == "alice@example.com" and site["data"]["changed_by_type"] == "User"
    assert site["data"]["client"] == "Azure Portal" and site["data"]["count"] == 4 and site["at"].isoformat() == "2026-10-02T08:30:00.347000+00:00"
    by_path = {c["path"]: c for c in site["data"]["changes"]}
    assert by_path["properties.httpsOnly"] == {"path": "properties.httpsOnly", "kind": "Update", "old": "False", "new": "True"}
    hidden = by_path["properties.siteConfig.appSettings[].value"]
    assert hidden == {"path": "properties.siteConfig.appSettings[].value", "kind": "Update", "hidden": True}   # index folded, value dropped
    assert by_path["tags.owner"]["new"] == "platform" and by_path["tags.db_password"]["new"] == "[masked]"
    assert vm["data"]["changes"][0]["old"] == "Standard_D2s_v5" and vm["data"]["changes"][0]["new"] == "Standard_D4s_v5"
    assert "PLANTED" not in json.dumps([site, vm], default=str)


def test_a_change_to_a_network_s_subnet_and_to_a_secret_looking_value_are_handled_by_path():
    def change(type_, bag):
        return shape.signal("change", {"properties": {"targetResourceId": "/subscriptions/s/resourcegroups/g/providers/x/y/z",
                                                      "targetResourceType": type_, "changes": bag}})["data"]["changes"]
    sub = change("Microsoft.Network/virtualNetworks", {"properties.subnets[2].properties.addressPrefix": {"previousValue": "10.0.1.0/24", "newValue": "10.0.2.0/24"}})
    assert sub[0]["old"] == "10.0.1.0/24" and sub[0]["new"] == "10.0.2.0/24"
    conn = change("Microsoft.Storage/storageAccounts", {"properties.minimumTlsVersion": {"previousValue": "TLS1_0", "newValue": "Server=tcp:db;Password=PLANTED-SECRET-9;"}})
    assert conn[0]["old"] == "TLS1_0" and conn[0]["new"] == "[masked]"
    obj = change("Microsoft.Storage/storageAccounts", {"properties.minimumTlsVersion": {"newValue": {"a": "PLANTED-SECRET-8"}}})
    assert obj[0].get("hidden") and "PLANTED" not in json.dumps(obj)
    # Create and Delete records carry no `changes` object
    assert shape.signal("change", {"properties": {"targetResourceId": "/a/b", "changeType": "Create"}})["data"]["changes"] == []


def test_subnets_become_child_rows_with_their_relations(shaped):
    hub = shaped["vnet-prod-hub"]
    kids = {k["name"]: k for k in hub["children"]}
    assert set(kids) == {"snet-app", "snet-data", "snet-egress"}
    app = kids["snet-app"]
    assert app["type"] == shape.SUBNET and app["category"] == "networking" and app["label"] == "Subnet"
    assert app["props"] == {"address_prefixes": ["10.10.1.0/24"], "delegations": ["Microsoft.Web/serverFarms"],
                            "service_endpoints": ["Microsoft.Storage"]}
    assert {r["kind"]: r["to_native_id"].rsplit("/", 1)[-1] for r in app["relations"]} == {"route_table": "rt-app", "nsg": "nsg-app"}
    assert kids["snet-data"]["relations"] == [] and kids["snet-egress"]["relations"][0]["kind"] == "nat_gateway"
    assert app["native_id"] == app["native_id"].lower() and app["region"] == hub["region"]


def test_a_peering_to_a_network_we_cannot_read_keeps_its_target(shaped):
    peers = {r["props"]["name"]: r for r in shaped["vnet-prod-hub"]["relations"] if r["kind"] == "peering"}
    assert set(peers) == {"hub-to-dev", "hub-to-partner"}
    far = peers["hub-to-partner"]
    assert far["to_native_id"].startswith("/subscriptions/99999999") and far["props"]["state"] == "Connected"
    assert {p["name"] for p in shaped["vnet-prod-hub"]["props"]["peerings"]} == set(peers)


def test_relations_of_the_other_types(shaped):
    def rel(name):
        return {(r["kind"], r["to_native_id"].rsplit("/", 1)[-1]) for r in shaped[name]["relations"]}

    assert rel("nic-vm-prod-1") == {("subnet", "snet-app"), ("public_ip", "pip-vm-prod-1"), ("nsg", "nsg-app")}
    assert rel("vm-prod-1") == {("nic", "nic-vm-prod-1")}
    assert rel("app-portal-prod") == {("plan", "asp-prod"), ("vnet_integration", "snet-app")}
    assert rel("app-api-prod") == {("plan", "asp-prod")}
    assert rel("lb-prod") == {("public_ip", "pip-lb-prod")} and rel("pe-sql") == {("subnet", "snet-data")}


@pytest.mark.parametrize("type_, props, expected", [
    ("microsoft.compute/virtualmachines", {"extended": {"instanceView": {"powerState": {"code": "PowerState/running"}}}}, ("running", "PowerState/running")),
    ("microsoft.compute/virtualmachines", {"extended": {"instanceView": {"powerState": {"code": "PowerState/deallocated"}}}}, ("stopped", "PowerState/deallocated")),
    ("microsoft.compute/virtualmachines", {"extended": {"instanceView": {"powerState": {"code": "PowerState/stopping"}}}}, ("stopped", "PowerState/stopping")),
    ("microsoft.compute/virtualmachines", {"extended": {"instanceView": {"powerState": {"code": "PowerState/starting"}}}}, ("ok", "PowerState/starting")),
    ("microsoft.compute/virtualmachines", {}, ("unknown", "")),
    ("microsoft.web/sites", {"state": "Stopped"}, ("stopped", "Stopped")),
    ("microsoft.web/sites", {"state": "Running"}, ("running", "Running")),
    ("microsoft.containerservice/managedclusters", {"powerState": {"code": "Stopped"}}, ("stopped", "Stopped")),
    ("microsoft.sql/servers/databases", {"status": "Online"}, ("running", "Online")),
    ("microsoft.sql/servers/databases", {"status": "Paused"}, ("stopped", "Paused")),
    ("microsoft.sql/servers/databases", {"status": "Offline"}, ("stopped", "Offline")),
    ("microsoft.network/applicationgateways", {"operationalState": "Stopped"}, ("stopped", "Stopped")),
    ("microsoft.app/containerapps", {"runningStatus": "Running"}, ("running", "Running")),
    ("microsoft.logic/workflows", {"state": "Disabled"}, ("stopped", "Disabled")),
    ("microsoft.storage/storageaccounts", {"provisioningState": "Succeeded"}, ("ok", "")),
    ("microsoft.storage/storageaccounts", {"provisioningState": "Failed"}, ("failed", "Failed")),
    ("microsoft.web/sites", {"state": "Running", "provisioningState": "Canceled"}, ("failed", "Canceled")),
])
def test_state_rules(type_, props, expected):
    assert shape.state_of(type_, "", props) == expected


def test_tags_are_masked_by_name_or_by_value():
    out = shape.tags({
        "env": "prod", "db_password": "hunter2", "ApiToken": "abc", "keyvault": "kv1", "owner": None,
        "dsn": "Server=tcp:db;User Id=a;Password=pw;", "storage": "DefaultEndpointsProtocol=https;AccountKey=zzz",
        "note": "password: letmein", "long": "x" * 400, "url": "https://u:p@host/db"})
    assert out["env"] == "prod" and out["owner"] == "" and len(out["long"]) == 256
    for name in ("db_password", "ApiToken", "keyvault", "dsn", "storage", "note", "url"):
        assert out[name] == "[masked]", name
    assert shape.tags(None) == {} and shape.tags(["a"]) == {}


def test_a_path_that_lands_on_an_object_yields_nothing(monkeypatch):
    monkeypatch.setitem(shape.ALLOW, "microsoft.contoso/widgets", {"whole": "inner", "ok": "inner.name"})
    row = {"id": "/subscriptions/s/resourceGroups/g/providers/Microsoft.Contoso/widgets/w", "type": "Microsoft.Contoso/widgets",
           "name": "w", "properties": {"inner": {"name": "n", "token": "t"}}}
    assert shape.reduce(row)["props"] == {"ok": "n"}


def test_a_row_without_id_or_type_is_dropped():
    assert shape.reduce({"name": "x"}) is None and shape.reduce({"id": "/a", "type": ""}) is None and shape.reduce("x") is None


def test_the_runtime_comes_only_from_the_config_rows():
    tenant = hand_tenant()
    items = [shape.reduce(r) for r in tenant["resources"]]
    shape.merge_runtime(items, tenant["site_config"] + [{"id": "/nowhere/config/web", "properties": {"linuxFxVersion": "X"}}])
    runtime = {i["name"]: i["props"]["runtime"] for i in items if i["type"] == shape.SITES}
    assert runtime == {"app-portal-prod": "NODE|20-lts", "app-api-prod": "", "func-jobs-prod": "", "app-portal-dev": ""}
    assert "appSettings" not in json.dumps(items)


def test_nothing_planted_survives_the_shape():
    fake = FakeAzure(hand_tenant())
    markers = fake.plant_secrets()
    blob = json.dumps([shape.reduce(r) for r in fake.tenant["resources"]] + [shape.container(r) for r in fake.tenant["containers"]])
    assert len(markers) >= 7 and not [m for m in markers if m in blob]
    assert "azureuser" not in blob and "ignore me" not in blob


def test_containers_carry_management_groups_and_tags():
    containers = [shape.container(r) for r in hand_tenant()["containers"]]
    sub = next(c for c in containers if c["id"].count("/") == 2)
    assert sub["props"] == {"management_groups": [{"id": "mg-corp", "name": "Corp"}]}
    group = next(c for c in containers if "/resourcegroups/" in c["id"])
    assert group["tags"] == {"owner": "netops"} and group["subscription"] == group["id"].split("/")[2]
