"""Things that cost money for nothing, found in the stored inventory. Computed on read, never stored (ADR 0045).

A pointer to look, not a verdict: "attached to nothing" is not "nobody needs it", and "idle" (a firewall that carries no
traffic) cannot be seen from inventory at all. Each rule reads stored fields only. `findings` is pure (no database, no
network); `priced` is the one call that may reach prices.azure.com, through `prices`, and only with a region and a SKU.

A rule that needs a property `shape.ALLOW` / `RELATION_PATHS` does not store yet is inert (`active`) until the path
appears there; the paths are the exact ones in research R3 8.3, so the rule lights up when that allow-list lands.
Absence-based rules read an empty list as "stored and empty", which is only true for a row written after the path was
added: after the allow-list grows, run a pull before trusting them.

The rules and thresholds come from research R3 section 3.1 (property paths: the MIT-licensed "Azure Orphaned
Resources" queries; prices: Microsoft's retail price list); the thresholds Microsoft does not set are constants here.
"""

from __future__ import annotations

import datetime as _dt
from collections import namedtuple
from typing import Any, Callable

from plugins._ea_azure.helpers import shape

PIP = "microsoft.network/publicipaddresses"
DISK = "microsoft.compute/disks"
VM = "microsoft.compute/virtualmachines"
PLAN = "microsoft.web/serverfarms"
SITE = "microsoft.web/sites"
GATEWAY = "microsoft.network/virtualnetworkgateways"
CONNECTION = "microsoft.network/connections"
LB = "microsoft.network/loadbalancers"
APPGW = "microsoft.network/applicationgateways"
FIREWALL = "microsoft.network/azurefirewalls"
DDOS = "microsoft.network/ddosprotectionplans"
DNS_ZONE = "microsoft.network/privatednszones"
POOL = "microsoft.sql/servers/elasticpools"
SQL_DB = "microsoft.sql/servers/databases"
WORKSPACE = "microsoft.operationalinsights/workspaces"

#: thresholds Microsoft does not set; change here
SNAPSHOT_DAYS = 90  # a snapshot older than this is probably a forgotten one
RETENTION_DAYS = 90  # the first 31 days are free; beyond this, with no daily cap, is a choice worth asking about
FREE_TIERS = {"free", "shared", "dynamic"}  # App Service plans that cost nothing while empty
NONPROD = {"dev", "development", "test", "testing", "qa", "uat", "stage", "staging", "nonprod", "non-prod", "sandbox", "poc", "lab"}
ENV_TAGS = ("env", "environment")

Rule = namedtuple("Rule", "title types needs check price")
RULES: dict[str, Rule] = {}


def rule(name: str, title: str, *types: str, needs: list[tuple[str, str]] = (), price: Callable | None = None):
    """Register a check: `check(resource, links_out, links_in, ctx)` returns the evidence text, or None for no finding."""
    def add(check: Callable) -> Callable:
        RULES[name] = Rule(title, types, tuple(needs), check, price)
        return check
    return add


# --- what the allow-list stores ---------------------------------------------------------------

def _paths(spec: Any) -> list[str]:
    if isinstance(spec, str):
        return spec.split("|")
    if isinstance(spec, dict):
        return [p for s in spec.values() for p in _paths(s)]
    return [spec[0], *_paths(spec[1])]


def prop_key(type_: str, path: str) -> str | None:
    """The `props` key `shape.ALLOW` stores `path` under for this type, or None when it is not stored."""
    return next((k for k, s in shape.ALLOW.get(type_, {}).items() if path in _paths(s)), None)


def link_kind(type_: str, path: str) -> str | None:
    """The relation kind `shape.RELATION_PATHS` stores `path` under for this type, or None."""
    return next((k for k, p in shape.RELATION_PATHS.get(type_, []) if path in p.split("|")), None)


def stored(type_: str, path: str) -> bool:
    return bool(prop_key(type_, path) or link_kind(type_, path))


def active(name: str) -> bool:
    return all(stored(t, p) for t, p in RULES[name].needs)


# --- small readers -----------------------------------------------------------------------------

def _tier(r: dict[str, Any]) -> str:
    return str(r["sku"].get("tier") or "").lower()


def _has(links: list[dict[str, Any]], *kinds: str | None) -> bool:
    return any(link["kind"] in kinds for link in links)


def _from(links: list[dict[str, Any]], type_: str) -> bool:
    return any(link["type"] == type_ for link in links)


def _date(value: Any) -> _dt.datetime | None:
    try:
        at = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=_dt.timezone.utc)


def _stored_any(r: dict[str, Any], type_: str, *paths: str) -> bool:
    return any((k := prop_key(type_, p)) and r["props"].get(k) for p in paths)


# --- prices: (service, sku, meter, os) for `prices.price`, or None when no list price is looked up -----

