"""Reducing one Resource Graph row to the fields this plugin keeps. Nothing else is ever stored.

`ALLOW` is the whole policy: a type, then `{our key: path into properties}`. A type that is not a key keeps the
envelope only. Adding a path here is the only way a new field is ever stored. `row["properties"]` is never copied,
logged or returned; only the listed paths are read out of it, and a path that lands on an object (not a scalar or a
list of scalars) yields nothing, so a wrong path cannot leak a whole sub-document.

Path language: dots descend, `[]` means "each element" (a list comes back), `a|b` joins the lists of two paths,
and a leading `@` reads the row itself (`@zones`) instead of `properties`. A value that is a dict is a nested
object of such paths; a `(list path, dict)` pair is a list of objects, one per element.

Every path is `# unverified:` until a real tenant has been read; they follow Microsoft's documented ARM shapes.

`SIGNALS` and `change` do the same for the four extra tables a pull reads beside the resources (Advisor, policy,
Azure's own change records, Resource Health): one reviewed path list per kind, nothing else is kept.
"""

from __future__ import annotations

import datetime as _dt
import re
from functools import cache
from typing import Any

from plugins._ea_azure.helpers import categories
from plugins._ea_inventory.helpers import redact

VNET = "microsoft.network/virtualnetworks"
SUBNET = "microsoft.network/virtualnetworks/subnets"
SITES = "microsoft.web/sites"

ENVELOPE = ("id", "name", "type", "kind", "location", "resourceGroup", "subscriptionId", "sku", "tags", "managedBy",
            "zones", "properties.provisioningState")
_SKU_KEYS = ("name", "tier", "size", "family", "capacity")
RELATIONS = ("route_table", "nsg", "nat_gateway", "peering", "subnet", "public_ip", "nic", "plan", "vnet_integration",
             "managed_by", "ip_configuration", "nat_gateway_ip", "public_ip_prefix", "vnet_gateway", "local_gateway", "target",
             "source", "firewall_policy", "virtual_hub", "protects", "vm", "elastic_pool")

_PUBLIC_IP_IPCONFIGS = "ipConfigurations[].properties.publicIPAddress.id"
_SUBNET_IPCONFIGS = "ipConfigurations[].properties.subnet.id"
_PUBLIC_ACCESS = "publicNetworkAccess"

