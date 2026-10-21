"""The appliance's network list: what it says, which network an address is in, and why a connect failed.

The appliance console writes `/etc/rezztor/networks/networks.json`; the executor reads it and
the application asks the executor for it. This module is the one place that reads its shape,
finds an address in it, classifies a failed connect and words the result, so the executor
(which classifies) and the application (which words) cannot drift apart. Pure standard library:
the executor image copies this directory.

A missing or broken list means "nothing known": every function then behaves as it did before
the list existed. No secret is ever in the list.
"""

from __future__ import annotations

import ipaddress
from typing import Any

OK = "ok"
NO_ROUTE_KNOWN = "no_route_known"
NO_ANSWER_ON_LINK = "no_answer_on_link"
NO_ANSWER_VIA_GATEWAY = "no_answer_via_gateway"
REFUSED = "refused"
NETWORK_DOWN = "network_down"

ROLES = frozenset({"internet", "users", "devices"})


def _net(value: Any) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    try:
        return ipaddress.ip_network(str(value), strict=False)
    except ValueError:
        return None


def parse(document: Any) -> dict[str, Any]:
    """The list as the application uses it. Anything unusable is dropped, never raised."""
    entries = document.get("networks") if isinstance(document, dict) else None
    networks: list[dict[str, Any]] = []
    for raw in entries if isinstance(entries, list) else []:
        if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
            continue
        roles = [r for r in raw.get("roles") or [] if r in ROLES] if isinstance(raw.get("roles"), list) else []
        routed = [str(s) for s in raw.get("routed_subnets") or [] if _net(s)] if isinstance(raw.get("routed_subnets"), list) else []
        networks.append({
            "name": str(raw["name"]).strip(),
            "roles": roles,
            "interface": str(raw.get("interface") or ""),
            "parent": raw.get("parent") or None,
            "vlan": raw.get("vlan") if isinstance(raw.get("vlan"), int) else None,
            "address": str(raw.get("address") or ""),
            "gateway": raw.get("gateway") or None,
            "subnet": str(raw.get("subnet") or "") if _net(raw.get("subnet")) else "",
            "routed_subnets": routed,
            "state": "down" if raw.get("state") == "down" else "up",
        })
    generated = document.get("generated_at") if isinstance(document, dict) else None
    return {
        "known": bool(networks),
        "generated_at": generated if isinstance(generated, (int, float)) else None,
        "networks": networks,
    }


def locate(address: str, listing: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """(network, routed) for an address among the device networks, or (None, False).

    A directly attached subnet wins over a routed one; the routing table does the same.
    """
    try:
        ip = ipaddress.ip_address(str(address).strip())
    except ValueError:
        return None, False
    devices = [n for n in listing.get("networks", []) if "devices" in n["roles"]]
    for routed in (False, True):
        for n in devices:
            subnets = n["routed_subnets"] if routed else [n["subnet"]]
            if any(s and (net := _net(s)) and ip.version == net.version and ip in net for s in subnets):
                return n, routed
    return None, False


def classify(address: str, outcome: str, listing: dict[str, Any]) -> dict[str, Any]:
    """The reason class of a connect, with the network's name.

    `outcome` is the executor's own connect result: ok, refused, or any other failure (timeout,
    no route). Returns `reason` ("" when nothing is known and the connect failed: today's
    behaviour), `network` (name or "") and `routed`.
    """
    network, routed = locate(address, listing) if listing.get("known") else (None, False)
    name = network["name"] if network else ""
    if outcome == "ok":
        reason = OK
    elif outcome == "refused":
        reason = REFUSED
    elif not listing.get("known"):
        reason = ""
    elif network is None:
        reason = NO_ROUTE_KNOWN
    elif network["state"] == "down":
        reason = NETWORK_DOWN
    else:
        reason = NO_ANSWER_VIA_GATEWAY if routed else NO_ANSWER_ON_LINK
    return {"reason": reason, "network": name, "routed": routed}


def label(address: str, listing: dict[str, Any]) -> str:
    """"VLAN 200, routed" beside a device's address; "" when the list does not know it."""
    network, routed = locate(address, listing)
    return f"{network['name']}, routed" if network and routed else network["name"] if network else ""


def own_address(network: dict[str, Any] | None) -> str:
    """The appliance's own address in that network, without its prefix: what a device must allow."""
    return str((network or {}).get("address") or "").split("/", 1)[0]


def sentence(address: str, port: int, row: dict[str, Any], listing: dict[str, Any]) -> str:
    """The plain sentence for a failed connect, or "" when the reason class has none (use today's)."""
    reason = row.get("reason") or ""
    if reason != REFUSED and not listing.get("known"):
        return ""  # a list we cannot read gives no sentence; today's text stays
    network, _ = locate(address, listing)
    name, mine = row.get("network") or "", own_address(network)
    gateway = (network or {}).get("gateway") or "its gateway"
    if reason == NO_ROUTE_KNOWN:
        known = "; ".join(
            f"{n['name']} ({', '.join([n['subnet'], *n['routed_subnets']])})"
            for n in listing["networks"] if "devices" in n["roles"]
        ) or "none"
        return (
            f"{address} is not in any device network this appliance knows. It knows: {known}. "
            "An administrator can add a network or a routed subnet on the appliance console (menu Network)."
        )
    if reason == NO_ANSWER_ON_LINK:
        return (
            f"Nothing answers at {address} on {name}. Check that the device is up and that {name} "
            "is allowed on the appliance's switch port."
        )
    if reason == NO_ANSWER_VIA_GATEWAY:
        return (
            f"No answer from {address} through gateway {gateway}. Check the route on the gateway "
            f"and that the device's management ACL allows {mine or 'the appliance'}."
        )
    if reason == REFUSED:
        return f"{address} answers but refuses port {port}. Is SSH enabled, or is this device managed over HTTPS?"
    if reason == NETWORK_DOWN:
        return (
            f"The appliance's {name} interface is down. This is a problem on the appliance or its "
            "switch port, not on the device."
        )
    return ""


def management_hint(listing: dict[str, Any]) -> str:
    """"On your devices, allow management from 10.0.100.5 (VLAN 100), ..." for the Networks view."""
    seen = [
        f"{own_address(n)} ({n['name']})"
        for n in listing.get("networks", []) if "devices" in n["roles"] and own_address(n)
    ]
    return f"On your devices, allow management from {', '.join(seen)}." if seen else ""