_DISK_SIZES = (4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32767)
_DISK_TIERS = (1, 2, 3, 4, 6, 10, 15, 20, 30, 40, 50, 60, 70, 80)
_DISK_SKUS = {"premium_lrs": ("P", "LRS"), "premium_zrs": ("P", "ZRS"), "standardssd_lrs": ("E", "LRS"),
              "standardssd_zrs": ("E", "ZRS"), "standard_lrs": ("S", "LRS")}


def _disk_price(r: dict[str, Any]) -> tuple | None:
    letter_redundancy = _DISK_SKUS.get(str(r["sku"].get("name") or "").lower())
    size = r["props"].get("size_gb")
    if not letter_redundancy or not isinstance(size, (int, float)) or size > _DISK_SIZES[-1]:
        return None
    letter, redundancy = letter_redundancy
    i = max(next(i for i, s in enumerate(_DISK_SIZES) if size <= s), 3 if letter == "S" else 0)  # S1 to S3 do not exist
    tier = f"{letter}{_DISK_TIERS[i]} {redundancy}"
    # unverified: the meter name is copied for P10 only; E and S disks answer None if Microsoft spells them differently
    return "Storage", tier, f"{tier} Disk", ""


def _plan_price(r: dict[str, Any]) -> tuple | None:
    name = str(r["sku"].get("name") or "")
    if len(name) > 2 and name[0].isalpha() and "v" in name[2:]:  # ARM says P1v3, the price list says "P1 v3"
        name = name.replace("v", " v", 1)
    return ("Azure App Service", name, "", "linux" if r["props"].get("linux") else "windows") if name else None


def _pip_price(r: dict[str, Any]) -> tuple | None:
    if str(r["sku"].get("name") or "").lower() != "standard" or r["props"].get("version") == "IPv6":
        return None
    return "Virtual Network", "Standard", "Standard IPv4 Static Public IP", ""


def _vm_price(r: dict[str, Any]) -> tuple | None:
    size = r["props"].get("size")
    return ("Virtual Machines", size, "", str(r["props"].get("os_type") or "").lower()) if size else None


# --- the rules ---------------------------------------------------------------------------------

@rule("unattached_disk", "Disk attached to nothing", DISK, price=_disk_price)
def _(r, o, i, c):
    tags = " ".join(r["tags"]).lower()
    if (r["props"].get("disk_state") != "Unattached" or _has(o, "managed_by")
            or "asrreplica" in r["name"].lower() or "kubernetes.io-created-for" in tags):  # replication and AKS volumes
        return None
    return f"disk state Unattached, {r['props'].get('size_gb', '?')} GB, {r['sku'].get('name', 'unknown SKU')}, no owner VM"


@rule("disk_behind_stopped_vm", "Disk still billed behind a stopped VM", DISK, price=_disk_price)
def _(r, o, i, c):
    owner = next((c["by_native"].get(link["to_native_id"]) for link in o if link["kind"] == "managed_by"), None)
    if r["props"].get("disk_state") == "Unattached" or not (owner and owner["type"] == VM and owner["state"] == "stopped"):
        return None
    return f"attached to {owner['name']}, which is {owner['state_detail'] or 'stopped'}; the disk is billed while the VM is off"


@rule("unused_public_ip", "Public IP address attached to nothing", PIP,
      needs=[(PIP, p) for p in ("ipConfiguration.id", "natGateway.id", "publicIPPrefix.id")], price=_pip_price)
def _(r, o, i, c):
    kinds = [link_kind(PIP, p) for p in ("ipConfiguration.id", "natGateway.id", "publicIPPrefix.id")]
    if _has(o, *kinds) or _has(i, "public_ip"):
        return None
    return f"{r['sku'].get('name', '')} {r['props'].get('allocation', '')} address, in use by no network card, gateway, NAT gateway or prefix".replace("  ", " ")


@rule("empty_app_service_plan", "App Service plan with no apps", PLAN, price=_plan_price)
def _(r, o, i, c):
    if r["props"].get("sites") != 0 or _tier(r) in FREE_TIERS or not _tier(r):
        return None
    return f"{r['sku'].get('name', '')} plan ({r['sku'].get('tier', '')}) holds 0 apps and is billed as long as it exists"


@rule("stopped_web_app_on_paid_plan", "Stopped web app on a paid plan", SITE)
def _(r, o, i, c):
    plan = next((c["by_native"].get(link["to_native_id"]) for link in o if link["kind"] == "plan"), None)
    if r["state"] != "stopped" or not plan or not _tier(plan) or _tier(plan) in FREE_TIERS:
        return None
    return f"app is stopped, but plan {plan['name']} ({plan['sku'].get('tier', '')}) is billed whether or not any app runs"