# unverified: every path below, until a real tenant has been read (phase 4)
ALLOW: dict[str, dict[str, Any]] = {
    VNET: {
        "address_space": "addressSpace.addressPrefixes",
        "dns_servers": "dhcpOptions.dnsServers",
        "peerings": ("virtualNetworkPeerings[]", {
            "name": "name", "state": "properties.peeringState", "sync": "properties.peeringSyncLevel",
            "remote_id": "properties.remoteVirtualNetwork.id",
            "remote_address_space": "properties.remoteAddressSpace.addressPrefixes",
            "allow_forwarded": "properties.allowForwardedTraffic", "allow_gateway_transit": "properties.allowGatewayTransit",
            "use_remote_gateways": "properties.useRemoteGateways"}),
    },
    SUBNET: {  # applied to each entry of the network's `subnets`, which the plugin lifts out as rows of their own
        "address_prefixes": "addressPrefixes|addressPrefix",
        "delegations": "delegations[].properties.serviceName",
        "service_endpoints": "serviceEndpoints[].service",
    },
    "microsoft.network/routetables": {
        "bgp_propagation": "disableBgpRoutePropagation",  # inverted by _FIX
        "routes": ("routes[]", {"name": "name", "prefix": "properties.addressPrefix",
                                "next_hop_type": "properties.nextHopType", "next_hop_ip": "properties.nextHopIpAddress"}),
    },
    "microsoft.network/networksecuritygroups": {  # custom rules only, never `description`
        "rules": ("securityRules[]", {
            "name": "name", "priority": "properties.priority", "direction": "properties.direction",
            "access": "properties.access", "protocol": "properties.protocol",
            "sources": "properties.sourceAddressPrefix|properties.sourceAddressPrefixes",
            "source_ports": "properties.sourcePortRange|properties.sourcePortRanges",
            "destinations": "properties.destinationAddressPrefix|properties.destinationAddressPrefixes",
            "destination_ports": "properties.destinationPortRange|properties.destinationPortRanges"}),
    },
    "microsoft.network/publicipaddresses": {
        "ip": "ipAddress", "allocation": "publicIPAllocationMethod", "version": "publicIPAddressVersion",
        "fqdn": "dnsSettings.fqdn",
    },
    "microsoft.network/networkinterfaces": {
        "ip_configs": ("ipConfigurations[]", {"name": "name", "private_ip": "properties.privateIPAddress",
                                              "primary": "properties.primary"}),
        "ip_forwarding": "enableIPForwarding", "accelerated": "enableAcceleratedNetworking",
    },
    "microsoft.compute/virtualmachines": {
        "size": "hardwareProfile.vmSize", "os_type": "storageProfile.osDisk.osType",
        "image": {"publisher": "storageProfile.imageReference.publisher", "offer": "storageProfile.imageReference.offer",
                  "sku": "storageProfile.imageReference.sku", "version": "storageProfile.imageReference.version"},
        "license_type": "licenseType",
    },
    "microsoft.compute/disks": {"size_gb": "diskSizeGB", "disk_state": "diskState", "time_created": "timeCreated",
                                "last_ownership_update": "LastOwnershipUpdateTime"},
    "microsoft.compute/snapshots": {"size_gb": "diskSizeGB", "time_created": "timeCreated", "incremental": "incremental"},
    SITES: {
        "host_names": "hostNames", "default_host_name": "defaultHostName", "https_only": "httpsOnly",
        "public_network_access": _PUBLIC_ACCESS, "outbound_ips": "outboundIpAddresses",  # a comma list, split by _FIX
        "availability_state": "availabilityState", "runtime": "",  # filled from SITE_CONFIG; "" until verified
    },
    "microsoft.web/serverfarms": {
        "workers": "numberOfWorkers", "sites": "numberOfSites", "linux": "reserved", "zone_redundant": "zoneRedundant",
        "status": "status",
    },
    "microsoft.storage/storageaccounts": {
        "access_tier": "accessTier", "public_network_access": _PUBLIC_ACCESS,
        "allow_blob_public_access": "allowBlobPublicAccess", "default_network_action": "networkAcls.defaultAction",
        "min_tls": "minimumTlsVersion", "https_only": "supportsHttpsTrafficOnly",
    },
    "microsoft.sql/servers": {"version": "version", "public_network_access": _PUBLIC_ACCESS,
                              "host_name": "fullyQualifiedDomainName"},
    "microsoft.sql/servers/databases": {"max_size_bytes": "maxSizeBytes", "zone_redundant": "zoneRedundant",
                                        "status": "status"},
    "microsoft.dbforpostgresql/flexibleservers": {
        "version": "version", "public_network_access": "network.publicNetworkAccess",
        "host_name": "fullyQualifiedDomainName", "storage_gb": "storage.storageSizeGB"},
    "microsoft.dbforpostgresql/servers": {"version": "version", "public_network_access": _PUBLIC_ACCESS,
                                          "host_name": "fullyQualifiedDomainName", "storage_mb": "storageProfile.storageMB"},
    "microsoft.dbformysql/flexibleservers": {
        "version": "version", "public_network_access": "network.publicNetworkAccess",
        "host_name": "fullyQualifiedDomainName", "storage_gb": "storage.storageSizeGB"},
    "microsoft.dbformysql/servers": {"version": "version", "public_network_access": _PUBLIC_ACCESS,
                                     "host_name": "fullyQualifiedDomainName", "storage_mb": "storageProfile.storageMB"},
    "microsoft.documentdb/databaseaccounts": {
        "public_network_access": _PUBLIC_ACCESS, "host_name": "documentEndpoint",
        "consistency": "consistencyPolicy.defaultConsistencyLevel"},
    "microsoft.cache/redis": {"version": "redisVersion", "public_network_access": _PUBLIC_ACCESS, "host_name": "hostName"},
    "microsoft.containerservice/managedclusters": {
        "version": "kubernetesVersion", "private_cluster": "apiServerAccessProfile.enablePrivateCluster",
        "fqdn": "fqdn",
        "node_pools": ("agentPoolProfiles[]", {"name": "name", "count": "count", "size": "vmSize", "mode": "mode"}),
    },
    "microsoft.app/containerapps": {
        "images": "template.containers[].image", "ingress_external": "configuration.ingress.external",
        "fqdn": "configuration.ingress.fqdn"},
    "microsoft.containerinstance/containergroups": {"images": "containers[].properties.image", "fqdn": "ipAddress.fqdn"},
    "microsoft.keyvault/vaults": {
        "sku": "sku.name", "rbac": "enableRbacAuthorization", "public_network_access": _PUBLIC_ACCESS,
        "default_network_action": "networkAcls.defaultAction", "soft_delete": "enableSoftDelete",
        "purge_protection": "enablePurgeProtection"},
    "microsoft.network/applicationgateways": {
        "operational_state": "operationalState", "sku_name": "sku.name", "sku_tier": "sku.tier",
        "sku_capacity": "sku.capacity",
        "backend_addresses": "backendAddressPools[].properties.backendAddresses[].ipAddress"
                             "|backendAddressPools[].properties.backendAddresses[].fqdn",
        "backend_ip_configs": "backendAddressPools[].properties.backendIPConfigurations[].id"},
    "microsoft.network/azurefirewalls": {"threat_intel_mode": "threatIntelMode", "sku_tier": "sku.tier"},
    "microsoft.network/virtualnetworkgateways": {  # never `vpnClientConfiguration` whole: it holds RADIUS and certificate data
        "gateway_type": "gatewayType", "vpn_type": "vpnType", "active_active": "activeActive", "sku_name": "sku.name",
        "vpn_client_pool": "vpnClientConfiguration.vpnClientAddressPool.addressPrefixes"},
    "microsoft.network/connections": {"connection_type": "connectionType", "connection_status": "connectionStatus"},
    "microsoft.network/privateendpoints": {
        "connection_states": "privateLinkServiceConnections[].properties.privateLinkServiceConnectionState.status",
        "manual_connection_states":
            "manualPrivateLinkServiceConnections[].properties.privateLinkServiceConnectionState.status"},
    "microsoft.logic/workflows": {"version": "version"},
    "microsoft.network/loadbalancers": {
        "backend_ip_configs": "backendAddressPools[].properties.backendIPConfigurations[].id",
        "backend_addresses": "backendAddressPools[].properties.loadBalancerBackendAddresses[].name",
        "inbound_nat_rules": "inboundNatRules[].name"},
    "microsoft.network/localnetworkgateways": {"address_space": "localNetworkAddressSpace.addressPrefixes",
                                               "gateway_ip": "gatewayIpAddress"},
    "microsoft.network/virtualhubs": {"address_prefix": "addressPrefix"},
    "microsoft.network/expressroutecircuits": {"provider_state": "serviceProviderProvisioningState",
                                               "circuit_state": "circuitProvisioningState"},
    "microsoft.network/privatednszones": {"vnet_links": "numberOfVirtualNetworkLinks", "record_sets": "numberOfRecordSets"},
    "microsoft.operationalinsights/workspaces": {"retention_days": "retentionInDays", "sku": "sku.name",
                                                 "daily_quota_gb": "workspaceCapping.dailyQuotaGb"},
    "microsoft.web/certificates": {"expires": "expirationDate"},
}

