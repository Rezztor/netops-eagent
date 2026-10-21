"""The monthly cost of what a plan creates, at list price (ADR 0062, build contract D9, D11).

Prices come from `prices.py` (the read plugin's module, copied into the image at build), which asks only
`prices.azure.com` and keeps an answer a week. Three kinds of resource: `FREE` (no charge of its own), `USAGE`
(billed by use, so no fixed monthly figure; listed with a note and counted as zero) and priced ones, which need a
price lookup. A type that is in none of these, or a priced one the price list cannot answer, is *unknown*, and
`policy` refuses a plan with an unknown price unless the rails say `allow_unknown_price`.

Only creates are priced. An update here never raises a price (size and SKU never change; capacity goes up only
through a simple action, which prices itself).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Callable

from cloud_executor import gate

FREE = frozenset({
    "azurerm_resource_group", "azurerm_virtual_network", "azurerm_subnet", "azurerm_network_security_group",
    "azurerm_network_security_rule", "azurerm_route_table", "azurerm_route", "azurerm_virtual_network_peering",
    "azurerm_subnet_network_security_group_association", "azurerm_subnet_route_table_association",
    "azurerm_network_interface", "azurerm_linux_web_app", "azurerm_monitor_diagnostic_setting",
    "azurerm_consumption_budget_subscription", "azurerm_consumption_budget_resource_group", "azapi_update_resource",
    "azurerm_private_dns_zone_virtual_network_link",
})
USAGE = frozenset({
    "azurerm_storage_account", "azurerm_log_analytics_workspace", "azurerm_network_watcher_flow_log",
    "azurerm_private_dns_zone",
})

Lookup = Callable[..., Any]


def _lookup() -> Lookup | None:
    try:
        from cloud_executor import prices  # noqa: PLC0415 - copied in at build; absent in a bare checkout
    except ImportError:
        return None
    return prices.price


def _after(rc: dict[str, Any]) -> dict[str, Any]:
    return (rc.get("change") or {}).get("after") or {}


def _vm(after: dict[str, Any]) -> tuple[str, str, str, str] | None:
    size = str(after.get("size") or "")
    sku = re.sub(r"^Standard_", "", size).replace("_", " ")
    return ("Virtual Machines", sku, "", "linux") if sku else None


def _plan(after: dict[str, Any]) -> tuple[str, str, str, str] | None:
    sku = re.sub(r"^([A-Za-z]+\d+)(v\d+)$", r"\1 \2", str(after.get("sku_name") or ""))
    return ("Azure App Service", sku, "", "linux") if sku else None


def _public_ip(after: dict[str, Any]) -> tuple[str, str, str, str] | None:
    # unverified: the meter name of a Standard static IPv4 address, from the price list's own wording; proved in test session 2
    return ("Virtual Network", "Standard", "Standard IPv4 Static Public IP", "") if after.get("sku", "Standard") == "Standard" else None


def _endpoint(after: dict[str, Any]) -> tuple[str, str, str, str] | None:
    # unverified: skuName and meterName of a private endpoint, from the price list's own wording; proved in test session 2
    return ("Virtual Network", "Standard Private Endpoint", "Standard Private Endpoint", "")


#: type -> (how to turn the planned attributes into (service, sku, meter, os), the label shown to the engineer)
PRICED: dict[str, tuple[Callable[[dict[str, Any]], tuple[str, str, str, str] | None], str]] = {
    "azurerm_linux_virtual_machine": (_vm, "virtual machine, compute only (disks, data transfer extra)"),
    "azurerm_service_plan": (_plan, "app service plan"),
    "azurerm_public_ip": (_public_ip, "static public IP address"),
    "azurerm_private_endpoint": (_endpoint, "private endpoint, before data processed"),
}


def estimate(plan: dict[str, Any], lookup: Lookup | None = None) -> dict[str, Any]:
    """`{monthly_usd, items: [{address, meter, usd_month, note?}], unknown: [address], priced_at}` for the creates."""
    lookup = lookup or _lookup()
    items: list[dict[str, Any]] = []
    unknown: list[str] = []
    total = 0.0
    for rc in sorted(gate.managed_changes(plan), key=lambda r: str(r.get("address"))):
        address, rtype = str(rc.get("address")), str(rc.get("type"))
        if gate.change_action(rc) != "create" or rtype in FREE:
            continue
        if rtype in USAGE:
            items.append({"address": address, "meter": rtype, "usd_month": 0.0, "note": "billed by use; no fixed monthly price"})
            continue
        entry = PRICED.get(rtype)
        key = entry[0](_after(rc)) if entry else None
        price = None
        if key and lookup:
            try:
                price = lookup(_after(rc).get("location") or "", key[0], key[1], meter=key[2], os=key[3])
            except Exception:  # noqa: BLE001 - a price that cannot be read is an unknown price, never a crash
                price = None
        if not price or price.get("usd_month") is None:
            unknown.append(address)
            continue
        items.append({"address": address, "meter": f"{entry[1]}: {key[1]}", "usd_month": price["usd_month"]})
        total += price["usd_month"]
    return {"monthly_usd": round(total, 2), "items": items, "unknown": unknown,
            "priced_at": dt.datetime.now(dt.timezone.utc).date().isoformat()}


def policy(cost: dict[str, Any], rails: dict[str, Any], committed_usd: float) -> tuple[list[dict[str, Any]], list[str]]:
    """The cost checks and refusals. `committed_usd` is what earlier applied changes already add each month."""
    refusals: list[str] = []
    per, total = float(rails["usd_month_per_change"]), float(rails["usd_month_total"])
    if cost["unknown"] and not rails["allow_unknown_price"]:
        refusals.append(f"The price of {', '.join(cost['unknown'])} is unknown, and an unknown price is refused.")
    if cost["monthly_usd"] > per:
        refusals.append(f"{cost['monthly_usd']:.2f} USD a month is more than the {per:.2f} USD limit for one change.")
    if committed_usd + cost["monthly_usd"] > total:
        refusals.append(f"{committed_usd + cost['monthly_usd']:.2f} USD a month across approved changes is more than the "
                        f"{total:.2f} USD limit.")
    checks = [{"label": "Cost", "ok": not refusals, **({"detail": refusals[0]} if refusals else {})}]
    return checks, refusals