@rule("stopped_not_deallocated_vm", "VM stopped but not deallocated", VM, price=_vm_price)
def _(r, o, i, c):
    if r["state_detail"].lower() != "powerstate/stopped":
        return None
    return "power state is stopped, not deallocated: Azure still bills the compute"


@rule("old_snapshot", "Old snapshot", "microsoft.compute/snapshots", needs=[("microsoft.compute/snapshots", "timeCreated")])
def _(r, o, i, c):
    made = _date(r["props"].get(prop_key("microsoft.compute/snapshots", "timeCreated")))
    if not made or (days := (c["now"] - made).days) < SNAPSHOT_DAYS:
        return None
    size = r["props"].get(prop_key("microsoft.compute/snapshots", "diskSizeGB"))
    return f"created {made.date()}, {days} days ago" + (f", {size} GB" if size else "")


@rule("gateway_without_connection", "Network gateway with no connection", GATEWAY,
      needs=[(CONNECTION, "virtualNetworkGateway1.id"), (CONNECTION, "virtualNetworkGateway2.id"),
             (GATEWAY, "vpnClientConfiguration.vpnClientAddressPool.addressPrefixes")])
def _(r, o, i, c):
    pool = prop_key(GATEWAY, "vpnClientConfiguration.vpnClientAddressPool.addressPrefixes")
    if _from(i, CONNECTION) or r["props"].get(pool):
        return None
    return f"{r['props'].get('gateway_type', 'gateway')} gateway: no connection uses it and it has no point-to-site address pool"


@rule("circuit_not_provisioned", "ExpressRoute circuit the provider has not provisioned",
      "microsoft.network/expressroutecircuits", needs=[("microsoft.network/expressroutecircuits", "serviceProviderProvisioningState")])
def _(r, o, i, c):
    state = r["props"].get(prop_key("microsoft.network/expressroutecircuits", "serviceProviderProvisioningState"))
    return None if not state or state.lower() == "provisioned" else f"service provider state is {state}; the circuit is billed from creation"


@rule("unused_nat_gateway", "NAT gateway on no subnet", "microsoft.network/natgateways")
def _(r, o, i, c):
    return None if _has(i, "nat_gateway") else "no subnet uses it"


@rule("lb_without_backend", "Load balancer with no back end", LB,
      needs=[(LB, p) for p in ("backendAddressPools[].properties.backendIPConfigurations[].id",
                               "backendAddressPools[].properties.loadBalancerBackendAddresses[].name", "inboundNatRules[].name")])
def _(r, o, i, c):
    paths = [p for t, p in RULES["lb_without_backend"].needs]
    return None if _stored_any(r, LB, *paths) else f"{r['sku'].get('name', '')} load balancer has no back-end addresses and no inbound NAT rules".strip()


@rule("appgw_empty_backend", "Application gateway with empty back-end pools", APPGW,
      needs=[(APPGW, p) for p in ("backendAddressPools[].properties.backendAddresses[].ipAddress",
                                  "backendAddressPools[].properties.backendAddresses[].fqdn",
                                  "backendAddressPools[].properties.backendIPConfigurations[].id")])
def _(r, o, i, c):
    paths = [p for t, p in RULES["appgw_empty_backend"].needs]
    return None if _stored_any(r, APPGW, *paths) else "every back-end pool is empty"


@rule("firewall_deallocated", "Azure Firewall deallocated", FIREWALL, needs=[(FIREWALL, "virtualHub.id")])
def _(r, o, i, c):
    if _has(o, "subnet", "public_ip", link_kind(FIREWALL, "virtualHub.id")):  # a hub firewall has no ip configurations
        return None
    return "no IP configuration and not in a virtual hub: the firewall is deallocated, which is billed less but not nothing"


@rule("ddos_plan_unused", "DDoS protection plan protecting no network", DDOS, needs=[(DDOS, "virtualNetworks[].id")])
def _(r, o, i, c):
    return None if _has(o, link_kind(DDOS, "virtualNetworks[].id")) else "no virtual network is protected by it"


@rule("private_endpoint_disconnected", "Private endpoint that is disconnected", "microsoft.network/privateendpoints")
def _(r, o, i, c):
    states = r["props"].get("connection_states") or []
    return "connection state is Disconnected" if any(str(s).lower() == "disconnected" for s in states) else None


@rule("private_dns_zone_unlinked", "Private DNS zone linked to no network", DNS_ZONE, needs=[(DNS_ZONE, "numberOfVirtualNetworkLinks")])
def _(r, o, i, c):
    n = r["props"].get(prop_key(DNS_ZONE, "numberOfVirtualNetworkLinks"))
    return "0 virtual network links" if n == 0 else None


@rule("empty_elastic_pool", "SQL elastic pool with no database", POOL, needs=[(SQL_DB, "elasticPoolId")])
def _(r, o, i, c):
    return None if _from(i, SQL_DB) else "no database is in the pool"