#: Applied to every type, on top of its row in `ALLOW`: the envelope's zones, which the envelope names but never stored.
ALL_TYPES: dict[str, Any] = {"zones": "@zones"}

#: type -> [(relation kind, path to a resource id or a list of them)]
# unverified: every path below, like `ALLOW`'s, until a real tenant has been read
RELATION_PATHS: dict[str, list[tuple[str, str]]] = {
    SUBNET: [("route_table", "routeTable.id"), ("nsg", "networkSecurityGroup.id"), ("nat_gateway", "natGateway.id")],
    "microsoft.network/networkinterfaces": [
        ("subnet", _SUBNET_IPCONFIGS), ("public_ip", _PUBLIC_IP_IPCONFIGS), ("nsg", "networkSecurityGroup.id")],
    "microsoft.compute/virtualmachines": [("nic", "networkProfile.networkInterfaces[].id")],
    SITES: [("plan", "serverFarmId"), ("vnet_integration", "virtualNetworkSubnetId")],
    "microsoft.network/loadbalancers": [("public_ip", "frontendIPConfigurations[].properties.publicIPAddress.id")],
    "microsoft.network/applicationgateways": [
        ("public_ip", "frontendIPConfigurations[].properties.publicIPAddress.id"),
        ("subnet", "gatewayIPConfigurations[].properties.subnet.id")],
    "microsoft.network/azurefirewalls": [
        ("public_ip", _PUBLIC_IP_IPCONFIGS), ("subnet", _SUBNET_IPCONFIGS), ("firewall_policy", "firewallPolicy.id"),
        ("virtual_hub", "virtualHub.id")],
    "microsoft.network/virtualnetworkgateways": [("public_ip", _PUBLIC_IP_IPCONFIGS), ("subnet", _SUBNET_IPCONFIGS)],
    "microsoft.network/bastionhosts": [("public_ip", _PUBLIC_IP_IPCONFIGS), ("subnet", _SUBNET_IPCONFIGS)],
    "microsoft.network/natgateways": [("public_ip", "publicIpAddresses[].id")],
    "microsoft.network/privateendpoints": [
        ("subnet", "subnet.id"), ("nic", "networkInterfaces[].id"),
        ("target", "privateLinkServiceConnections[].properties.privateLinkServiceId"
                   "|manualPrivateLinkServiceConnections[].properties.privateLinkServiceId")],
    "microsoft.network/publicipaddresses": [
        ("ip_configuration", "ipConfiguration.id"), ("nat_gateway_ip", "natGateway.id"),
        ("public_ip_prefix", "publicIPPrefix.id")],
    "microsoft.network/connections": [
        ("vnet_gateway", "virtualNetworkGateway1.id|virtualNetworkGateway2.id"), ("local_gateway", "localNetworkGateway2.id")],
    "microsoft.compute/snapshots": [("source", "creationData.sourceResourceId")],  # never `sourceUri`: it can be a storage URL
    "microsoft.network/ddosprotectionplans": [("protects", "virtualNetworks[].id")],
    "microsoft.compute/availabilitysets": [("vm", "virtualMachines[].id")],
    "microsoft.sql/servers/databases": [("elastic_pool", "elasticPoolId")],
}

