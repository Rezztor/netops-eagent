"""Which of the ten categories an Azure resource type belongs to, and what the screen and chat call it.

A type that is not in `BY_TYPE` falls back to its provider namespace (`BY_NAMESPACE`), then to `other`; it is
never dropped. The pull lists the unknown ones so the table can grow.
"""

from __future__ import annotations

CATEGORIES = {
    "compute": "Compute",
    "web": "Web and apps",
    "containers": "Containers",
    "networking": "Networking",
    "storage": "Storage",
    "databases": "Databases",
    "identity_security": "Identity and security",
    "integration": "Integration",
    "monitoring": "Monitoring and management",
    "other": "Other",
}

_TYPES: dict[str, tuple[str, ...]] = {
    "compute": (
        "microsoft.compute/virtualmachines", "microsoft.compute/virtualmachines/extensions",
        "microsoft.compute/virtualmachinescalesets", "microsoft.compute/disks", "microsoft.compute/snapshots",
        "microsoft.compute/images", "microsoft.compute/galleries", "microsoft.compute/galleries/images",
        "microsoft.compute/galleries/images/versions", "microsoft.compute/availabilitysets",
        "microsoft.compute/proximityplacementgroups", "microsoft.compute/hostgroups", "microsoft.compute/hostgroups/hosts",
        "microsoft.compute/restorepointcollections", "microsoft.compute/sshpublickeys",
        "microsoft.compute/capacityreservationgroups", "microsoft.classiccompute/virtualmachines",
        "microsoft.hybridcompute/machines", "microsoft.hybridcompute/machines/extensions",
        "microsoft.desktopvirtualization/hostpools", "microsoft.desktopvirtualization/applicationgroups",
        "microsoft.desktopvirtualization/workspaces", "microsoft.batch/batchaccounts", "microsoft.avs/privateclouds",
        "microsoft.azurestackhci/clusters", "microsoft.azurestackhci/virtualmachineinstances",
    ),
    "web": (
        "microsoft.web/sites", "microsoft.web/sites/slots", "microsoft.web/serverfarms", "microsoft.web/staticsites",
        "microsoft.web/certificates", "microsoft.web/hostingenvironments", "microsoft.web/connections",
        "microsoft.web/kubeenvironments", "microsoft.domainregistration/domains",
        "microsoft.certificateregistration/certificateorders", "microsoft.signalrservice/signalr",
        "microsoft.signalrservice/webpubsub", "microsoft.appplatform/spring",
    ),
    "containers": (
        "microsoft.containerservice/managedclusters", "microsoft.containerservice/openshiftmanagedclusters",
        "microsoft.redhatopenshift/openshiftclusters", "microsoft.containerregistry/registries",
        "microsoft.containerinstance/containergroups", "microsoft.app/containerapps", "microsoft.app/managedenvironments",
        "microsoft.app/jobs", "microsoft.kubernetes/connectedclusters", "microsoft.servicefabric/clusters",
        "microsoft.servicefabric/managedclusters",
    ),
    "networking": (
        "microsoft.network/virtualnetworks", "microsoft.network/virtualnetworks/subnets",
        "microsoft.network/networkinterfaces", "microsoft.network/networksecuritygroups",
        "microsoft.network/applicationsecuritygroups", "microsoft.network/routetables",
        "microsoft.network/publicipaddresses", "microsoft.network/publicipprefixes", "microsoft.network/loadbalancers",
        "microsoft.network/applicationgateways", "microsoft.network/applicationgatewaywebapplicationfirewallpolicies",
        "microsoft.network/azurefirewalls", "microsoft.network/firewallpolicies", "microsoft.network/ipgroups",
        "microsoft.network/virtualnetworkgateways", "microsoft.network/localnetworkgateways",
        "microsoft.network/connections", "microsoft.network/expressroutecircuits", "microsoft.network/expressroutegateways",
        "microsoft.network/expressrouteports", "microsoft.network/privateendpoints",
        "microsoft.network/privatelinkservices", "microsoft.network/privatednszones",
        "microsoft.network/privatednszones/virtualnetworklinks", "microsoft.network/dnszones",
        "microsoft.network/dnsresolvers", "microsoft.network/natgateways", "microsoft.network/bastionhosts",
        "microsoft.network/frontdoors", "microsoft.network/frontdoorwebapplicationfirewallpolicies",
        "microsoft.network/trafficmanagerprofiles", "microsoft.network/virtualwans", "microsoft.network/virtualhubs",
        "microsoft.network/vpngateways", "microsoft.network/vpnsites", "microsoft.network/p2svpngateways",
        "microsoft.network/networkwatchers", "microsoft.network/networkwatchers/flowlogs",
        "microsoft.network/ddosprotectionplans", "microsoft.network/networkvirtualappliances",
        "microsoft.network/routefilters", "microsoft.network/networkmanagers", "microsoft.network/networkprofiles",
        "microsoft.cdn/profiles", "microsoft.cdn/profiles/endpoints", "microsoft.cdn/cdnwebapplicationfirewallpolicies",
        "microsoft.networkfunction/azuretrafficcollectors",
    ),
    "storage": (
        "microsoft.storage/storageaccounts", "microsoft.netapp/netappaccounts", "microsoft.netapp/netappaccounts/capacitypools",
        "microsoft.netapp/netappaccounts/capacitypools/volumes", "microsoft.dataprotection/backupvaults",
        "microsoft.recoveryservices/vaults", "microsoft.elasticsan/elasticsans", "microsoft.storagesync/storagesyncservices",
        "microsoft.storagecache/caches", "microsoft.databox/jobs", "microsoft.datashare/accounts",
    ),
    "databases": (
        "microsoft.sql/servers", "microsoft.sql/servers/databases", "microsoft.sql/servers/elasticpools",
        "microsoft.sql/managedinstances", "microsoft.sql/managedinstances/databases", "microsoft.sql/virtualclusters",
        "microsoft.dbforpostgresql/servers", "microsoft.dbforpostgresql/flexibleservers",
        "microsoft.dbformysql/servers", "microsoft.dbformysql/flexibleservers", "microsoft.dbformariadb/servers",
        "microsoft.documentdb/databaseaccounts", "microsoft.cache/redis", "microsoft.cache/redisenterprise",
        "microsoft.synapse/workspaces", "microsoft.synapse/workspaces/sqlpools", "microsoft.synapse/workspaces/bigdatapools",
        "microsoft.kusto/clusters", "microsoft.kusto/clusters/databases", "microsoft.sqlvirtualmachine/sqlvirtualmachines",
        "microsoft.azuredata/sqlserverinstances",
    ),
    "identity_security": (
        "microsoft.keyvault/vaults", "microsoft.keyvault/managedhsms", "microsoft.managedidentity/userassignedidentities",
        "microsoft.compute/diskencryptionsets", "microsoft.aad/domainservices", "microsoft.security/assessments",
        "microsoft.security/securitycontacts", "microsoft.security/pricings", "microsoft.security/automations",
        "microsoft.security/iotsecuritysolutions", "microsoft.securityinsights/incidents",
        "microsoft.operationsmanagement/solutions", "microsoft.attestation/attestationproviders",
        "microsoft.confidentialledger/ledgers", "microsoft.authorization/policyassignments",
    ),
    "integration": (
        "microsoft.servicebus/namespaces", "microsoft.eventhub/namespaces", "microsoft.eventgrid/topics",
        "microsoft.eventgrid/domains", "microsoft.eventgrid/systemtopics", "microsoft.eventgrid/namespaces",
        "microsoft.logic/workflows", "microsoft.logic/integrationaccounts", "microsoft.apimanagement/service",
        "microsoft.datafactory/factories", "microsoft.relay/namespaces", "microsoft.notificationhubs/namespaces",
        "microsoft.notificationhubs/namespaces/notificationhubs", "microsoft.streamanalytics/streamingjobs",
        "microsoft.devices/iothubs", "microsoft.communication/communicationservices", "microsoft.azurearcdata/datacontrollers",
    ),
    "monitoring": (
        "microsoft.insights/components", "microsoft.insights/actiongroups", "microsoft.insights/metricalerts",
        "microsoft.insights/scheduledqueryrules", "microsoft.insights/activitylogalerts", "microsoft.insights/autoscalesettings",
        "microsoft.insights/datacollectionrules", "microsoft.insights/datacollectionendpoints",
        "microsoft.insights/workbooks", "microsoft.insights/webtests", "microsoft.operationalinsights/workspaces",
        "microsoft.operationalinsights/querypacks", "microsoft.alertsmanagement/smartdetectoralertrules",
        "microsoft.automation/automationaccounts", "microsoft.automation/automationaccounts/runbooks",
        "microsoft.portal/dashboards", "microsoft.dashboard/grafana", "microsoft.maintenance/maintenanceconfigurations",
        "microsoft.monitor/accounts", "microsoft.resourcegraph/queries", "microsoft.managedservices/registrationdefinitions",
        "microsoft.solutions/applications", "microsoft.costmanagement/exports", "microsoft.advisor/recommendations",
        "microsoft.resources/templatespecs", "microsoft.resources/deployments",
    ),
}

