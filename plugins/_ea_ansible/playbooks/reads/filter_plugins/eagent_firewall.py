"""Turn the answers of the FortiOS REST API and the PAN-OS XML API into small, fixed shapes.

Used by `reads/fw/read.yml` (through `eagent_fw`). Standard library only, so it also runs
under pytest without Ansible (`plugins/_ea_ansible/tests/test_net_firewall_shapes.py`).

Written from the vendors' documented output, not run against a device: the field names
come from the pages cited in `tests/fixtures/firewall/README.md`. A field a device does not
send is left out, never guessed. Nothing here keeps a header, a token or a configuration
secret: each shape names the fields it keeps, and a reply that carries anything else loses it.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

try:  # Ansible loads this file; pytest does not have it.
    from ansible.errors import AnsibleFilterError as _Error
except ImportError:  # pragma: no cover
    class _Error(Exception):  # type: ignore[no-redef]
        pass

MAX_BYTES = 8 * 1024 * 1024
CAP = 200  # rows kept from a list; the total is always reported
_DTD = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)


class FirewallAnswerError(_Error):
    """A plain reason for the engineer; never contains a header or a token."""


def _clip(text: Any, limit: int = 160) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


# --- transport -----------------------------------------------------------------------------------

def _xml(text: str) -> dict[str, Any]:
    """`<response status=.. code=..><result>..` as {status, code, result}. Refuses DTDs outright:
    ElementTree expands entities, and a device (or whoever sits between) must not be able to
    make the executor allocate gigabytes."""
    if len(text) > MAX_BYTES or _DTD.search(text):
        raise FirewallAnswerError("The device answer was refused: it is too large or declares a DTD.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise FirewallAnswerError(f"The device answer is not XML ({_clip(error, 80)}).") from error
    return {"status": root.get("status", ""), "code": root.get("code", ""), "result": _node(root.find("result"))}


def _node(element: ET.Element | None) -> Any:
    if element is None:
        return {}
    out: dict[str, Any] = {f"@{k}": v for k, v in element.attrib.items()}
    for child in element:
        value = _node(child)
        if child.tag in out:
            out[child.tag] = out[child.tag] if isinstance(out[child.tag], list) else [out[child.tag]]
            out[child.tag].append(value)
        else:
            out[child.tag] = value
    if len(element):
        return out
    text = (element.text or "").strip()
    return text if text or not out else out


def _body(result: Any, platform: str) -> Any:
    """The decoded answer of one `uri` result, or a FirewallAnswerError saying why not."""
    status = result.get("status") if isinstance(result, dict) else None
    if status is None or status == -1:
        raise FirewallAnswerError(
            f"The device could not be reached or its certificate was not trusted: {_clip((result or {}).get('msg'))}"
        )
    content = result.get("content") or ""
    if platform == "fortios":
        try:
            doc = json.loads(content)
        except ValueError:
            doc = {}
        if status in (401, 403) or (isinstance(doc, dict) and doc.get("http_status") in (401, 403)):
            raise FirewallAnswerError(
                f"Access denied: the device rejected the stored API token (HTTP {status}). Check the token, its "
                "admin profile and the trusted hosts, which must include the executor's address."
            )
        if status != 200 or not isinstance(doc, dict):
            raise FirewallAnswerError(f"The FortiGate answered HTTP {status}: {_clip(doc.get('error') if isinstance(doc, dict) else '')}")
        return doc
    doc = _xml(content) if content.lstrip().startswith("<") else {"status": "error", "code": str(status)}
    if status in (401, 403) or doc["code"] in ("403", "401"):
        raise FirewallAnswerError(
            "Access denied: the device rejected the stored API key. Check the key and that the administrator's "
            "role allows the XML API."
        )
    if status != 200 or doc["status"] != "success":
        raise FirewallAnswerError(
            f"The firewall answered HTTP {status}, code {doc['code'] or '?'}: {_clip(_text(doc.get('result')))}"
        )
    return doc["result"]


def _text(node: Any) -> str:
    if isinstance(node, dict):
        return " ".join(_text(v) for k, v in node.items() if not k.startswith("@"))
    if isinstance(node, list):
        return " ".join(_text(v) for v in node)
    return str(node or "")


# --- small helpers -------------------------------------------------------------------------------

def _d(node: Any, *path: str) -> Any:
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _list(node: Any) -> list[Any]:
    return [] if node in (None, "", {}) else node if isinstance(node, list) else [node]


def _one(node: Any) -> dict[str, Any]:
    node = node[0] if isinstance(node, list) and node else node
    return node if isinstance(node, dict) else {}


def _keep(**fields: Any) -> dict[str, Any]:
    return {k: v for k, v in fields.items() if v not in (None, "", [], {})}


def _capped(rows: list[Any], **extra: Any) -> dict[str, Any]:
    return {**extra, "total": len(rows), "shown": min(len(rows), CAP), "rows": rows[:CAP]}


def _names(items: Any) -> list[str]:
    return [str(i.get("name") if isinstance(i, dict) else i) for i in _list(items)]


def _count(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[str(row.get(key) or "unknown")] = out.get(str(row.get(key) or "unknown"), 0) + 1
    return out


def _current(value: Any) -> Any:
    value = value[0] if isinstance(value, list) and value else value
    return value.get("current") if isinstance(value, dict) else value


# --- FortiOS: bodies are the decoded JSON documents -----------------------------------------------

def _forti_facts(bodies: list[Any]) -> dict[str, Any]:
    """Same shape `facts/normalisers._fortios` reads. Status (first) is required; HA is best effort."""
    status, ha, peers = (bodies + [None, None])[:3]
    res = _one(status.get("results"))
    out = {"system_status": _keep(
        serial=status.get("serial"), version=status.get("version"), build=status.get("build"),
        results=_keep(hostname=res.get("hostname"), model_name=res.get("model_name"),
                      model_number=res.get("model_number"), model=res.get("model")),
    )}
    if isinstance(ha, dict):  # cmdb system/ha?format=mode|group-name: no HA password is ever asked for
        r = _one(ha.get("results"))
        out["system_ha"] = {"results": _keep(mode=r.get("mode"), **{"group-name": r.get("group-name")})}
    if isinstance(peers, dict):
        out["system_ha-peer"] = {"results": [
            _keep(serial_no=p.get("serial_no"), hostname=p.get("hostname"), primary=p.get("primary", p.get("master")))
            for p in _list(peers.get("results")) if isinstance(p, dict)
        ]}
    return out


def _forti_interfaces(bodies: list[Any]) -> dict[str, Any]:
    results = bodies[0].get("results") or {}
    rows = list(results.values()) if isinstance(results, dict) else _list(results)
    return _capped([
        _keep(name=r.get("name"), alias=r.get("alias"), link="up" if r.get("link") else "down",
              ip=f"{r['ip']}/{r['mask']}" if r.get("ip") and r.get("mask") not in (None, "") else r.get("ip"),
              speed=r.get("speed"), duplex=r.get("duplex"), rx_errors=r.get("rx_errors"), tx_errors=r.get("tx_errors"),
              rx_bytes=r.get("rx_bytes"), tx_bytes=r.get("tx_bytes"))
        for r in rows if isinstance(r, dict)
    ])


def _forti_routes(bodies: list[Any]) -> dict[str, Any]:
    return _capped([
        _keep(destination=r.get("ip_mask"), gateway=r.get("gateway"), interface=r.get("interface"), type=r.get("type"),
              distance=r.get("distance"), metric=r.get("metric"), priority=r.get("priority"), vrf=r.get("vrf"))
        for r in _list(bodies[0].get("results")) if isinstance(r, dict)
    ])


def _forti_policy(bodies: list[Any]) -> dict[str, Any]:
    rows = [
        _keep(id=r.get("policyid"), name=r.get("name"), action=r.get("action"), status=r.get("status"),
              srcintf=_names(r.get("srcintf")), dstintf=_names(r.get("dstintf")))
        for r in _list(bodies[0].get("results")) if isinstance(r, dict)
    ]
    return _capped(rows, by_action=_count(rows, "action"), by_status=_count(rows, "status"))


def _forti_vpn(bodies: list[Any]) -> dict[str, Any]:
    rows = []
    for t in _list(bodies[0].get("results")):
        if not isinstance(t, dict):
            continue
        phase2 = [p for p in _list(t.get("proxyid")) if isinstance(p, dict)]
        up = sum(1 for p in phase2 if p.get("status") == "up")
        rows.append(_keep(name=t.get("name"), remote_gateway=t.get("rgwy"), phase2_up=up, phase2_total=len(phase2),
                          state="up" if phase2 and up == len(phase2) else "partial" if up else "down",
                          incoming_bytes=t.get("incoming_bytes"), outgoing_bytes=t.get("outgoing_bytes")))
    return _capped(rows, by_state=_count(rows, "state"))


def _forti_usage(bodies: list[Any], keys: tuple[str, ...]) -> dict[str, Any]:
    res = bodies[0].get("results") or {}
    return {k: _current(res[k]) for k in keys if k in res and _current(res[k]) is not None}


# --- PAN-OS: bodies are the `result` element as a dict --------------------------------------------

def _pan_facts(bodies: list[Any]) -> dict[str, Any]:
    """The `ansible_net_*` names `facts/normalisers._panos` reads. System info (first) is required."""
    system = _d(bodies[0], "system") or {}
    out = _keep(ansible_net_hostname=system.get("hostname"), ansible_net_model=system.get("model"),
                ansible_net_serial=system.get("serial"), ansible_net_version=system.get("sw-version"),
                uptime=system.get("uptime"))
    ha = bodies[1] if len(bodies) > 1 and isinstance(bodies[1], dict) else {}
    if ha.get("enabled") in ("yes", "no"):
        out["ansible_net_ha_enabled"] = ha["enabled"] == "yes"
        out.update(_keep(ansible_net_ha_localmode=_d(ha, "group", "mode"),
                         ansible_net_ha_localstate=_d(ha, "group", "local-info", "state"),
                         ha_peer_state=_d(ha, "group", "peer-info", "state")))
    return out


def _pan_interfaces(bodies: list[Any]) -> dict[str, Any]:
    logical = {e.get("name"): e for e in _list(_d(bodies[0], "ifnet", "entry")) if isinstance(e, dict)}
    hardware = {e.get("name"): e for e in _list(_d(bodies[0], "hw", "entry")) if isinstance(e, dict)}
    return _capped([
        _keep(name=n, state=hardware.get(n, {}).get("state"), speed_duplex=hardware.get(n, {}).get("st"),
              mac=hardware.get(n, {}).get("mac"), zone=logical.get(n, {}).get("zone"), ip=logical.get(n, {}).get("ip"),
              vsys=logical.get(n, {}).get("vsys"))
        for n in sorted({*logical, *hardware}, key=str) if n
    ])


def _pan_routes(bodies: list[Any]) -> dict[str, Any]:
    return _capped([
        _keep(virtual_router=e.get("virtual-router"), destination=e.get("destination"), next_hop=e.get("nexthop"),
              interface=e.get("interface"), metric=e.get("metric"), flags=e.get("flags"), age=e.get("age"))
        for e in _list(_d(bodies[0], "entry")) if isinstance(e, dict)
    ])


def _pan_policy(bodies: list[Any]) -> dict[str, Any]:
    entries = [e for r in _list(_d(bodies[0], "rules")) for e in _list(_d(r, "entry")) if isinstance(e, dict)]
    rows = [_keep(name=e.get("@name"), action=e.get("action"), disabled=e.get("disabled") or "no",
                  from_zones=_names(_d(e, "from", "member")), to_zones=_names(_d(e, "to", "member")))
            for e in entries]
    return _capped(rows, by_action=_count(rows, "action"), disabled=sum(1 for r in rows if r["disabled"] == "yes"))


def _pan_vpn(bodies: list[Any]) -> dict[str, Any]:
    """`show vpn flow`: every entry that has a name and a state is a tunnel, wherever it sits."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if "name" in node and "state" in node:
                found.append(_keep(name=node["name"], state=node["state"], inner_interface=node.get("inner-if"),
                                   outer_interface=node.get("outer-if"), monitor=node.get("mon")))
            for value in node.values():
                walk(value)

    walk(bodies[0])
    return _capped(found, by_state=_count(found, "state"))