#: type -> (path, {Azure word, lower-cased: state}); the word itself is kept as `state_detail`
_STATE: dict[str, tuple[str, dict[str, str]]] = {
    SITES: ("state", {"running": "running", "stopped": "stopped"}),
    "microsoft.containerservice/managedclusters": ("powerState.code", {"running": "running", "stopped": "stopped"}),
    "microsoft.sql/servers/databases": ("status", {"online": "running", "paused": "stopped", "offline": "stopped",
                                                   "stopped": "stopped", "disabled": "stopped"}),
    "microsoft.network/applicationgateways": ("operationalState", {"running": "running", "stopped": "stopped"}),
    "microsoft.app/containerapps": ("runningStatus", {"running": "running", "stopped": "stopped"}),
    "microsoft.logic/workflows": ("state", {"enabled": "running", "disabled": "stopped", "suspended": "stopped"}),
    "microsoft.dbforpostgresql/flexibleservers": ("state", {"ready": "running", "stopped": "stopped", "disabled": "stopped"}),
    "microsoft.dbformysql/flexibleservers": ("state", {"ready": "running", "stopped": "stopped", "disabled": "stopped"}),
}
_POWER = {"running": "running", "stopped": "stopped", "deallocated": "stopped", "deallocating": "stopped",
          "stopping": "stopped"}