BY_TYPE: dict[str, str] = {t: cat for cat, types in _TYPES.items() for t in types}

BY_NAMESPACE: dict[str, str] = {
    "microsoft.compute": "compute", "microsoft.classiccompute": "compute", "microsoft.hybridcompute": "compute",
    "microsoft.desktopvirtualization": "compute", "microsoft.batch": "compute", "microsoft.avs": "compute",
    "microsoft.azurestackhci": "compute", "microsoft.vmwarecloudsimple": "compute",
    "microsoft.web": "web", "microsoft.signalrservice": "web", "microsoft.appplatform": "web",
    "microsoft.domainregistration": "web", "microsoft.certificateregistration": "web",
    "microsoft.containerservice": "containers", "microsoft.containerregistry": "containers",
    "microsoft.containerinstance": "containers", "microsoft.app": "containers", "microsoft.kubernetes": "containers",
    "microsoft.redhatopenshift": "containers", "microsoft.servicefabric": "containers",
    "microsoft.network": "networking", "microsoft.cdn": "networking", "microsoft.classicnetwork": "networking",
    "microsoft.storage": "storage", "microsoft.netapp": "storage", "microsoft.recoveryservices": "storage",
    "microsoft.dataprotection": "storage", "microsoft.elasticsan": "storage", "microsoft.storagesync": "storage",
    "microsoft.storagecache": "storage", "microsoft.databox": "storage", "microsoft.classicstorage": "storage",
    "microsoft.sql": "databases", "microsoft.dbforpostgresql": "databases", "microsoft.dbformysql": "databases",
    "microsoft.dbformariadb": "databases", "microsoft.documentdb": "databases", "microsoft.cache": "databases",
    "microsoft.synapse": "databases", "microsoft.kusto": "databases", "microsoft.sqlvirtualmachine": "databases",
    "microsoft.keyvault": "identity_security", "microsoft.managedidentity": "identity_security",
    "microsoft.aad": "identity_security", "microsoft.security": "identity_security",
    "microsoft.securityinsights": "identity_security", "microsoft.attestation": "identity_security",
    "microsoft.authorization": "identity_security",
    "microsoft.servicebus": "integration", "microsoft.eventhub": "integration", "microsoft.eventgrid": "integration",
    "microsoft.logic": "integration", "microsoft.apimanagement": "integration", "microsoft.datafactory": "integration",
    "microsoft.relay": "integration", "microsoft.notificationhubs": "integration", "microsoft.streamanalytics": "integration",
    "microsoft.communication": "integration",
    "microsoft.insights": "monitoring", "microsoft.operationalinsights": "monitoring",
    "microsoft.operationsmanagement": "monitoring", "microsoft.alertsmanagement": "monitoring",
    "microsoft.automation": "monitoring", "microsoft.portal": "monitoring", "microsoft.dashboard": "monitoring",
    "microsoft.maintenance": "monitoring", "microsoft.monitor": "monitoring", "microsoft.resourcegraph": "monitoring",
    "microsoft.costmanagement": "monitoring", "microsoft.advisor": "monitoring", "microsoft.resources": "monitoring",
    "microsoft.machinelearningservices": "other", "microsoft.cognitiveservices": "other", "microsoft.search": "other",
    "microsoft.databricks": "other", "microsoft.devices": "other",
}