_TOP = re.compile(r"(?m)^(?:top -|%Cpu|Cpu\(s\)|KiB Mem|MiB Mem|Mem:).*$")
_IDLE = re.compile(r"([\d.]+)[ %]*id\b")


def _pan_resources(bodies: list[Any]) -> dict[str, Any]:
    """`show system resources` is `top` output inside <result>. Keep its summary lines, plus the CPU use they imply."""
    text = bodies[0] if isinstance(bodies[0], str) else _text(bodies[0])
    lines = [_clip(m.group(0), 200) for m in _TOP.finditer(text)]
    idle = next((_IDLE.search(line) for line in lines if "Cpu" in line and _IDLE.search(line)), None)
    return _keep(summary=lines, cpu_used_percent=round(100 - float(idle.group(1)), 1) if idle else None)


_SESSION_FIELDS = ("num-max", "num-active", "num-tcp", "num-udp", "num-icmp", "num-installed", "cps", "pps", "kbps")


def _pan_sessions(bodies: list[Any]) -> dict[str, Any]:
    return {k: bodies[0][k] for k in _SESSION_FIELDS if isinstance(bodies[0], dict) and k in bodies[0]}


# --- ports for the inventory (reads/inventory_ports): named fields only, parsed by ports_parse.py ----------------

PORT_CAP = 1000  # a firewall can carry hundreds of VLAN interfaces