_VM = "microsoft.compute/virtualmachines"
_SECRET_NAME = re.compile(r"pass|pwd|secret|token|key|credential|connection", re.I)
# a value shaped like a connection string, a URL with a password, a JWT or a PEM block (redact_text does not know these)
_SECRET_VALUE = re.compile(
    r"(?:^|;)\s*(?:server|data source|endpoint|accountkey|sharedaccesskey|accountname|defaultendpointsprotocol|password|pwd"
    r"|uid|user id)\s*=|://[^/\s:@]+:[^@\s]+@|^eyJ[\w-]+\.[\w-]+\.|-----BEGIN", re.I)
_CUT = 512
_LIST_MAX = 100  # ponytail: a list of scalars is cut here (a load balancer with thousands of back ends); raise if a rule needs more


# --- reading paths out of a row -----------------------------------------------------

def _key(obj: Any, name: str) -> Any:
    if not isinstance(obj, dict):
        return None
    if name in obj:
        return obj[name]
    low = name.lower()
    for k, v in obj.items():  # Resource Graph is not consistent about case
        if isinstance(k, str) and k.lower() == low:
            return v
    return None


def _walk(obj: Any, parts: list[str]) -> list[Any]:
    """Every value at the path; `[]` fans out over a list."""
    if not parts:
        return [obj]
    head, rest = parts[0], parts[1:]
    if head.endswith("[]"):
        items = obj if head == "[]" else _key(obj, head[:-2])
        return [v for item in (items if isinstance(items, list) else []) for v in _walk(item, rest)]
    return _walk(_key(obj, head), rest) if obj is not None else []


def _scalar(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool)) and not (isinstance(v, str) and not v.strip())


def _clean(v: Any) -> Any:
    return v[:_CUT] if isinstance(v, str) else v


def _get(source: Any, path: str, row: dict) -> Any:
    """The value at `path`: a scalar, a list of scalars (a `[]` or `|` path), or None. Never an object."""
    if not path:
        return None
    many = "[]" in path or "|" in path
    found: list[Any] = []
    for one in path.split("|"):
        root = row if one.startswith("@") else source
        found += _walk(root, one.lstrip("@").split("."))
    flat = [x for v in found for x in (v if isinstance(v, list) else [v])]
    flat = [_clean(x) for x in flat if _scalar(x)]
    if many or any(isinstance(v, list) for v in found):
        return list(dict.fromkeys(flat))[:_LIST_MAX] if flat else None
    return flat[0] if flat else None


def _pick(spec: Any, source: Any, row: dict) -> Any:
    if isinstance(spec, str):
        return _get(source, spec, row)
    if isinstance(spec, dict):
        out = {k: _pick(p, source, row) for k, p in spec.items()}
        return {k: v for k, v in out.items() if v is not None} or None
    path, sub = spec
    items = [x for x in _walk(source, path.split(".")) if isinstance(x, dict)]
    out = [{k: v for k, p in sub.items() if (v := _pick(p, item, row)) is not None} for item in items]
    return [o for o in out if o] or None


def paths() -> list[str]:
    """Every property path `ALLOW` and `RELATION_PATHS` can read, for the test that guards the forbidden list."""
    def walk(spec: Any) -> list[str]:
        if isinstance(spec, str):
            return [p for p in spec.split("|") if p]
        if isinstance(spec, dict):
            return [p for s in spec.values() for p in walk(s)]
        return [spec[0], *walk(spec[1])]

    return [p for rules in (*ALLOW.values(), ALL_TYPES, *SIGNALS.values()) for s in rules.values() for p in walk(s)] + [
        p for rels in RELATION_PATHS.values() for _, p in rels]


# --- tags, state, props ------------------------------------------------------------