_LABELS = {
    "microsoft.web/sites": "Web app", "microsoft.web/sites/slots": "Deployment slot",
    "microsoft.web/serverfarms": "App Service plan", "microsoft.web/staticsites": "Static web app",
    "microsoft.web/certificates": "App Service certificate", "microsoft.web/hostingenvironments": "App Service environment",
    "microsoft.network/virtualnetworks": "Virtual network", "microsoft.network/virtualnetworks/subnets": "Subnet",
    "microsoft.network/networkinterfaces": "Network card", "microsoft.network/networksecuritygroups": "Security group",
    "microsoft.network/applicationsecuritygroups": "Application security group",
    "microsoft.network/routetables": "Route table", "microsoft.network/publicipaddresses": "Public address",
    "microsoft.network/loadbalancers": "Load balancer", "microsoft.network/applicationgateways": "Application gateway",
    "microsoft.network/azurefirewalls": "Firewall", "microsoft.network/firewallpolicies": "Firewall policy",
    "microsoft.network/virtualnetworkgateways": "Virtual network gateway", "microsoft.network/localnetworkgateways": "Local network gateway",
    "microsoft.network/connections": "Gateway connection", "microsoft.network/privateendpoints": "Private endpoint",
    "microsoft.network/privatednszones": "Private DNS zone", "microsoft.network/dnszones": "DNS zone",
    "microsoft.network/natgateways": "NAT gateway", "microsoft.network/bastionhosts": "Bastion",
    "microsoft.network/frontdoors": "Front Door", "microsoft.network/trafficmanagerprofiles": "Traffic Manager profile",
    "microsoft.network/expressroutecircuits": "ExpressRoute circuit", "microsoft.network/networkwatchers": "Network Watcher",
    "microsoft.compute/virtualmachines": "Virtual machine", "microsoft.compute/virtualmachinescalesets": "Scale set",
    "microsoft.compute/disks": "Disk", "microsoft.compute/snapshots": "Snapshot", "microsoft.compute/images": "Image",
    "microsoft.compute/availabilitysets": "Availability set", "microsoft.hybridcompute/machines": "Arc server",
    "microsoft.storage/storageaccounts": "Storage account", "microsoft.sql/servers": "SQL server",
    "microsoft.sql/servers/databases": "SQL database", "microsoft.sql/managedinstances": "SQL managed instance",
    "microsoft.dbforpostgresql/flexibleservers": "PostgreSQL server", "microsoft.dbforpostgresql/servers": "PostgreSQL server",
    "microsoft.dbformysql/flexibleservers": "MySQL server", "microsoft.dbformysql/servers": "MySQL server",
    "microsoft.documentdb/databaseaccounts": "Cosmos DB account", "microsoft.cache/redis": "Redis",
    "microsoft.keyvault/vaults": "Key vault", "microsoft.managedidentity/userassignedidentities": "Managed identity",
    "microsoft.containerservice/managedclusters": "AKS cluster", "microsoft.containerregistry/registries": "Container registry",
    "microsoft.containerinstance/containergroups": "Container group", "microsoft.app/containerapps": "Container app",
    "microsoft.app/managedenvironments": "Container apps environment", "microsoft.logic/workflows": "Logic app (consumption)",
    "microsoft.servicebus/namespaces": "Service Bus namespace", "microsoft.eventhub/namespaces": "Event Hubs namespace",
    "microsoft.apimanagement/service": "API Management", "microsoft.datafactory/factories": "Data factory",
    "microsoft.insights/components": "Application Insights", "microsoft.operationalinsights/workspaces": "Log Analytics workspace",
    "microsoft.insights/actiongroups": "Action group", "microsoft.automation/automationaccounts": "Automation account",
}