def _rows(node: Any) -> list[dict[str, Any]]:
    """`results` as a list of objects: FortiOS keys the interface monitor by name, the cmdb answers a list."""
    return [r for r in (list(node.values()) if isinstance(node, dict) else _list(node)) if isinstance(r, dict)]


def _forti_ports(bodies: list[Any]) -> dict[str, Any]:
    """Interface monitor (required), then cmdb interfaces (admin state, VLAN id, parent), cmdb zones and system status."""
    monitor, config, zones, status = (bodies + [None, None, None])[:4]
    names = ("name", "alias", "description", "mac", "ip", "mask", "link", "speed", "duplex", "type")
    out: dict[str, Any] = {"interfaces": [_keep(**{k: r.get(k) for k in names}) for r in _rows(monitor.get("results"))][:PORT_CAP]}
    if isinstance(config, dict):
        keys = ("name", "alias", "description", "status", "type", "vlanid", "interface", "role")
        out["config"] = [_keep(**{k: r.get(k) for k in keys}) for r in _rows(config.get("results"))][:PORT_CAP]
    if isinstance(zones, dict):
        out["zones"] = [_keep(name=r.get("name"), interfaces=[i.get("interface-name") for i in _list(r.get("interface")) if isinstance(i, dict)])
                        for r in _rows(zones.get("results"))]
    if isinstance(status, dict):
        res = _one(status.get("results"))
        out["system"] = _keep(serial=status.get("serial"), model_name=res.get("model_name"), model_number=res.get("model_number"), model=res.get("model"))
    return out