def tags(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for name, value in raw.items():
        n, v = str(name)[:256], "" if value is None else str(value)
        masked = _SECRET_NAME.search(n) or _SECRET_VALUE.search(v) or redact.redact_text(v)[0] != v
        out[n] = "[masked]" if masked and v else v[:256]
    return out


def state_of(type_: str, kind: str, properties: Any) -> tuple[str, str]:
    """(state, Azure's own word). A failed or canceled provisioning wins over everything else."""
    t = str(type_ or "").lower()
    prov = _get(properties, "provisioningState", {})
    if str(prov or "").lower() in ("failed", "canceled"):
        return "failed", str(prov)
    if t == _VM:
        code = _get(properties, "extended.instanceView.powerState.code", {})  # unverified: where the power state is
        if not code:
            return "unknown", ""
        return _POWER.get(str(code).rsplit("/", 1)[-1].lower(), "ok"), str(code)
    if t in _STATE:
        path, words = _STATE[t]
        word = _get(properties, path, {})
        if word:
            return words.get(str(word).lower(), "ok"), str(word)
    return "ok", ""


def _outbound(props: dict, _properties: Any) -> None:
    if isinstance(props.get("outbound_ips"), str):
        props["outbound_ips"] = [i.strip() for i in props["outbound_ips"].split(",") if i.strip()]


def _bgp(props: dict, _properties: Any) -> None:
    if isinstance(props.get("bgp_propagation"), bool):
        props["bgp_propagation"] = not props["bgp_propagation"]


_FIX = {SITES: _outbound, "microsoft.network/routetables": _bgp}


def _props(type_: str, properties: Any, row: dict) -> dict[str, Any]:
    out = {k: v for k, spec in {**ALLOW.get(type_, {}), **ALL_TYPES}.items() if (v := _pick(spec, properties, row)) is not None}
    if type_ == SITES:
        out["runtime"] = ""
    if type_ in _FIX:
        _FIX[type_](out, properties)
    return out


def _relations(type_: str, properties: Any, row: dict) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for kind, path in [*RELATION_PATHS.get(type_, []), ("managed_by", "@managedBy")]:
        found = _get(properties, path, row)
        for to in found if isinstance(found, list) else [found]:
            to_id = str(to or "").strip().lower()
            if to_id.startswith("/") and (kind, to_id) not in seen:
                seen.add((kind, to_id))
                out.append({"kind": kind, "to_native_id": to_id, "props": {}})
    return out


def _sku(raw: Any) -> dict[str, Any]:
    return {k: _clean(v) for k in _SKU_KEYS if _scalar(v := _key(raw, k))}


def _shape(row: dict, properties: Any, type_: str, parent: dict | None = None) -> dict[str, Any]:
    state, detail = state_of(type_, str(row.get("kind") or ""), properties)
    return {
        "native_id": str(row["id"]).lower(), "name": str(row.get("name") or "")[:256], "type": type_,
        "kind": str(row.get("kind") or ""), "region": str(row.get("location") or ""),
        "subscription": str(row.get("subscriptionId") or "").lower(),
        "resource_group": str(row.get("resourceGroup") or ""), "sku": _sku(row.get("sku")),
        "tags": tags(row.get("tags")), "props": _props(type_, properties, row), "state": state, "state_detail": detail,
        "category": categories.category_of(type_), "label": categories.label_of(type_, str(row.get("kind") or "")),
        "relations": _relations(type_, properties, row), "children": [],
    }


def reduce(row: dict) -> dict[str, Any] | None:
    """One Resource Graph row (objectArray format) as the stored shape, or None when it has no id or type."""
    if not isinstance(row, dict) or not row.get("id") or not row.get("type"):
        return None
    type_ = str(row["type"]).lower()
    properties = _key(row, "properties")
    shaped = _shape(row, properties, type_)
    if type_ == VNET:
        for sub in _key(properties, "subnets") or []:
            if not isinstance(sub, dict) or not sub.get("id"):
                continue
            sub_row = {"id": sub["id"], "name": sub.get("name"), "type": SUBNET, "location": row.get("location"),
                       "subscriptionId": row.get("subscriptionId"), "resourceGroup": row.get("resourceGroup")}
            shaped["children"].append(_shape(sub_row, _key(sub, "properties"), SUBNET))
        for peer in shaped["props"].get("peerings") or []:
            if peer.get("remote_id"):
                shaped["relations"].append({"kind": "peering", "to_native_id": peer["remote_id"].lower(),
                                            "props": {"name": peer.get("name", ""), "state": peer.get("state", "")}})
    return shaped


def merge_runtime(shaped: list[dict], config_rows: list[dict]) -> None:
    """Set `props.runtime` on each web app from its `SITE_CONFIG` row. Nothing else of the row is read."""
    sites = {s["native_id"]: s for s in shaped if s["type"] == SITES}
    for row in config_rows:
        site = sites.get(str(row.get("id") or "").lower().split("/config/", 1)[0])
        if not site:
            continue
        for name in ("linuxFxVersion", "windowsFxVersion", "netFrameworkVersion"):
            value = _get(_key(row, "properties"), name, {})
            if isinstance(value, str) and value:
                site["props"]["runtime"] = value
                break


def container(row: dict) -> dict[str, Any] | None:
    """A subscription or resource group row from `CONTAINERS`: its place, tags, and (subscription) management groups."""
    if not isinstance(row, dict) or not row.get("id"):
        return None
    properties = _key(row, "properties")
    out: dict[str, Any] = {
        "id": str(row["id"]).lower(), "name": str(row.get("name") or "")[:256], "region": str(row.get("location") or ""),
        "subscription": str(row.get("subscriptionId") or "").lower(), "tags": tags(row.get("tags")), "props": {},
    }
    if str(row.get("type") or "").lower() == "microsoft.resources/subscriptions":
        # unverified: the ancestors chain is where Resource Graph puts a subscription's management groups
        chain = [x for x in _walk(properties, ["managementGroupAncestorsChain[]"]) if isinstance(x, dict)]
        groups = [{"id": str(_key(g, "name") or ""), "name": str(_key(g, "displayName") or _key(g, "name") or "")}
                  for g in chain]
        out["props"] = {"management_groups": [g for g in groups if g["id"]]}
    return out


# --- what Azure itself says about a resource: Advisor, policy, health and its own change records -----------------

#: kind -> {our key: path into the row's properties}, the same path language as `ALLOW`. `_target` is the resource the
#: row is about, `_at` when Azure says it happened and `_type` the target's type; none of them is kept under its own key.
# unverified: every path below, and the table and type names in queries.py, until test session 1 has read them
SIGNALS: dict[str, dict[str, Any]] = {
    "advisor": {  # Microsoft's own wording and estimates; the savings are strings, never summed here
        "_target": "resourceMetadata.resourceId", "_at": "lastUpdated", "category": "category", "impact": "impact",
        "risk": "risk", "problem": "shortDescription.problem", "solution": "shortDescription.solution",
        "impacted_type": "impactedField", "impacted_name": "impactedValue", "type_id": "recommendationTypeId",
        "recommendation": "extendedProperties.recommendationType", "current_sku": "extendedProperties.currentSku",
        "target_sku": "extendedProperties.targetSku", "lookback_days": "extendedProperties.lookbackPeriod",
        "savings": "extendedProperties.savingsAmount", "annual_savings": "extendedProperties.annualSavingsAmount",
        "currency": "extendedProperties.savingsCurrency",
    },
    "policy": {  # non-compliant states only (queries.POLICY)
        "_target": "resourceId", "_at": "timestamp", "state": "complianceState", "assignment": "policyAssignmentName",
        "definition": "policyDefinitionName", "action": "policyDefinitionAction", "category": "policyDefinitionCategory",
        "initiative": "policySetDefinitionName", "reference": "policyDefinitionReferenceId",
    },
    "health": {  # two row types: availabilitystatuses (the state) and resourceannotations (why it changed)
        "_target": "targetResourceId", "_at": "occurredTime", "state": "availabilityState",
        "previous_state": "previousAvailabilityState", "annotation": "annotationName", "reason": "reason",
        "category": "category", "context": "context", "summary": "summary",
    },
    "change": {  # `changes` (old and new values) is built by `_changes`, only for the paths `ALLOW` reads
        "_target": "targetResourceId", "_at": "changeAttributes.timestamp", "_type": "targetResourceType",
        "change_type": "changeType", "changed_by": "changeAttributes.changedBy",
        "changed_by_type": "changeAttributes.changedByType", "client": "changeAttributes.clientType",
        "operation": "changeAttributes.operation", "correlation_id": "changeAttributes.correlationId",
        "count": "changeAttributes.changesCount", "truncated": "changeAttributes.isTruncated",
    },
}
#: where a row's own id says what it is about, when its properties do not
_ABOUT = {"advisor": "/providers/microsoft.advisor/", "health": "/providers/microsoft.resourcehealth/"}
_INDEX = re.compile(r"\[\d+\]")
_CHANGES_MAX = 40
_ENVELOPE_CHANGES = ("location", "zones", "kind")


def _when(value: Any) -> _dt.datetime | None:
    try:
        at = _dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=_dt.timezone.utc)