@rule("unused_nsg", "Network security group on nothing", "microsoft.network/networksecuritygroups")
def _(r, o, i, c):
    return None if _has(i, "nsg") else "no subnet and no network card uses it"


@rule("unused_route_table", "Route table on nothing", "microsoft.network/routetables")
def _(r, o, i, c):
    return None if _has(i, "route_table") else "no subnet uses it"


@rule("expired_certificate", "Expired App Service certificate", "microsoft.web/certificates",
      needs=[("microsoft.web/certificates", "expirationDate")])
def _(r, o, i, c):
    end = _date(r["props"].get(prop_key("microsoft.web/certificates", "expirationDate")))
    return f"expired {end.date()}" if end and end < c["now"] else None


@rule("workspace_long_retention_no_cap", "Log workspace with long retention and no daily cap", WORKSPACE,
      needs=[(WORKSPACE, "retentionInDays"), (WORKSPACE, "workspaceCapping.dailyQuotaGb")])
def _(r, o, i, c):
    days = r["props"].get(prop_key(WORKSPACE, "retentionInDays"))
    cap = r["props"].get(prop_key(WORKSPACE, "workspaceCapping.dailyQuotaGb"))
    if not isinstance(days, (int, float)) or days <= RETENTION_DAYS or (isinstance(cap, (int, float)) and cap >= 0):
        return None
    return f"keeps data {int(days)} days (the first 31 are free) and has no daily cap"


@rule("premium_in_nonprod", "Costly choice in a non-production place", DISK, "microsoft.storage/storageaccounts", PLAN, SQL_DB)
def _(r, o, i, c):
    env = next((v.lower() for k, v in r["tags"].items() if k.lower() in ENV_TAGS), "")
    sku = str(r["sku"].get("name") or "").lower()
    why = ("a Premium or Ultra disk" if r["type"] == DISK and sku.startswith(("premium", "ultra"))
           else "geo-redundant storage" if r["type"].endswith("storageaccounts") and any(x in sku for x in ("_grs", "_gzrs", "_ragrs", "_ragzrs"))
           else "zone redundancy" if r["type"] in (PLAN, SQL_DB) and r["props"].get("zone_redundant") is True else "")
    return f"{why} where the tag says {env}" if why and env in NONPROD else None


# --- running them ------------------------------------------------------------------------------

def findings(rows: list[dict[str, Any]], out: dict[str, list], inn: dict[str, list], as_of: str | None,
             now: _dt.datetime | None = None) -> list[dict[str, Any]]:
    """One finding per rule that fires on a live resource: `rows` as `store._resource_json` makes them, `out` and `inn`
    the links out of and into each by row id. Findings are ordered by rule, then name."""
    ctx = {"by_native": {r["native_id"]: r for r in rows}, "now": now or _dt.datetime.now(_dt.timezone.utc)}
    found = []
    for name, rl in RULES.items():
        if not active(name):
            continue
        for r in rows:
            if r["type"] in rl.types and (why := rl.check(r, out.get(r["id"], []), inn.get(r["id"], []), ctx)):
                found.append({
                    "rule": name, "title": rl.title, "id": r["id"], "native_id": r["native_id"], "name": r["name"],
                    "label": r["label"], "subscription": r["subscription"]["name"], "resource_group": r["resource_group"]["name"],
                    "region": r["region"], "evidence": why, "as_of": as_of, "_price": rl.price(r) if rl.price else None})
    return sorted(found, key=lambda f: (f["rule"], f["name"].lower(), f["native_id"]))


def priced(found: list[dict[str, Any]]) -> dict[str, Any]:
    """Put `usd_month` (list price, USD, 730 hours) on each finding the price list can answer, drop `_price`, and total.

    The total is the sum of the prices found; `unpriced` counts the rest. The one place here that may reach the network
    (`prices.price`, a region and a SKU, cached seven days); with no internet every `usd_month` is simply absent.
    """
    from plugins._ea_azure.helpers import cost, prices

    memo: dict[tuple, Any] = {}
    total, count, oldest = 0.0, 0, None
    for f in found:
        hint = f.pop("_price", None)
        if hint and (f["region"], hint) not in memo:
            memo[(f["region"], hint)] = prices.price(f["region"], *hint)
        p = memo.get((f["region"], hint)) if hint else None
        if p and p["usd_month"] is not None:
            f["usd_month"] = p["usd_month"]
            total, count = total + p["usd_month"], count + 1
            oldest = min(filter(None, (oldest, p["fetched_at"])))
    cost.annotate(found)  # what last month cost, where a cost answer for it is already kept; no call to Azure
    return {"findings": found, "total_usd_month": round(total, 2), "priced": count, "unpriced": len(found) - count,
            "priced_at": oldest}