#: What a person types for the tool's `type` argument, mapped to the Azure type it means.
TYPE_ALIASES = {
    "app service": "microsoft.web/sites", "web app": "microsoft.web/sites", "function app": "microsoft.web/sites",
    "vnet": "microsoft.network/virtualnetworks", "virtual network": "microsoft.network/virtualnetworks",
    "subnet": "microsoft.network/virtualnetworks/subnets", "route table": "microsoft.network/routetables",
    "nsg": "microsoft.network/networksecuritygroups", "security group": "microsoft.network/networksecuritygroups",
    "vm": "microsoft.compute/virtualmachines", "virtual machine": "microsoft.compute/virtualmachines",
    "public ip": "microsoft.network/publicipaddresses", "storage account": "microsoft.storage/storageaccounts",
    "sql database": "microsoft.sql/servers/databases", "key vault": "microsoft.keyvault/vaults",
    "aks": "microsoft.containerservice/managedclusters",
}


def category_of(type_: str) -> str:
    t = str(type_ or "").lower()
    return BY_TYPE.get(t) or BY_NAMESPACE.get(t.split("/", 1)[0], "other")


def known(type_: str) -> bool:
    """Whether the type is on the list (a namespace fallback still files it, but the pull reports it)."""
    return str(type_ or "").lower() in BY_TYPE


def label_of(type_: str, kind: str = "") -> str:
    t, k = str(type_ or "").lower(), str(kind or "").lower()
    if t == "microsoft.web/sites":
        if "workflowapp" in k:  # a standard logic app is also kind functionapp
            return "Logic app (standard)"
        if "functionapp" in k:
            return "Function app"
    return _LABELS.get(t) or (t.rsplit("/", 1)[-1] or "Unknown")