def _safe(value: str) -> str:
    return "[masked]" if _SECRET_VALUE.search(value) or redact.redact_text(value)[0] != value else value[:_CUT]


@cache
def _changeable(type_: str) -> frozenset[str]:
    """The property paths (lower-cased, `[]` for an index) whose old and new values a change record may show: the
    ones `ALLOW`, `RELATION_PATHS` and the state rule read for this type, and a network's subnets' own."""
    def flat(spec: Any) -> list[str]:
        if isinstance(spec, str):
            return [p for p in spec.split("|") if p and p[0] != "@"]
        if isinstance(spec, dict):
            return [p for s in spec.values() for p in flat(s)]
        if isinstance(spec, list):
            return [p for s in spec for p in flat(s)]
        return [f"{spec[0]}.{p}" for p in flat(spec[1])]

    found = [*flat(ALLOW.get(type_, {})), *flat([p for _, p in RELATION_PATHS.get(type_, [])]), "provisioningState"]
    if type_ in _STATE:
        found.append(_STATE[type_][0])
    if type_ == VNET:
        found += [f"subnets[].properties.{p}" for p in [*flat(ALLOW[SUBNET]), *flat([p for _, p in RELATION_PATHS[SUBNET]])]]
    return frozenset(p.lower() for p in found)