def _pan_ports(bodies: list[Any]) -> dict[str, Any]:
    """`show interface all` (required): logical `ifnet` entries and `hw` entries, then `show system info`."""
    entries = lambda key: [e for e in _list(_d(bodies[0], key, "entry")) if isinstance(e, dict)]  # noqa: E731
    system = _d(bodies[1], "system") if len(bodies) > 1 and isinstance(bodies[1], dict) else None
    out = {
        "ifnet": [_keep(name=e.get("name"), zone=e.get("zone"), ip=e.get("ip"), tag=e.get("tag"), vsys=e.get("vsys")) for e in entries("ifnet")][:PORT_CAP],
        "hw": [_keep(name=e.get("name"), state=e.get("state"), st=e.get("st"), speed=e.get("speed"), duplex=e.get("duplex"), mac=e.get("mac"))
               for e in entries("hw")][:PORT_CAP],
    }
    if isinstance(system, dict):
        out["system"] = _keep(model=system.get("model"), serial=system.get("serial"))
    return out


#: (platform, read) -> (shaper, how many leading answers must succeed; the rest are best effort)
SHAPES = {
    ("fortios", "facts"): (_forti_facts, 1), ("panos", "facts"): (_pan_facts, 1),
    ("fortios", "interfaces"): (_forti_interfaces, 1), ("panos", "interfaces"): (_pan_interfaces, 1),
    ("fortios", "routing_table"): (_forti_routes, 1), ("panos", "routing_table"): (_pan_routes, 1),
    ("fortios", "firewall_policy_summary"): (_forti_policy, 1), ("panos", "firewall_policy_summary"): (_pan_policy, 1),
    ("fortios", "vpn_tunnels"): (_forti_vpn, 1), ("panos", "vpn_tunnels"): (_pan_vpn, 1),
    ("fortios", "system_resources"): (lambda b: _forti_usage(b, ("cpu", "mem", "disk")), 1),
    ("panos", "system_resources"): (_pan_resources, 1),
    ("fortios", "ports"): (_forti_ports, 1), ("panos", "ports"): (_pan_ports, 1),
    ("fortios", "session_summary"): (lambda b: _forti_usage(b, ("session", "session6", "setup_rate", "setup_rate6")), 1),
    ("panos", "session_summary"): (_pan_sessions, 1),
}


def eagent_fw(results: Any, platform: str, read: str) -> dict[str, Any]:
    """`results`: the `results` list of the `uri` loop in reads/fw/read.yml, in request order."""
    if (platform, read) not in SHAPES:
        raise FirewallAnswerError(f"There is no {read} read for {platform}.")
    shape, required = SHAPES[(platform, read)]
    bodies = []
    for index, result in enumerate(results or []):
        try:
            bodies.append(_body(result, platform))
        except FirewallAnswerError:
            if index < required:
                raise
            bodies.append(None)
    return shape(bodies)


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {"eagent_fw": eagent_fw}
