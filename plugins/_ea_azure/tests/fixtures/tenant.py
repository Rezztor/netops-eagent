"""Made-up Azure tenants for the stand-in. Rows are shaped like Resource Graph's `objectArray` answers.

`hand_tenant()`: three subscriptions and about forty resources of the main types, with the cases the plugin has to
get right (a peering that points outside the tenant, an open security rule, a subnet with no route table, one machine
deallocated, a type the plugin has never heard of). `generated_tenant()`: many resources, for paging and timing.
Both are plain dicts a test may edit: `{"tenant_id", "subscriptions", "containers", "resources", "site_config",
"advisor", "policy", "changes", "health"}`; the last four are Azure's own tables (`advisorresources`, `policyresources`,
`resourcechanges`, `healthresources`).
"""

from __future__ import annotations

TENANT_ID = "7c1e2a40-0000-4000-8000-3f5b9d1e6a21"
CLIENT_ID = "b93d5f08-0000-4000-8000-a4c27e118c53"
SUB_PROD = "11111111-1111-4111-8111-111111111111"
SUB_DEV = "22222222-2222-4222-8222-222222222222"
SUB_SHARED = "33333333-3333-4333-8333-333333333333"
OUTSIDE_SUB = "99999999-9999-4999-8999-999999999999"  # a network in a tenant we cannot read


def rid(sub: str, group: str, provider: str, *tail: str) -> str:
    return f"/subscriptions/{sub}/resourceGroups/{group}/providers/{provider}/" + "/".join(tail)


def row(sub, group, provider, kind_path, name, location="westeurope", **extra):
    """One Resource Graph row. `kind_path` is the type after the provider (`virtualNetworks`)."""
    return {
        "id": rid(sub, group, provider, kind_path, name), "name": name, "type": f"{provider}/{kind_path}".lower(),
        "kind": "", "location": location, "resourceGroup": group, "subscriptionId": sub, "sku": None, "tags": None,
        "managedBy": "", "zones": None, "properties": {}, **extra,
    }


def _vnet(sub, group, name, space, subnets, peerings=()):
    return row(
        sub, group, "Microsoft.Network", "virtualNetworks", name, tags={"env": "prod" if sub == SUB_PROD else "dev"},
        properties={
            "provisioningState": "Succeeded", "addressSpace": {"addressPrefixes": [space]},
            "dhcpOptions": {"dnsServers": ["10.10.0.4"]} if sub == SUB_PROD else {},
            "subnets": [
                {"name": n, "id": rid(sub, group, "Microsoft.Network", "virtualNetworks", name, "subnets", n), "properties": p}
                for n, p in subnets
            ],
            "virtualNetworkPeerings": [
                {"name": n, "properties": {
                    "peeringState": "Connected", "peeringSyncLevel": "FullyInSync", "remoteVirtualNetwork": {"id": remote},
                    "remoteAddressSpace": {"addressPrefixes": [space2]}, "allowForwardedTraffic": True,
                    "allowGatewayTransit": False, "useRemoteGateways": False}}
                for n, remote, space2 in peerings
            ],
        },
    )


def _nic(sub, group, name, subnet_id, public_ip=None, nsg=None, private_ip="10.10.1.4", vm=None):
    props = {
        "provisioningState": "Succeeded", "enableIPForwarding": False, "enableAcceleratedNetworking": True,
        "ipConfigurations": [{"name": "ipconfig1", "properties": {
            "privateIPAddress": private_ip, "primary": True, "subnet": {"id": subnet_id},
            **({"publicIPAddress": {"id": public_ip}} if public_ip else {})}}],
    }
    if nsg:
        props["networkSecurityGroup"] = {"id": nsg}
    if vm:
        props["virtualMachine"] = {"id": vm}
    return row(sub, group, "Microsoft.Network", "networkInterfaces", name, properties=props)


def _vm(sub, group, name, nic_id, power, size="Standard_D2s_v5", location="westeurope"):
    return row(
        sub, group, "Microsoft.Compute", "virtualMachines", name, location, tags={"env": "prod" if sub == SUB_PROD else "dev"},
        zones=["1"], properties={
            "provisioningState": "Succeeded", "hardwareProfile": {"vmSize": size},
            "storageProfile": {"osDisk": {"osType": "Linux"}, "imageReference": {
                "publisher": "canonical", "offer": "ubuntu-24_04-lts", "sku": "server", "version": "latest"}},
            "networkProfile": {"networkInterfaces": [{"id": nic_id}]},
            "osProfile": {"computerName": name, "adminUsername": "azureuser"},
            "extended": {"instanceView": {"powerState": {"code": power}}},
        })


