"""What to check before anything is drafted: findings computed from the stored inventory, each with its evidence rows
and the date of the pull. The model explains them; it never does the arithmetic (CIDR overlap, name patterns, tag
coverage, prices).

Nothing here reaches Azure. The inventory comes from `store`, the on-prem addresses are two read-only queries on the
device inventory, and the one outside call is `prices.price` (Microsoft's public list price, `None` when it cannot
say). A check whose data is not stored says so (`not_checked`) rather than passing.

Written from Microsoft's published rules, not run against a tenant: the name rules (Azure resource name rules page,
read 2026-10-03), the abbreviations (Cloud Adoption Framework "resource abbreviations"), five reserved addresses per
subnet, and the reserved subnet names. The name pattern is inferred from the estate, so it is only as good as the
names in it.
"""

from __future__ import annotations

import collections
import ipaddress
import os
import re
from typing import Any, NamedTuple

from plugins._ea_azure.helpers import categories, prices, store
from plugins._ea_inventory.helpers import store as inv

SCAN_CAP, EVIDENCE = 4000, 12
POOLS = tuple(ipaddress.ip_network(p) for p in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
SUBNET = "microsoft.network/virtualnetworks/subnets"
VNET, RTABLE, PLAN, VM = ("microsoft.network/virtualnetworks", "microsoft.network/routetables",
                          "microsoft.web/serverfarms", "microsoft.compute/virtualmachines")
#: subnets a hub carries, with the smallest size Microsoft documents
RESERVED_SUBNETS = {"GatewaySubnet": 27, "AzureFirewallSubnet": 26, "AzureBastionSubnet": 26,
                    "AzureFirewallManagementSubnet": 26, "RouteServerSubnet": 27}
PRICE_NOTE = ("list price from Microsoft's public price list, 730 hours a month, before data transfer, usage-based "
              "charges and your discounts")
_VNET_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?"


class Rule(NamedTuple):
    abbr: str  # Cloud Adoption Framework abbreviation
    lo: int
    hi: int
    pattern: str
    scope: str  # where the name must be unique
    lower: bool = False
    note: str = ""


RULES: dict[str, Rule] = {
    VNET: Rule("vnet", 2, 64, _VNET_NAME, "resource group"),
    SUBNET: Rule("snet", 1, 80, _VNET_NAME, "virtual network"),
    "microsoft.network/networksecuritygroups": Rule("nsg", 1, 80, _VNET_NAME, "resource group"),
    RTABLE: Rule("rt", 1, 80, _VNET_NAME, "resource group"),
    "microsoft.network/publicipaddresses": Rule("pip", 1, 80, _VNET_NAME, "resource group"),
    VM: Rule("vm", 1, 64, r"[A-Za-z0-9-]*[A-Za-z0-9]", "resource group",
             note="15 characters at most for a Windows machine, and a Windows name cannot contain a period"),
    "microsoft.storage/storageaccounts": Rule("st", 3, 24, r"[a-z0-9]+", "all of Azure", True),
    "microsoft.keyvault/vaults": Rule("kv", 3, 24, r"[A-Za-z](?:[A-Za-z0-9]|-(?!-))*[A-Za-z0-9]", "all of Azure"),
    "microsoft.web/sites": Rule("app", 2, 60, r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", "all of Azure (its web address)"),
    PLAN: Rule("asp", 1, 60, r"[A-Za-z0-9-]+", "resource group"),
    "microsoft.sql/servers": Rule("sql", 3, 63, r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", "all of Azure", True),
    "microsoft.sql/servers/databases": Rule("sqldb", 1, 128, r"[^<>*%&:\\/?\x00-\x1f]*[^<>*%&:\\/?\x00-\x1f. ]", "server"),
    "microsoft.containerservice/managedclusters": Rule("aks", 1, 63, r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?", "resource group"),
    "microsoft.operationalinsights/workspaces": Rule("log", 4, 63, r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", "resource group"),
    "microsoft.containerregistry/registries": Rule("cr", 5, 50, r"[A-Za-z0-9]+", "all of Azure"),
    "microsoft.compute/disks": Rule("disk", 1, 80, r"[A-Za-z0-9_-]+", "resource group"),
}
#: no delimiter between the parts of a name: Azure allows letters and digits only
_PLAIN = {"microsoft.storage/storageaccounts", "microsoft.containerregistry/registries"}
#: what a person says -> the Azure type; `categories.TYPE_ALIASES` plus the types that have name rules
NEEDS = {**categories.TYPE_ALIASES, "app service plan": PLAN, "log analytics workspace": "microsoft.operationalinsights/workspaces",
         "sql server": "microsoft.sql/servers", "container registry": "microsoft.containerregistry/registries",
         "disk": "microsoft.compute/disks"}


class Refused(ValueError):
    """An argument that is not usable; the message is the sentence the agent gets."""


def slug(text: str, limit: int = 20) -> str:
    """Lower-case words joined by hyphens, whole words only while they fit in `limit` (a first word longer than that is cut)."""
    out = ""
    for word in re.findall(r"[a-z0-9]+", str(text).lower()):
        if out and len(out) + 1 + len(word) > limit:
            break
        out = f"{out}-{word}" if out else word[:limit]
    return out


def _finding(check: str, summary: str, evidence: Any = None, level: str = "info") -> dict[str, Any]:
    out: dict[str, Any] = {"check": check, "level": level, "summary": summary}
    return {**out, "evidence": evidence} if evidence else out


def _shares(names: list[str], pred: Any) -> bool:
    return sum(1 for n in names if pred(n)) * 2 >= len(names)


# --- the stored inventory ----------------------------------------------------------------------------------------

def _scan(filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    found, offset = [], 0
    while True:
        page, total = store.search(filters, 500, offset)
        found += page
        offset += 500
        if offset >= total or len(found) >= SCAN_CAP:
            return found, total


def _net(text: Any) -> ipaddress.IPv4Network | None:
    try:
        n = ipaddress.ip_network(str(text).strip(), strict=False)
    except ValueError:
        return None
    return n if n.version == 4 and n.prefixlen > 0 else None  # a default route is not a network anyone owns


def _onprem() -> list[tuple[ipaddress.IPv4Network, str]]:
    """Networks the on-prem inventory knows: each observed interface address (as its network) and each management
    address (as a host). A decommissioned device gives its address back."""
    with inv.transaction() as conn:
        ifaces = inv.rows(conn, "SELECT d.name AS device, i.name AS port, i.ip_prefixes FROM inventory_interface i "
                                "JOIN inventory_device d ON d.id = i.device_id WHERE d.status <> 'decommissioned' "
                                "AND cardinality(i.ip_prefixes) > 0")
        hosts = inv.rows(conn, "SELECT name AS device, mgmt_ip FROM inventory_device "
                               "WHERE mgmt_ip IS NOT NULL AND status <> 'decommissioned'")
    out = [(n, f"on-prem {r['device']} {r['port']}") for r in ifaces for p in r["ip_prefixes"] if (n := _net(p))]
    return out + [(n, f"on-prem {r['device']} management address") for r in hosts if (n := _net(r["mgmt_ip"]))]


def _used(by_type: dict[str, list[dict[str, Any]]]) -> list[tuple[ipaddress.IPv4Network, str]]:
    used = []
    for v in by_type[VNET]:
        used += [(n, f"virtual network {v['name']}") for p in v["props"].get("address_space") or [] if (n := _net(p))]
        for peer in v["props"].get("peerings") or []:
            used += [(n, f"peering {peer.get('name')} of {v['name']}") for p in peer.get("remote_address_space") or []
                     if (n := _net(p))]
    for t in by_type[RTABLE]:
        used += [(n, f"route {r.get('name')} in {t['name']}") for r in t["props"].get("routes") or []
                 if (n := _net(r.get("prefix")))]
    for g in by_type["microsoft.network/localnetworkgateways"]:
        used += [(n, f"local gateway {g['name']}") for p in g["props"].get("address_space") or [] if (n := _net(p))]
    used += [(n, f"virtual hub {h['name']}") for h in by_type["microsoft.network/virtualhubs"]
             if (n := _net(h["props"].get("address_prefix")))]
    return used + _onprem()


def _free(pool: ipaddress.IPv4Network, size: int, used: list[ipaddress.IPv4Network]) -> ipaddress.IPv4Network | None:
    """First aligned block of /size in the pool that touches nothing used (first fit, walking the used list once)."""
    step, end = 1 << (32 - size), int(pool.broadcast_address) + 1
    cur = -(-int(pool.network_address) // step) * step
    for n in sorted(used, key=lambda n: int(n.network_address)):
        a, b = int(n.network_address), int(n.broadcast_address) + 1
        if b <= cur:
            continue
        if a >= cur + step:
            break
        cur = -(-b // step) * step
    return ipaddress.ip_network((cur, size)) if cur + step <= end else None


# --- the checks ----------------------------------------------------------------------------------------------------

def _address(size: int, by_type: dict[str, list[dict[str, Any]]], unread: list[str]) -> list[dict[str, Any]]:
    used = _used(by_type)
    nets = [n for n, _ in used]
    free = {str(p): str(b) if (b := _free(p, size, nets)) else None for p in POOLS}
    first = next((p for p in free if free[p]), None)
    evidence = {"asked_size": f"/{size}", "usable_addresses_as_a_subnet": max((1 << (32 - size)) - 5, 0),
                "first_free_block_in_each_range": free,
                "networks_counted": [{"network": str(n), "from": w} for n, w in used[:EVIDENCE * 3]],
                "networks_counted_total": len(used),
                "not_counted": ["routes learned over a gateway or BGP (Azure does not list them here)",
                                "address spaces in subscriptions the last pull did not read"
                                + (f": {', '.join(unread)}" if unread else ""),
                                "anything the on-prem inventory has not observed"]}
    summary = (f"First free /{size}: {free[first]} (in {first}); none of the {len(used)} networks stored in Azure routes, "
               "peerings, local gateways, hubs or the on-prem inventory overlaps it." if first else
               f"No free /{size} in 10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16 against the stored networks.")
    out = [_finding("address_block", summary, evidence, "info" if first else "warn")]
    if size < 16:
        out.append(_finding("address_size", f"A /{size} is a very large network; Microsoft's planning guidance says to avoid "
                            "them. Ask whether /16 or smaller will do.", level="warn"))
    return out


def _hub(by_type: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    nets = {}
    for s in by_type[SUBNET]:
        if s["name"] in RESERVED_SUBNETS and s["parent"]:
            nets.setdefault(s["parent"]["name"], []).append(s["name"])
    transit = [{"network": v["name"], "peering": p.get("name")} for v in by_type[VNET]
               for p in v["props"].get("peerings") or [] if p.get("allow_gateway_transit")]
    default = [{"route_table": t["name"], "next_hop": r.get("next_hop_ip"), "route": r.get("name")}
               for t in by_type[RTABLE] for r in t["props"].get("routes") or []
               if r.get("prefix") == "0.0.0.0/0" and r.get("next_hop_type") == "VirtualAppliance"]
    dns = [{"network": v["name"], "dns_servers": v["props"]["dns_servers"]} for v in by_type[VNET] if v["props"].get("dns_servers")]
    evidence = {"networks_with_hub_subnets": nets, "peerings_with_gateway_transit": transit,
                "default_route_to_an_appliance": default, "networks_with_custom_dns": dns[:EVIDENCE]}
    found = nets or transit or default
    return _finding("hub", ("Existing hub parts found; a new network in this estate usually peers to them and follows their "
                            "route table and DNS servers." if found else "No network in the inventory holds a firewall, "
                            "gateway or bastion subnet, and no route table sends 0.0.0.0/0 to an appliance."),
                    {k: v for k, v in evidence.items() if v})


def _tags(scope: list[dict[str, Any]]) -> dict[str, Any]:
    taggable = [r for r in scope if r["type"] != SUBNET]
    names: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in taggable:
        for k, v in r["tags"].items():
            names[k.lower()][v] += 1
    if not taggable:
        return _finding("tags", "No resources stored to read a tag convention from.")
    rows = [{"tag": k, "resources": sum(c.values()), "percent": round(100 * sum(c.values()) / len(taggable)),
             **({"values": sorted(c)} if len(c) <= 8 else {})}
            for k, c in sorted(names.items(), key=lambda kv: -sum(kv[1].values()))[:EVIDENCE]]
    common = [r["tag"] for r in rows if r["percent"] >= 50]
    return _finding("tags", (f"Tags on at least half of the {len(taggable)} resources: {', '.join(common)}. Ask for their "
                             "values and use the spellings already in use." if common else
                             "No tag is on half of the resources; propose owner, env and costcentre and say it is a proposal."),
                    rows, "info" if common else "warn")


def _region(scope: list[dict[str, Any]], asked: str) -> tuple[str, list[dict[str, Any]]]:
    counts = collections.Counter(r["region"] for r in scope if r["region"] and r["region"] != "global")
    top = counts.most_common(1)[0][0] if counts else ""
    region = asked or top
    evidence = [{"region": k, "resources": n} for k, n in counts.most_common(EVIDENCE)]
    if not region:
        return "", [_finding("region", "No region could be inferred; ask which one.", level="warn")]
    if asked and top and asked != top:
        return region, [_finding("region", f"Most resources are in {top}, not {asked}. Confirm {asked} is intended.", evidence, "warn")]
    return region, [_finding("region", f"{region}: {'asked for' if asked else 'where most of the inventory is'}; "
                             "an allowed-locations policy is not read (see policy_locks_budget).", evidence)]


def _name_for(type_: str, existing: list[dict[str, Any]], purpose: str, env: str, region: str) -> tuple[str, dict[str, Any]]:
    rule, names = RULES[type_], [r["name"] for r in existing]
    estate = len(names) >= 2
    delims = collections.Counter(d for n in names for d in ("-", "_") if d in n)
    delim = "" if type_ in _PLAIN else "-" if not estate else (
        delims.most_common(1)[0][0] if delims and _shares(names, lambda n: any(d in n for d in "-_")) else "")
    first = collections.Counter(re.split(r"[-_\d]", n, maxsplit=1)[0].lower() for n in names)
    if delim:  # the first word, when half the names start with it
        prefix = first.most_common(1)[0][0] if estate and first and first.most_common(1)[0][1] * 2 >= len(names) else rule.abbr
    else:  # no delimiter: what all the names start with, before their number
        prefix = os.path.commonprefix([re.sub(r"\d+$", "", n.lower()) for n in names]) if estate else ""
        prefix = prefix if len(prefix) >= 2 else rule.abbr
    numbered = estate and _shares(names, lambda n: re.search(r"\d+$", n) is not None)
    width = max((len(m.group()) for n in names if (m := re.search(r"\d+$", n))), default=3)
    has_env = not estate or _shares(names, lambda n: env in n.lower())
    has_region = not estate or _shares(names, lambda n: region and region in n.lower())
    top = max((int(m.group()) for n in names if n.lower().startswith(prefix) and (m := re.search(r"\d+$", n))), default=0)
    purpose = slug(purpose, 14).replace("-", "") if type_ in _PLAIN else slug(purpose, 14)
    parts = {"prefix": prefix, "purpose": purpose, "env": env if has_env else "",
             "region": region if has_region and type_ not in _PLAIN else "",
             "number": f"{top + 1:0{width}d}" if numbered or not estate else ""}

    def build() -> str:
        name = delim.join(p for p in parts.values() if p)
        return name.lower() if rule.lower else name

    shortened = []
    for part in ("region", "env"):  # a name that does not fit loses its region, then its environment
        if len(build()) > rule.hi and parts[part]:
            parts[part], shortened = "", [*shortened, part]
    name = build()  # still too long or otherwise invalid: the caller's rule check says so
    return name, {"basis": "the names already in use" if estate else "Cloud Adoption Framework default (the estate has no pattern for this type)",
                  "examples": names[:6], "delimiter": delim or "none", "prefix": prefix, "number_width": width if numbered else 0,
                  "env_in_names": has_env, "region_in_names": has_region, **({"left_out_to_fit": shortened} if shortened else {})}


def _rule_problems(type_: str, name: str) -> list[str]:
    rule = RULES[type_]
    bad = []
    if not rule.lo <= len(name) <= rule.hi:
        bad.append(f"{len(name)} characters; Azure allows {rule.lo} to {rule.hi}")
    if not re.fullmatch(rule.pattern, name):
        bad.append("characters or ends Azure does not allow for this type")
    return bad


def _skus(type_: str, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    c: collections.Counter = collections.Counter()
    for r in existing:
        c[(r["props"].get("size") if type_ == VM else (r["sku"] or {}).get("name") or "", (r["props"].get("os_type") or "")
           if type_ == VM else ("linux" if r["props"].get("linux") else "windows" if type_ == PLAN else ""))] += 1
    return [{"sku": s, "os": o, "count": n} for (s, o), n in c.most_common(3) if s]


def _price_rows(type_: str, skus: list[dict[str, Any]], region: str) -> list[dict[str, Any]]:
    rows = []
    for s in skus:
        if type_ == VM:  # an unknown operating system is not guessed: Windows costs more than Linux
            p = prices.price(region, "Virtual Machines", s["sku"], os=s["os"].lower()) if s["os"].lower() in ("linux", "windows") else None
        elif type_ == PLAN:  # the plan says "P1v3"; the price list says "P1 v3"
            p = prices.price(region, "Azure App Service", re.sub(r"^([A-Za-z]+\d+)(v\d+)$", r"\1 \2", s["sku"]), os=s["os"])
        else:
            continue
        rows.append({"sku": s["sku"], "os": s["os"], **({k: p[k] for k in ("usd_hour", "usd_month", "fetched_at")} if p else {"price": "unknown"})})
    return rows


def run(purpose: str, env: str, region: str, needs: list[str], size: int | None, subscription: str) -> dict[str, Any]:
    """The findings for one planned build. `needs` are Azure types already resolved through `NEEDS`; raises `Refused`
    for a subscription the inventory does not hold. The caller has checked that a pull exists."""
    asof = store.as_of()
    if subscription:
        # by name or by GUID: the change tool asks for the GUID, so the agent often has only that
        known = [s for s in store.subscriptions() if subscription.lower() in (s["name"].lower(), s["native_id"].lower())]
        if not known:
            raise Refused(f"No subscription {subscription} in the stored inventory; it holds: "
                          + ", ".join(s["name"] for s in asof["subscriptions"]) + ".")
    scope, total = _scan({"subscription": subscription} if subscription else {})
    by_type: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in scope:
        by_type[r["type"]].append(r)
    unread = [s["name"] for s in asof["subscriptions"] if not s["readable"] or not s["last_read_at"]]
    chosen, findings = _region(scope, region)
    findings.append(_tags(scope))
    if len(asof["subscriptions"]) > 1 and not subscription:
        findings.append(_finding("scope", "No subscription was named, so the names, tags and regions above come from all of "
                                 f"{len(asof['subscriptions'])} subscriptions; name one to narrow them.", level="warn"))
    if total > len(scope):
        findings.append(_finding("scope", f"Only the first {len(scope)} of {total} resources were read for these checks.", level="warn"))
    if size or VNET in needs:
        findings += _address(size or 24, by_type, unread)
    if VNET in needs or SUBNET in needs:
        findings.append(_hub(by_type))
        findings.append(_finding("reserved_subnets", "A subnet loses 5 addresses to Azure. These subnets need these exact names and "
                                 "at least this size.", {n: f"/{p} or larger" for n, p in RESERVED_SUBNETS.items()}))
    # look-alikes, removed ones included: any resource whose name contains one of the purpose's words
    near = list({r["id"]: r for w in re.findall(r"[a-z0-9]{3,}", purpose.lower())[:3]
                 for r in store.search({"q": w, "include_removed": "yes"}, 60)[0]}.values())
    for type_ in needs:
        label = categories.label_of(type_)
        existing = by_type[type_]
        if type_ in RULES:
            name, basis = _name_for(type_, existing, purpose, slug(env, 10), chosen)
            bad = _rule_problems(type_, name)
            findings.append(_finding("naming", f"{label}: suggest `{name}`." + (" It breaks Azure's rules; shorten the purpose." if bad else ""), basis,
                                     "warn" if bad else "info"))
            rule = RULES[type_]
            findings.append(_finding("name_rules", f"{label} names: {rule.lo} to {rule.hi} characters, unique in {rule.scope}."
                                     + (f" {rule.note}." if rule.note else "") + " Global uniqueness can only be proven by asking Azure.",
                                     {"suggested_name_problems": bad} if bad else None, "warn" if bad else "info"))
            norm, key = (re.sub(r"[^a-z0-9]|\d+$", "", x.lower()) for x in (name, slug(purpose, 14)))
            twins = [{"name": r["name"], "type": r["label"], "resource_group": r["resource_group"]["name"], "removed_at": r["removed_at"]}
                     for r in near if r["type"] == type_ and (re.sub(r"[^a-z0-9]|\d+$", "", r["name"].lower()) == norm
                                                              or key in re.sub(r"[^a-z0-9]", "", r["name"].lower()))]
            if twins:
                findings.append(_finding("duplicates", f"{len(twins)} {label.lower()}(s) have this name apart from the number or delimiter, or "
                                         "carry the purpose in theirs (removed ones included); ask whether this is a re-creation.",
                                         twins[:EVIDENCE], "warn"))
        else:
            findings.append(_finding("naming", f"{label}: no name rules are stored for this type; follow the estate's names.",
                                     [r["name"] for r in existing[:6]] or None, "not_checked"))
        zones = collections.Counter(",".join(map(str, r["props"].get("zones") or [])) or "none" for r in existing)
        if existing:
            findings.append(_finding("zones", f"Zones of the {len(existing)} existing {label.lower()}(s). Whether the region offers zones is not checked.",
                                     [{"zones": z, "resources": n} for z, n in zones.most_common(EVIDENCE)]))
        skus = _skus(type_, existing)
        if skus:
            priced = _price_rows(type_, skus, chosen) if chosen else []
            findings.append(_finding("sku_in_use", f"SKUs in use for {label.lower()}: " + ", ".join(f"{s['sku']} x{s['count']}" for s in skus) + ".", skus))
            if priced:
                found = any("usd_month" in p for p in priced)
                findings.append(_finding("list_price", (f"Per month, {PRICE_NOTE}." if found else "Price unknown: Microsoft's price list could not be "
                                         "reached, or it has no single price for that SKU."), priced, "info" if found else "not_checked"))
    workspaces = by_type["microsoft.operationalinsights/workspaces"]
    if workspaces:
        findings.append(_finding("log_workspaces", "Existing Log Analytics workspaces: new resources usually log to one of these.",
                                 [{"name": w["name"], "region": w["region"]} for w in workspaces[:EVIDENCE]]))
    findings.append(_finding("policy_locks_budget", "Policy assignments (including allowed locations and required tags), resource locks and "
                             "budgets are not read yet. Say so, and ask the engineer or check the portal.", level="not_checked"))
    pulled = asof["last_pull"]["started_at"]
    return {"findings": [f if f["check"] == "list_price" else {**f, "as_of": pulled} for f in findings],  # a price carries its own date
            "region": chosen, "resources_read": len(scope)}