def _changes(properties: Any, type_: str) -> list[dict[str, Any]]:
    """Each changed path, and its old and new value only when the path is one this plugin stores. A value on any
    other path is dropped here, so an app setting or a script changed in Azure never reaches the database."""
    bag = _key(properties, "changes")
    if not isinstance(bag, dict):
        return []
    allowed = _changeable(type_)
    out: list[dict[str, Any]] = []
    for path, entry in list(bag.items())[:_CHANGES_MAX]:
        name = _INDEX.sub("[]", str(path))
        low = name.lower()
        item: dict[str, Any] = {"path": redact.redact_text(name[:256])[0],
                                "kind": str(_key(entry, "propertyChangeType") or "")[:16]}
        shown = (low[11:] in allowed if low.startswith("properties.") else low.startswith("tags.")
                 or low in _ENVELOPE_CHANGES or (low.startswith("sku.") and low[4:] in _SKU_KEYS))
        if not shown:
            out.append({**item, "hidden": True})
            continue
        for key, source in (("old", "previousValue"), ("new", "newValue")):
            value = _key(entry, source)
            if not (_scalar(value) or value is None):
                item["hidden"] = True
            elif low.startswith("tags."):
                item[key] = tags({name[5:]: value})[name[5:]] if value is not None else None
            else:
                item[key] = value if value is None or not isinstance(value, str) else _safe(value)
        out.append(item)
    return out


def signal(kind: str, row: dict) -> dict[str, Any] | None:
    """One Advisor, policy, health or change row as `{kind, target, at, data}`; None when it names no resource (or is
    an Advisor recommendation somebody dismissed). `target` is the lower-cased id of the resource it is about."""
    if not isinstance(row, dict) or kind not in SIGNALS:
        return None
    properties, spec = _key(row, "properties"), SIGNALS[kind]
    if kind == "advisor" and _get(properties, "suppressionIds[]", row):
        return None
    target = _get(properties, spec["_target"], row)
    if not target and kind in _ABOUT:
        target = str(row.get("id") or "").lower().split(_ABOUT[kind], 1)[0]
    target = str(target or "").strip().lower()
    if not target.startswith("/"):
        return None
    data = {k: v for k, p in spec.items() if k[0] != "_" and (v := _pick(p, properties, row)) is not None}
    if kind == "change":
        data["changes"] = _changes(properties, str(_get(properties, spec["_type"], row) or "").lower())
    return {"kind": kind, "target": target, "at": _when(_get(properties, spec["_at"], row)), "data": data}