def _site(sub, group, name, plan_id, kind, subnet_id=None, https_only=True, public="Enabled"):
    return row(
        sub, group, "Microsoft.Web", "sites", name, kind=kind, tags={"env": "prod" if sub == SUB_PROD else "dev"},
        properties={
            "provisioningState": "Succeeded", "state": "Running", "serverFarmId": plan_id, "httpsOnly": https_only,
            "hostNames": [f"{name}.azurewebsites.net"], "defaultHostName": f"{name}.azurewebsites.net",
            "publicNetworkAccess": public, "outboundIpAddresses": "20.1.1.1,20.1.1.2", "availabilityState": "Normal",
            **({"virtualNetworkSubnetId": subnet_id} if subnet_id else {}),
        })


def hand_tenant() -> dict:
    P, D, S = SUB_PROD, SUB_DEV, SUB_SHARED
    net, web, data = "rg-network", "rg-web", "rg-data"
    hub = rid(P, net, "Microsoft.Network", "virtualNetworks", "vnet-prod-hub")
    spoke = rid(D, "rg-dev", "Microsoft.Network", "virtualNetworks", "vnet-dev")
    snet_app = hub + "/subnets/snet-app"
    snet_data = hub + "/subnets/snet-data"
    snet_dev = spoke + "/subnets/snet-dev"
    rt = rid(P, net, "Microsoft.Network", "routeTables", "rt-app")
    nsg_app = rid(P, net, "Microsoft.Network", "networkSecurityGroups", "nsg-app")
    nsg_dev = rid(D, "rg-dev", "Microsoft.Network", "networkSecurityGroups", "nsg-dev")
    nat = rid(P, net, "Microsoft.Network", "natGateways", "natgw-prod")
    pip_vm = rid(P, net, "Microsoft.Network", "publicIPAddresses", "pip-vm-prod-1")
    pip_lb = rid(P, net, "Microsoft.Network", "publicIPAddresses", "pip-lb-prod")
    plan = rid(P, web, "Microsoft.Web", "serverfarms", "asp-prod")
    plan_dev = rid(D, "rg-dev", "Microsoft.Web", "serverfarms", "asp-dev")
    nic1 = rid(P, net, "Microsoft.Network", "networkInterfaces", "nic-vm-prod-1")
    nic2 = rid(P, net, "Microsoft.Network", "networkInterfaces", "nic-vm-prod-2")
    nic3 = rid(D, "rg-dev", "Microsoft.Network", "networkInterfaces", "nic-vm-dev-1")
    outside = rid(OUTSIDE_SUB, "rg-partner", "Microsoft.Network", "virtualNetworks", "vnet-partner")

    def rule(name, priority, access, direction, source, port, proto="Tcp"):
        return {"name": name, "properties": {
            "priority": priority, "access": access, "direction": direction, "protocol": proto,
            "sourceAddressPrefix": source, "sourcePortRange": "*", "destinationAddressPrefix": "*",
            "destinationPortRange": port, "description": "ignore me"}}

    resources = [
        _vnet(P, net, "vnet-prod-hub", "10.10.0.0/16", [
            ("snet-app", {"addressPrefix": "10.10.1.0/24", "routeTable": {"id": rt}, "networkSecurityGroup": {"id": nsg_app},
                          "delegations": [{"properties": {"serviceName": "Microsoft.Web/serverFarms"}}],
                          "serviceEndpoints": [{"service": "Microsoft.Storage"}]}),
            ("snet-data", {"addressPrefix": "10.10.2.0/24"}),
            ("snet-egress", {"addressPrefix": "10.10.3.0/24", "natGateway": {"id": nat}}),
        ], [("hub-to-dev", spoke, "10.20.0.0/16"), ("hub-to-partner", outside, "172.16.0.0/16")]),
        _vnet(D, "rg-dev", "vnet-dev", "10.20.0.0/16", [
            ("snet-dev", {"addressPrefix": "10.20.1.0/24", "networkSecurityGroup": {"id": nsg_dev}})],
            [("dev-to-hub", hub, "10.10.0.0/16")]),
        row(P, net, "Microsoft.Network", "routeTables", "rt-app", properties={
            "disableBgpRoutePropagation": False, "routes": [
                {"name": "to-firewall", "properties": {"addressPrefix": "0.0.0.0/0", "nextHopType": "VirtualAppliance", "nextHopIpAddress": "10.10.0.4"}},
                {"name": "to-dev", "properties": {"addressPrefix": "10.20.0.0/16", "nextHopType": "VnetLocal"}}]}),
        row(P, net, "Microsoft.Network", "networkSecurityGroups", "nsg-app", properties={"securityRules": [
            rule("allow-ssh-any", 100, "Allow", "Inbound", "*", "22"), rule("allow-https", 110, "Allow", "Inbound", "10.0.0.0/8", "443"),
            rule("deny-rest", 4000, "Deny", "Inbound", "*", "*")]}),
        row(D, "rg-dev", "Microsoft.Network", "networkSecurityGroups", "nsg-dev", properties={"securityRules": [
            rule("allow-internal", 100, "Allow", "Inbound", "10.0.0.0/8", "*")]}),
        row(P, net, "Microsoft.Network", "natGateways", "natgw-prod", properties={"provisioningState": "Succeeded"}),
        row(P, net, "Microsoft.Network", "publicIPAddresses", "pip-vm-prod-1", properties={
            "ipAddress": "20.50.1.10", "publicIPAllocationMethod": "Static", "publicIPAddressVersion": "IPv4"}),
        row(P, net, "Microsoft.Network", "publicIPAddresses", "pip-lb-prod", properties={
            "ipAddress": "20.50.1.11", "publicIPAllocationMethod": "Static", "publicIPAddressVersion": "IPv4",
            "dnsSettings": {"fqdn": "prod-lb.westeurope.cloudapp.azure.com"}}),
        _nic(P, net, "nic-vm-prod-1", snet_app, pip_vm, nsg_app, "10.10.1.4", rid(P, web, "Microsoft.Compute", "virtualMachines", "vm-prod-1")),
        _nic(P, net, "nic-vm-prod-2", snet_data, None, None, "10.10.2.4"),
        _nic(D, "rg-dev", "nic-vm-dev-1", snet_dev, None, nsg_dev, "10.20.1.4"),
        _vm(P, web, "vm-prod-1", nic1, "PowerState/running"),
        _vm(P, web, "vm-prod-2", nic2, "PowerState/running", "Standard_D4s_v5"),
        _vm(D, "rg-dev", "vm-dev-1", nic3, "PowerState/deallocated", "Standard_B2s"),
        row(P, web, "Microsoft.Web", "serverfarms", "asp-prod", kind="linux", sku={"name": "P1v3", "tier": "PremiumV3", "capacity": 2},
            properties={"numberOfWorkers": 2, "numberOfSites": 3, "reserved": True, "zoneRedundant": False, "status": "Ready"}),
        row(D, "rg-dev", "Microsoft.Web", "serverfarms", "asp-dev", kind="linux", sku={"name": "B1", "tier": "Basic", "capacity": 1},
            properties={"numberOfWorkers": 1, "numberOfSites": 1, "reserved": True, "status": "Ready"}),
        _site(P, web, "app-portal-prod", plan, "app,linux", snet_app),
        _site(P, web, "app-api-prod", plan, "app,linux", None, https_only=False),
        _site(P, web, "func-jobs-prod", plan, "functionapp,linux"),
        _site(D, "rg-dev", "app-portal-dev", plan_dev, "app,linux", None, public="Disabled"),
        row(P, data, "Microsoft.Storage", "storageAccounts", "stprodlogs001", sku={"name": "Standard_LRS", "tier": "Standard"},
            kind="StorageV2", tags={"env": "prod", "db_conn": "placeholder"}, properties={
                "accessTier": "Hot", "publicNetworkAccess": "Enabled", "allowBlobPublicAccess": True,
                "networkAcls": {"defaultAction": "Allow"}, "minimumTlsVersion": "TLS1_2", "supportsHttpsTrafficOnly": True}),
        row(P, data, "Microsoft.Sql", "servers", "sql-prod-01", properties={
            "version": "12.0", "publicNetworkAccess": "Disabled", "fullyQualifiedDomainName": "sql-prod-01.database.windows.net",
            "administratorLogin": "sqladmin", "state": "Ready"}),
        row(P, data, "Microsoft.Sql", "servers", "sqldb-orders", id=rid(P, data, "Microsoft.Sql", "servers", "sql-prod-01", "databases", "sqldb-orders"),
            type="microsoft.sql/servers/databases", kind="v12.0,user", sku={"name": "GP_Gen5_2", "tier": "GeneralPurpose"},
            properties={"status": "Online", "maxSizeBytes": 34359738368}),
        row(P, data, "Microsoft.KeyVault", "vaults", "kv-prod-001", sku=None, tags={"env": "prod"}, properties={
            "sku": {"family": "A", "name": "standard"}, "enableRbacAuthorization": True, "publicNetworkAccess": "Enabled",
            "networkAcls": {"defaultAction": "Deny"}, "enableSoftDelete": True, "enablePurgeProtection": True,
            "vaultUri": "https://kv-prod-001.vault.azure.net/"}),
        row(P, "rg-aks", "Microsoft.ContainerService", "managedClusters", "aks-prod", properties={
            "kubernetesVersion": "1.30.4", "powerState": {"code": "Running"}, "fqdn": "aks-prod.hcp.westeurope.azmk8s.io",
            "agentPoolProfiles": [{"name": "system", "count": 3, "vmSize": "Standard_D4s_v5", "mode": "System"}],
            "apiServerAccessProfile": {"enablePrivateCluster": False}, "servicePrincipalProfile": {"clientId": "msi"}}),
        row(P, "rg-int", "Microsoft.Logic", "workflows", "logic-orders", properties={
            "state": "Enabled", "provisioningState": "Succeeded", "definition": {"triggers": {}, "actions": {}}}),
        row(P, net, "Microsoft.Network", "loadBalancers", "lb-prod", sku={"name": "Standard"}, properties={
            "frontendIPConfigurations": [{"name": "fe", "properties": {"publicIPAddress": {"id": pip_lb}}}]}),
        row(P, net, "Microsoft.Network", "privateEndpoints", "pe-sql", properties={
            "subnet": {"id": snet_data}, "provisioningState": "Succeeded"}),
        row(S, "rg-shared", "Microsoft.OperationalInsights", "workspaces", "law-shared", properties={"retentionInDays": 30}),
        row(S, "rg-shared", "Microsoft.ContainerRegistry", "registries", "acrshared", sku={"name": "Standard"}, properties={
            "loginServer": "acrshared.azurecr.io", "adminUserEnabled": False}),
        row(S, "rg-shared", "Microsoft.Storage", "storageAccounts", "stsharedtf001", sku={"name": "Standard_GRS"}, kind="StorageV2",
            properties={"accessTier": "Cool", "publicNetworkAccess": "Disabled", "allowBlobPublicAccess": False,
                        "networkAcls": {"defaultAction": "Deny"}, "minimumTlsVersion": "TLS1_2", "supportsHttpsTrafficOnly": True}),
        row(S, "rg-shared", "Microsoft.Network", "networkSecurityGroups", "nsg-shared", properties={"securityRules": []}),
        row(P, data, "Microsoft.Cache", "redis", "redis-prod", sku={"name": "Standard", "family": "C", "capacity": 1}, properties={
            "redisVersion": "6.0", "publicNetworkAccess": "Disabled", "hostName": "redis-prod.redis.cache.windows.net"}),
        row(P, data, "Microsoft.DocumentDB", "databaseAccounts", "cosmos-prod", kind="GlobalDocumentDB", properties={
            "publicNetworkAccess": "Enabled", "documentEndpoint": "https://cosmos-prod.documents.azure.com:443/"}),
        row(P, net, "Microsoft.Network", "applicationGateways", "agw-prod", properties={
            "operationalState": "Running", "sku": {"name": "WAF_v2"}, "frontendIPConfigurations": [
                {"name": "fe", "properties": {"publicIPAddress": {"id": pip_lb}}}]}),
        row(P, "rg-aks", "Microsoft.App", "containerApps", "ca-worker", properties={
            "runningStatus": "Running", "configuration": {"ingress": {"external": True, "fqdn": "ca-worker.example.io"}}}),
        row(D, "rg-dev", "Microsoft.Network", "routeTables", "rt-dev", properties={"disableBgpRoutePropagation": True, "routes": []}),
        row(S, "rg-shared", "Microsoft.Contoso", "widgets", "widget-1", properties={"colour": "blue"}),
    ]
    for r in resources:  # the real index lower-cases ids and types
        r["id"] = r["id"].lower()
    site_config = [
        {"id": f"{r['id']}/config/web", "name": "web", "type": "microsoft.web/sites/config", "subscriptionId": r["subscriptionId"],
         "resourceGroup": r["resourceGroup"], "properties": {"linuxFxVersion": "NODE|20-lts", "appSettings": [{"name": "X", "value": "y"}]}}
        for r in resources if r["type"] == "microsoft.web/sites" and r["name"] == "app-portal-prod"
    ]
    subscriptions = [
        {"subscriptionId": s, "id": f"/subscriptions/{s}", "displayName": name, "state": "Enabled", "tenantId": TENANT_ID}
        for s, name in ((P, "Production"), (D, "Non-production"), (S, "Shared services"))
    ]
    advisor, policy, changes, health = _findings(resources)
    containers = [
        {"id": f"/subscriptions/{s['subscriptionId']}", "name": s["displayName"], "type": "microsoft.resources/subscriptions",
         "subscriptionId": s["subscriptionId"], "location": "", "tags": None, "properties": {
             "state": "Enabled", "managementGroupAncestorsChain": [{"name": "mg-corp", "displayName": "Corp"}]}}
        for s in subscriptions
    ]
    for sub, group in sorted({(r["subscriptionId"], r["resourceGroup"]) for r in resources}):
        containers.append({
            "id": f"/subscriptions/{sub}/resourcegroups/{group}", "name": group, "type": "microsoft.resources/subscriptions/resourcegroups",
            "subscriptionId": sub, "location": "westeurope", "tags": {"owner": "netops"}, "properties": {"provisioningState": "Succeeded"}})
    return {"tenant_id": TENANT_ID, "subscriptions": subscriptions, "containers": containers,
            "resources": resources, "site_config": site_config, "advisor": advisor, "policy": policy, "changes": changes,
            "health": health}


