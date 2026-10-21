"""A device's observed ports and parts (ADR 0045, 0049): saved without approval, dated, replaced on refresh.

Like the rest of the inventory this is instance-wide (`db.NO_SCOPE`). The only column a person writes is
`is_uplink`, an annotation on an observed row; it survives a refresh while the port is still reported.
"""

from __future__ import annotations

import ipaddress
from collections import Counter
from typing import Any

from plugins._ea_inventory.helpers import store
from plugins._ea_inventory.helpers.facts.ports_parse import VIRTUAL, Parsed

try:  # pragma: no cover - the audit plugin is bundled but can be switched off
    from plugins._audit.helpers import audit
except Exception:  # noqa: BLE001
    audit = None  # type: ignore[assignment]

IFACE_COLS = (
    "name", "alias", "description", "admin_up", "oper_up", "speed", "media", "present", "poe_watts", "mode",
    "vlans", "ip_prefixes", "mac", "neighbor_name", "neighbor_port",
)


def emit(event: str, **fields: Any) -> None:
    if audit is not None:
        audit.emit(event, **fields)


def replace_snapshot(device_id: str, parsed: Parsed, at: Any) -> tuple[int, int]:
    """Make the stored rows exactly `parsed`; returns (interfaces, components). One transaction."""
    marks = {(i["name"]) for i in parsed.interfaces}
    with store.transaction() as conn:
        conn.execute("DELETE FROM inventory_interface WHERE device_id = %s AND NOT (name = ANY(%s))", (device_id, list(marks)))
        conn.execute("DELETE FROM inventory_component WHERE device_id = %s", (device_id,))
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO inventory_interface (device_id, observed_at, {', '.join(IFACE_COLS)}) "
                f"VALUES (%s, %s, {', '.join(['%s'] * len(IFACE_COLS))}) "
                "ON CONFLICT (device_id, name) DO UPDATE SET observed_at = EXCLUDED.observed_at, "
                + ", ".join(f"{c} = EXCLUDED.{c}" for c in IFACE_COLS[1:]),  # is_uplink is not in the list: it stays
                [(device_id, at, *(i[c] for c in IFACE_COLS)) for i in parsed.interfaces],
            )
            cur.executemany(
                "INSERT INTO inventory_component (device_id, observed_at, kind, name, part_number, serial, description, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [(device_id, at, c["kind"], c["name"], c["part_number"], c["serial"], c["description"], c["expires_at"])
                 for c in parsed.components],
            )
    return len(parsed.interfaces), len(parsed.components)


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def device_ports(device_id: str) -> dict[str, Any]:
    """`{observed_at, interfaces, components}` in the shape `inventory_ports` returns, minus the summary."""
    with store.transaction() as conn:
        found = store.rows(conn, "SELECT * FROM inventory_interface WHERE device_id = %s ORDER BY length(name), name", (device_id,))
        parts = store.rows(conn, "SELECT * FROM inventory_component WHERE device_id = %s ORDER BY kind, name", (device_id,))
        known = {r["name"].lower(): str(r["id"]) for r in store.rows(conn, "SELECT id, name FROM inventory_device WHERE id <> %s", (device_id,))}
    interfaces = []
    for r in found:
        n = r["neighbor_name"]
        peer = known.get(n.lower()) or known.get(n.split(".")[0].lower()) if n else None
        interfaces.append({
            **{c: r[c] for c in IFACE_COLS if c not in ("neighbor_name", "neighbor_port", "poe_watts", "vlans")},
            "poe_watts": _num(r["poe_watts"]), "vlans": list(r["vlans"]),
            "neighbor": {"name": n, "port": r["neighbor_port"], "device_id": peer} if n else None,
            "is_uplink": r["is_uplink"] or bool(peer),
            "uplink_source": "marked" if r["is_uplink"] else "neighbour" if peer else "",
        })
    components = [
        {"kind": r["kind"], "name": r["name"], "part_number": r["part_number"], "serial": r["serial"],
         "description": r["description"], "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None}
        for r in parts
    ]
    seen = [r["observed_at"] for r in found + parts]
    return {"observed_at": max(seen).isoformat() if seen else None, "interfaces": interfaces, "components": components}


def summary(interfaces: list[dict[str, Any]], components: list[dict[str, Any]], mgmt_ip: str = "") -> dict[str, Any]:
    ports = [i for i in interfaces if not VIRTUAL.match(i["name"])]  # ponytail: name heuristic; a `kind` column if it misleads
    poe = [i["poe_watts"] for i in ports if i["poe_watts"] is not None]
    optics = Counter(c["part_number"] or c["description"] or "unknown" for c in components if c["kind"] == "transceiver")
    subnet, best = "", (False, -1)
    for i in interfaces:
        for prefix in i["ip_prefixes"]:
            try:  # an address on the device wins; else the narrowest subnet holding it (a device reached through NAT)
                nic, ip = ipaddress.ip_interface(prefix), ipaddress.ip_address(mgmt_ip)
            except ValueError:
                continue
            if ip in nic.network and (ip == nic.ip, nic.network.prefixlen) > best:
                subnet, best = str(nic.network), (ip == nic.ip, nic.network.prefixlen)
    return {
        "total": len(ports), "up": sum(i["oper_up"] is True for i in ports), "down": sum(i["oper_up"] is False for i in ports),
        "unknown": sum(i["oper_up"] is None for i in ports),
        "poe": {"ports": len(poe), "watts": round(sum(poe), 1)} if poe else None,
        "optics": [{"media": m, "count": n} for m, n in sorted(optics.items())],
        "vlans": sorted({v for i in interfaces for v in i["vlans"]}),
        "mgmt_subnet": subnet,
        "uplinks": [
            {"port": i["name"], "neighbor": i["neighbor"]["name"] if i["neighbor"] else "",
             "neighbor_port": i["neighbor"]["port"] if i["neighbor"] else "",
             "device_id": i["neighbor"]["device_id"] if i["neighbor"] else None}
            for i in interfaces if i["is_uplink"]
        ],
    }


def set_uplink(device_id: str, name: str, is_uplink: bool) -> bool:
    """False when the device has no such observed port."""
    with store.transaction() as conn:
        return conn.execute(
            "UPDATE inventory_interface SET is_uplink = %s WHERE device_id = %s AND name = %s", (is_uplink, device_id, name),
        ).rowcount > 0


def vlan_ports(vlan: int) -> list[dict[str, Any]]:
    with store.transaction() as conn:
        found = store.rows(
            conn,
            "SELECT i.device_id, d.name AS device, i.name AS port, i.mode FROM inventory_interface i "
            "JOIN inventory_device d ON d.id = i.device_id WHERE %s = ANY(i.vlans) ORDER BY d.name, length(i.name), i.name",
            (vlan,),
        )
    return [{**r, "device_id": str(r["device_id"])} for r in found]