def _findings(resources: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Rows of Azure's own tables for a few of the hand tenant's resources.

    Written from documented output, not captured from a tenant (a real tenant has not been read yet). The property names
    come from these pages (read 2026-10-03); values are made up:
    resourcechanges: https://learn.microsoft.com/en-us/azure/governance/resource-graph/changes/get-resource-changes
    (changeAttributes.*, `changes` keyed by property path such as "properties.provisioningState" or "tags.key1");
    advisorresources: https://learn.microsoft.com/en-us/azure/advisor/resource-graph-samples
    (properties.category, resourceMetadata.resourceId, extendedProperties.savingsAmount, savingsCurrency, shortDescription);
    policyresources: https://learn.microsoft.com/en-us/azure/governance/policy/samples/resource-graph-samples
    (microsoft.policyinsights/policystates: complianceState, resourceId, policyAssignmentName, policyDefinitionReferenceId);
    healthresources: https://learn.microsoft.com/en-us/azure/virtual-machines/flash-azure-resource-graph
    (availabilitystatuses: targetResourceId, availabilityState, previousAvailabilityState, occurredTime;
    resourceannotations: annotationName, category, context, reason, summary).
    Not on those pages, so unverified: policyDefinitionAction, policyDefinitionCategory, policySetDefinitionName,
    extendedProperties.recommendationType, currentSku, targetSku, lookbackPeriod, suppressionIds, properties.impact,
    risk, impactedField, impactedValue.
    """
    def of(name: str) -> dict:
        return next(r for r in resources if r["name"] == name)

    def make(type_: str, target: dict | None, tail: str, properties: dict, sub: str | None = None) -> dict:
        base = target["id"] if target else f"/subscriptions/{sub}"
        return {"id": f"{base}{tail}", "name": tail.rsplit("/", 1)[-1], "type": type_, "kind": "", "location": "global",
                "resourceGroup": target["resourceGroup"] if target else "", "subscriptionId": sub or target["subscriptionId"],
                "properties": properties}

    vm2, app, st = of("vm-prod-2"), of("app-portal-prod"), of("stprodlogs001")
    advisor = [
        make("microsoft.advisor/recommendations", vm2, "/providers/microsoft.advisor/recommendations/aaa1", {
            "category": "Cost", "impact": "High", "impactedField": "Microsoft.Compute/virtualMachines", "impactedValue": "vm-prod-2",
            "lastUpdated": "2026-10-02T04:12:00.1234567Z", "recommendationTypeId": "e10b1381-5f0a-47ff-8c7b-37bd4eaffdd9",
            "risk": "Warning", "shortDescription": {"problem": "Right-size or shutdown underutilized virtual machines",
                                                    "solution": "Right-size or shutdown underutilized virtual machines"},
            "resourceMetadata": {"resourceId": vm2["id"], "source": "..."}, "suppressionIds": [],
            "extendedProperties": {"recommendationType": "RightSizing", "currentSku": "Standard_D4s_v5", "targetSku": "Standard_D2s_v5",
                                   "lookbackPeriod": "7", "savingsAmount": "86.4", "annualSavingsAmount": "1036.8", "savingsCurrency": "USD",
                                   "deploymentId": "not-kept", "maxCpuP95": "12"}}),
        make("microsoft.advisor/recommendations", st, "/providers/microsoft.advisor/recommendations/aaa2", {
            "category": "Security", "impact": "Medium", "impactedField": "Microsoft.Storage/storageAccounts", "impactedValue": "stprodlogs001",
            "lastUpdated": "2026-10-01T09:00:00Z", "recommendationTypeId": "11111111-0000-4000-8000-000000000001",
            "shortDescription": {"problem": "Storage accounts should restrict network access", "solution": "Restrict network access"},
            "resourceMetadata": {"resourceId": st["id"]}, "suppressionIds": []}),
        make("microsoft.advisor/recommendations", app, "/providers/microsoft.advisor/recommendations/aaa3", {
            "category": "Performance", "impact": "Low", "impactedValue": "app-portal-prod", "lastUpdated": "2026-09-30T00:00:00Z",
            "shortDescription": {"problem": "Dismissed on purpose", "solution": "-"}, "suppressionIds": ["f3c1a1d2-0000-4000-8000-000000000009"]}),
        make("microsoft.advisor/recommendations", None, "/providers/microsoft.advisor/recommendations/aaa4", {
            "category": "OperationalExcellence", "impact": "Low", "impactedValue": "Production", "lastUpdated": "2026-09-29T00:00:00Z",
            "shortDescription": {"problem": "Create a budget", "solution": "Create a budget"}, "suppressionIds": []}, SUB_PROD),
    ]
    policy = [
        make("microsoft.policyinsights/policystates", st, "/providers/microsoft.policyinsights/policystates/latest", {
            "resourceId": st["id"], "complianceState": "NonCompliant", "policyAssignmentName": "require-https", "policyDefinitionName": "404c3081-a854-4457-ae30-26a93ef643f9",
            "policyDefinitionAction": "audit", "policyDefinitionCategory": "Storage", "policySetDefinitionName": "", "timestamp": "2026-10-02T06:00:00Z",
            "resourceTags": {"db_password": "PLANTED-POLICY-TAG"}, "policyAssignmentParameters": {"x": "PLANTED-POLICY-PARAM"}}),
    ]
    changes = [
        make("microsoft.resources/changes", app, "/providers/microsoft.resources/changes/c1", {
            "targetResourceId": app["id"], "targetResourceType": "Microsoft.Web/sites", "changeType": "Update",
            "changeAttributes": {"timestamp": "2026-10-02T08:30:00.347+00:00", "changedBy": "alice@example.com", "changedByType": "User",
                                 "clientType": "Azure Portal", "operation": "Microsoft.Web/sites/write", "changesCount": 4,
                                 "correlationId": "5a1d0f6e-0000-4000-8000-000000000001"},
            "changes": {
                "properties.httpsOnly": {"previousValue": "False", "newValue": "True", "changeCategory": "User", "propertyChangeType": "Update"},
                "properties.siteConfig.appSettings[0].value": {"previousValue": "PLANTED-OLD-SETTING", "newValue": "PLANTED-NEW-SETTING", "propertyChangeType": "Update"},
                "tags.owner": {"previousValue": "netops", "newValue": "platform", "propertyChangeType": "Update"},
                "tags.db_password": {"newValue": "PLANTED-TAG-CHANGE", "propertyChangeType": "Insert"}}}),
        make("microsoft.resources/changes", vm2, "/providers/microsoft.resources/changes/c2", {
            "targetResourceId": vm2["id"], "targetResourceType": "Microsoft.Compute/virtualMachines", "changeType": "Update",
            "changeAttributes": {"timestamp": "2026-10-01T22:00:00Z", "changedBy": "b93d5f08-0000-4000-8000-a4c27e118c53", "changedByType": "ServicePrincipal",
                                 "clientType": "Terraform", "operation": "Microsoft.Compute/virtualMachines/write", "changesCount": 1},
            "changes": {"properties.hardwareProfile.vmSize": {"previousValue": "Standard_D2s_v5", "newValue": "Standard_D4s_v5", "propertyChangeType": "Update"}}}),
    ]
    health = [
        make("microsoft.resourcehealth/availabilitystatuses", of("vm-prod-1"), "/providers/microsoft.resourcehealth/availabilitystatuses/current", {
            "targetResourceType": "Microsoft.Compute/virtualMachines", "targetResourceId": of("vm-prod-1")["id"],
            "availabilityState": "Available", "previousAvailabilityState": "Unavailable", "occurredTime": "2026-10-03T01:00:00.0000000Z"}),
        make("microsoft.resourcehealth/availabilitystatuses", vm2, "/providers/microsoft.resourcehealth/availabilitystatuses/current", {
            "targetResourceType": "Microsoft.Compute/virtualMachines", "targetResourceId": vm2["id"],
            "availabilityState": "Unavailable", "previousAvailabilityState": "Available", "occurredTime": "2026-10-03T00:35:00.0000000Z"}),
        make("microsoft.resourcehealth/resourceannotations", vm2, "/providers/microsoft.resourcehealth/resourceannotations/host-reboot", {
            "targetResourceType": "Microsoft.Compute/virtualMachines", "targetResourceId": vm2["id"],
            "annotationName": "VirtualMachineHostRebootedForRepair", "occurredTime": "2026-10-03T00:34:00.0000000Z", "category": "Unplanned",
            "context": "Platform Initiated", "reason": "Unexpected host failure",
            "summary": "We're sorry, your virtual machine isn't available because an unexpected failure on the host server."}),
    ]
    return advisor, policy, changes, health


_TYPES = (
    ("Microsoft.Compute", "virtualMachines"), ("Microsoft.Network", "networkInterfaces"), ("Microsoft.Storage", "storageAccounts"),
    ("Microsoft.Web", "sites"), ("Microsoft.Network", "publicIPAddresses"), ("Microsoft.Network", "networkSecurityGroups"),
)


def generated_tenant(resources: int = 2500, subscriptions: int = 5) -> dict:
    """Bulk data: `resources` rows spread over `subscriptions` subscriptions of four resource groups each."""
    subs = [f"{i + 1:08d}-0000-4000-8000-000000000000" for i in range(subscriptions)]
    rows = []
    for i in range(resources):
        sub, group = subs[i % subscriptions], f"rg-{(i // subscriptions) % 4}"
        provider, kind_path = _TYPES[i % len(_TYPES)]
        r = row(sub, group, provider, kind_path, f"gen-{i:05d}", tags={"env": "gen", "n": str(i % 7)},
                properties={"provisioningState": "Succeeded", "padding": "x" * 300})
        r["id"] = r["id"].lower()
        rows.append(r)
    listed = [{"subscriptionId": s, "id": f"/subscriptions/{s}", "displayName": f"Gen {n}", "state": "Enabled", "tenantId": TENANT_ID}
              for n, s in enumerate(subs)]
    containers = [{"id": f"/subscriptions/{s}", "name": f"Gen {n}", "type": "microsoft.resources/subscriptions", "subscriptionId": s,
                   "location": "", "tags": None, "properties": {"state": "Enabled"}} for n, s in enumerate(subs)]
    containers += [{"id": f"/subscriptions/{s}/resourcegroups/rg-{g}", "name": f"rg-{g}", "type": "microsoft.resources/subscriptions/resourcegroups",
                    "subscriptionId": s, "location": "westeurope", "tags": None, "properties": {}} for s in subs for g in range(4)]
    return {"tenant_id": TENANT_ID, "subscriptions": listed, "containers": containers, "resources": rows, "site_config": []}
