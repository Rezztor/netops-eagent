"""Device `show` output and module facts -> port and part rows. Pure, never raises.

Input is the `EAGENT_PORTS` payload: `{"platform", "commands": {command: [line, ...]}, "facts": {...}}`.
A command that failed, printed a usage error or only shell noise (`sudo: not found`) simply yields no
rows: a table is recognised by its header, so there is nothing to mistake for data. `sonic_community`
and `ios` are checked against lab devices (tests/fixtures/ports/*_lab.json): a Cisco 3725 with an NM-16ESW
module on IOS 12.4(15)T14. The `*_documented` fixtures are shapes written from documentation.

`nxos`, `eos` and `junos` are written from documented output, not run against a device. They read the
vendor's structured output (`| json`, `| display json`), whose value is already parsed (NX-OS and EOS) or
arrives as text (Junos); each fixture names the page it was copied from. None of the three reports PoE here,
and Junos gives no speed, mode or VLANs: a field no cited example shows is left empty, never guessed.

`fortios` and `panos` are written from documented output too, but read the management API through the executor
(ADR 0058, `eagent_fw`'s `ports` shape): `commands` holds one key, `ports`, with named fields only. They report
no PoE and no neighbours, and put a zone in the description because a port row has no zone column.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any

DASHES = re.compile(r"^-+(?:\s+-+)+\s*$")
NA = {"", "n/a", "na", "none", "unknown", "-", "--", "builtin", "not present"}
# Rows that are not switch ports: counted neither up nor down in the summary.
VIRTUAL = re.compile(
    r"^(?:lo\d*$|loopback|vlan|port-?channel|po\d|tunnel|docker|eth\d|mgmt|management|vxlan|nve"  # IOS, NX-OS, EOS, SONiC
    r"|irb|ae\d|vme|fxp|em\d|me\d|gr-|ip-|lt-|mt-|vt-|lc-|lsq-|vcp-|pfe|pfh|pimd|pime|tap|dsc|gre|ipip|lsi|mtun|jsrv|bme|cbp|fti)",  # Junos
    re.I,
)

_IOS_SHORT = {
    "gi": "GigabitEthernet", "te": "TenGigabitEthernet", "fa": "FastEthernet", "twe": "TwentyFiveGigE",
    "fo": "FortyGigabitEthernet", "hu": "HundredGigE", "po": "Port-channel", "eth": "Ethernet",
}


@dataclass
class Parsed:
    interfaces: list[dict[str, Any]] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)


def _clean(lines: Any) -> list[str]:
    """Lines as strings, without shell noise."""
    return [str(x).rstrip() for x in lines or [] if "sudo: not found" not in str(x)] if isinstance(lines, list) else []


def _val(text: str) -> str:
    return "" if text.strip().lower() in NA else text.strip()


def _up(text: str) -> bool | None:
    return {"up": True, "down": False}.get(text.strip().lower())


def _dash_table(lines: list[str]) -> list[dict[str, str]]:
    """tabulate's plain format: a header, a line of dashes giving the column spans, then rows until a blank."""
    for i, line in enumerate(lines):
        if i and DASHES.match(line):
            spans = [m.span() for m in re.finditer(r"-+", line)]
            names = [lines[i - 1][s:e].strip() for s, e in spans]
            rows = []
            for row in lines[i + 1:]:
                if not row.strip() or row.lstrip().startswith("Total"):
                    break
                rows.append({n: row[s:e].strip() for n, (s, e) in zip(names, spans)})
            return rows
    return []


def _ios_status(lines: list[str]) -> list[dict[str, str]]:
    """IOS `show interfaces status`. Port, Name and Status start under their header words; after Status the
    fields are plain words (IOS right-shifts Duplex and Speed by a column, so header positions would cut them)."""
    for i, line in enumerate(lines):
        h = {w: line.find(w) for w in ("Port", "Name", "Status")}
        if line.startswith("Port") and min(h.values()) >= 0:
            out = []
            for row in lines[i + 1:]:
                m = re.match(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*(.*)$", row[h["Status"]:].lstrip())
                if m and row.strip():
                    out.append({"Port": row[:h["Name"]].strip(), "Name": row[h["Name"]:h["Status"]].strip(), "Status": m[1],
                                "Vlan": m[2], "Speed": m[4], "Type": m[5]})
            return out
    return []


def _grid_vlans(lines: list[str]) -> dict[str, tuple[set[int], bool]]:
    """SONiC `show vlan brief` (a grid): port -> (vlans, tagged somewhere). A continuation row has an empty VLAN ID."""
    found: dict[str, tuple[set[int], bool]] = {}
    vlan = 0
    header: list[str] = []
    for line in lines:
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not header:
            header = [c.lower() for c in cells]
            continue
        row = dict(zip(header, cells))
        if row.get("vlan id", "").isdigit():
            vlan = int(row["vlan id"])
        for port in re.split(r"[\s,]+", row.get("ports", "")):
            if port and vlan:
                seen, tagged = found.get(port, (set(), False))
                found[port] = (seen | {vlan}, tagged or row.get("port tagging", "").lower() == "tagged")
    return found


def _ios_name(name: str) -> str:
    m = re.match(r"^([A-Za-z]+)(\d.*)$", name)
    full = _IOS_SHORT.get(m.group(1).lower()) if m else None
    return full + m.group(2) if full else name


def _ios_switchport(lines: list[str]) -> dict[str, dict[str, str]]:
    """IOS `show interfaces switchport`: port -> {field: value}. A line without a colon continues the last field."""
    found: dict[str, dict[str, str]] = {}
    cur: dict[str, str] = {}
    key = ""
    for line in lines:
        k, colon, v = line.partition(":")
        if colon and k.strip() == "Name":
            cur, key = found.setdefault(v.strip(), {}), ""
        elif colon:
            key = k.strip()
            cur[key] = v.strip()
        elif key and line.strip():
            cur[key] += "," + line.strip()
    return found


def _vlan_list(text: str) -> list[int]:
    """`1,10,20-22` -> [1, 10, 20, 21, 22]; `ALL`, `NONE` and anything else -> [] (every VLAN is not a list)."""
    out: set[int] = set()
    for part in text.split(","):
        m = re.fullmatch(r"\s*(\d+)(?:-(\d+))?\s*", part)
        if m and int(m[2] or m[1]) <= 4094:
            out.update(range(int(m[1]), int(m[2] or m[1]) + 1))
    return sorted(out)


def _prefix(address: str, mask: str) -> str:
    try:
        return str(ipaddress.ip_interface(f"{address}/{mask}")) if mask else str(ipaddress.ip_interface(address))
    except ValueError:
        return ""


def _speed(kbit: Any) -> str:
    try:
        k = int(kbit)
    except (TypeError, ValueError):
        return ""
    return f"{k // 1_000_000}G" if k >= 1_000_000 and k % 1_000_000 == 0 else f"{k // 1000}M" if k >= 1000 else ""


class _Rows(dict):
    def __missing__(self, name: str) -> dict[str, Any]:
        self[name] = {
            "name": name, "alias": "", "description": "", "admin_up": None, "oper_up": None, "speed": "",
            "media": "", "present": None, "poe_watts": None, "mode": "", "vlans": [], "ip_prefixes": [],
            "mac": "", "neighbor_name": "", "neighbor_port": "",
        }
        return self[name]


def _vlan_column(row: dict[str, Any], vlan: str) -> None:
    """Status tables say `routed`, `trunk` or an access VLAN number."""
    if vlan in ("routed", "trunk"):
        row["mode"] = vlan
    elif vlan.isdigit():
        row["mode"], row["vlans"] = "access", [int(vlan)]


def _sonic(commands: dict[str, list[str]]) -> Parsed:
    rows = _Rows()
    get = lambda cmd: _clean(commands.get(cmd))  # noqa: E731
    for r in _dash_table(get("show interfaces status")):
        row = rows[r["Interface"]]
        row.update(
            alias=_val(r.get("Alias", "")), speed=_val(r.get("Speed", "")), media=_val(r.get("Type", "")),
            oper_up=_up(r.get("Oper", "")), admin_up=_up(r.get("Admin", "")),
        )
        _vlan_column(row, r.get("Vlan", "").lower())
    for r in _dash_table(get("show interfaces description")):
        row = rows[r["Interface"]]
        row.update(alias=row["alias"] or _val(r.get("Alias", "")), description=_val(r.get("Description", "")))
        row["oper_up"] = _up(r.get("Oper", "")) if row["oper_up"] is None else row["oper_up"]
        row["admin_up"] = _up(r.get("Admin", "")) if row["admin_up"] is None else row["admin_up"]
    for port, (vlans, tagged) in _grid_vlans(get("show vlan brief")).items():
        if port in rows:
            rows[port].update(mode="trunk" if tagged else "access", vlans=sorted(vlans))
    for r in _dash_table(get("show lldp table")):
        if r.get("LocalPort") in rows:
            rows[r["LocalPort"]].update(neighbor_name=_val(r.get("RemoteDevice", "")), neighbor_port=_val(r.get("RemotePortID", "")))
    for r in _dash_table(get("show ip interfaces")):
        prefix = _prefix(r.get("IPv4 address/mask", ""), "")
        if prefix:
            rows[r["Interface"]]["ip_prefixes"].append(prefix)
    present = {}
    for r in _dash_table(get("show interfaces transceiver presence")):
        if r.get("Port") in rows:
            present[r["Port"]] = r.get("Presence", "").strip().lower() == "present"
            rows[r["Port"]]["present"] = present[r["Port"]]
    out = Parsed(list(rows.values()))
    out.components += [
        {"kind": "transceiver", "name": p, "part_number": "", "serial": "", "description": rows[p]["media"], "expires_at": None}
        for p, there in present.items() if there
    ]
    summary = dict(re.findall(r"^([\w ]+):\s*(.*)$", "\n".join(get("show platform summary")), re.M))
    part, serial = _val(summary.get("Model Number", "")), _val(summary.get("Serial Number", ""))
    sku = _val(summary.get("HwSKU", ""))
    if part or serial or sku:
        out.components.append({
            "kind": "chassis", "name": "chassis", "part_number": part, "serial": serial,
            "description": f"HwSKU {sku}" if sku else "", "expires_at": None,
        })
    return out


def _ios_kind(name: str, descr: str, pid: str) -> str:
    text = f"{name} {descr}".lower()
    if name.strip().lower() == "chassis":  # before the module test: "Nexus9000 C9508 (8 Slot) Chassis" says slot
        return "chassis"
    if "power supply" in text or pid.upper().startswith("PWR") or re.match(r"(?:pem|psu)\b", name, re.I):
        return "psu"
    if re.search(r"\bfan\b", text):
        return "fan"
    if re.search(r"(?:gigabit|ethernet|gige|twe|fo|hu|te|gi)\d", name, re.I) or re.match(r"(?:X2|SFP|QSFP|GLC|CFP)", pid, re.I):
        return "transceiver"
    if re.match(r"switch \d+$", name, re.I):
        return "stack_member"
    if re.search(r"module|slot|supervisor|linecard|uplink", text):
        return "module"
    return "chassis" if re.search(r"chassis|stack", text) else "module"


def _component(name: str, descr: str, pid: str, sn: str, kind: str = "") -> dict[str, Any]:
    return {
        "kind": kind or _ios_kind(name, descr, pid), "name": name, "part_number": _val(pid),
        "serial": _val(sn) if sn.strip("0") else "",  # all zeros is a placeholder (emulated modules)
        "description": descr, "expires_at": None,
    }


def _ios(commands: dict[str, list[str]], facts: dict[str, Any]) -> Parsed:
    rows = _Rows()
    get = lambda cmd: _clean(commands.get(cmd))  # noqa: E731
    ifaces = facts.get("net_interfaces") or facts.get("ansible_net_interfaces")  # Ansible drops the `ansible_` prefix
    for name, f in (ifaces.items() if isinstance(ifaces, dict) else ()):
        if not isinstance(f, dict):
            continue
        status = str(f.get("operstatus") or "").lower()  # the admin side; `lineprotocol` is the link ("up (connected)" on some images)
        line = str(f.get("lineprotocol") or "").lower()
        rows[name].update(
            description=_val(str(f.get("description") or "")), mac=_val(str(f.get("macaddress") or "")),
            speed=_speed(f.get("bandwidth")), media=_val(str(f.get("type") or "")),
            oper_up=line.startswith("up") if line else status == "up" if status else None,
            admin_up=status != "administratively down" if status else None,
        )
        rows[name]["ip_prefixes"] = [p for a in f.get("ipv4") or [] if isinstance(a, dict) and (p := _prefix(str(a.get("address") or ""), str(a.get("subnet") or "")))]
    neighbours = facts.get("net_neighbors") or facts.get("ansible_net_neighbors")
    for name, found in (neighbours.items() if isinstance(neighbours, dict) else ()):
        if isinstance(found, list) and found and isinstance(found[0], dict) and name in rows:
            rows[name].update(neighbor_name=str(found[0].get("host") or ""), neighbor_port=str(found[0].get("port") or ""))
    for r in _ios_status(get("show interfaces status")):
        row = rows[_ios_name(r["Port"])]
        state = r.get("Status", "").lower()
        speed = re.sub(r"^a-", "", r.get("Speed", ""))
        speed = speed + "M" if speed.isdigit() else speed  # IOS prints megabits bare
        row.update(
            description=row["description"] or r.get("Name", ""),
            speed="" if speed.lower() == "auto" else _val(speed) or row["speed"], media=_val(r.get("Type", "")) or row["media"],
        )
        if state:
            row.update(oper_up=state == "connected", admin_up=state != "disabled")
            row["present"] = False if state == "sfpabsent" else row["present"]
        _vlan_column(row, r.get("Vlan", "").lower())
    vlan = 0
    # `show vlan brief` does not exist on every image (EtherSwitch modules on IOS 12.4 call it `show vlan-switch brief`)
    for line in get("show vlan brief") or get("show vlan-switch brief"):  # a continuation line (leading space) carries only more ports
        m = re.match(r"^(\d+)\s+\S+\s+\S+\s*(.*)$", line)
        if m:
            vlan, ports = int(m.group(1)), m.group(2)
        elif line[:1] == " ":
            ports = line
        else:
            continue
        for port in (p for p in re.split(r"[\s,]+", ports) if vlan and re.match(r"[A-Za-z]+\d", p)):
            row = rows[_ios_name(port)]
            row.update(mode="access", vlans=sorted({*row["vlans"], vlan}))
    for name, sw in _ios_switchport(get("show interfaces switchport")).items():
        row = rows.get(_ios_name(name))
        oper, admin = sw.get("Operational Mode", "").lower(), sw.get("Administrative Mode", "").lower()
        mode = next((m for m in (oper, admin) if "trunk" in m or "access" in m), "")
        if row is not None and mode:
            trunk = "trunk" in mode
            row["mode"] = "trunk" if trunk else "access"
            row["vlans"] = _vlan_list(sw.get("Trunking VLANs Enabled", "") if trunk else sw.get("Access Mode VLAN", "").split(" ")[0])
    for line in get("show power inline"):
        m = re.match(r"^([A-Za-z][\w/.-]*)\s+\S+\s+\S+\s+(\d+(?:\.\d+)?)\s", line)
        if m:
            rows[_ios_name(m.group(1))]["poe_watts"] = float(m.group(2))
    out = Parsed(list(rows.values()))
    inventory = "\n".join(get("show inventory"))
    for name, descr, pid, sn in re.findall(r'NAME:\s*"([^"]*)",\s*DESCR:\s*"([^"]*)"\s*PID:\s*([^,\s]*)[^\n]*?SN:\s*(\S*)', inventory):
        out.components.append(_component(name, descr, pid, sn))
    for name, tag, status in re.findall(r"^\s*(\S+)\s+\((.+?)\)\s+\d+\s+(.+?)\s*$", "\n".join(get("show license summary")), re.M):
        out.components.append({"kind": "licence", "name": name, "part_number": "", "serial": "", "description": f"{tag}; {status}", "expires_at": None})
    return out


def _doc(value: Any) -> dict[str, Any]:
    """A structured reply as a dict. NX-OS and EOS arrive parsed (their cliconf decodes `| json`), Junos as text.
    Text may carry a banner such as `{master:0}` ahead of the JSON, so decoding starts at each line that opens with a
    brace until one parses."""
    if isinstance(value, list):
        value = "\n".join(str(x) for x in value)
    if isinstance(value, str):
        for m in re.finditer(r"^\{", value, re.M):
            try:
                value = json.JSONDecoder().raw_decode(value[m.start():])[0]
                break
            except ValueError:
                continue
        else:
            return {}
    return value if isinstance(value, dict) else {}


def _walk(node: Any, key: str):
    """Every dict under `node` that has `key`: NX-OS nests its tables (per VRF too) and a one-row table is a dict, not a list."""
    if isinstance(node, dict):
        if key in node:
            yield node
        for child in node.values():
            yield from _walk(child, key)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child, key)


def _items(node: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(str(k), v) for k, v in node.items() if isinstance(v, dict)] if isinstance(node, dict) else []


def _nxos_name(name: str) -> str:
    """LLDP and `show inventory` print `Eth1/1` and `Po1`; `show interface` prints `Ethernet1/1` and `port-channel1`."""
    m = re.match(r"(?i)^(eth|po)(\d.*)$", name.strip())
    return {"eth": "Ethernet", "po": "port-channel"}[m[1].lower()] + m[2] if m else name.strip()


def _nxos(commands: dict[str, Any]) -> Parsed:
    rows = _Rows()
    doc = lambda cmd: _doc(commands.get(cmd))  # noqa: E731
    for r in _walk(doc("show interface status | json"), "interface"):
        row = rows[str(r["interface"])]
        state, speed = str(r.get("state", "")).lower(), re.sub(r"^a-", "", str(r.get("speed", "")))
        speed = speed + "M" if speed.isdigit() else speed
        row.update(description=_val(str(r.get("name", ""))), speed="" if speed.lower() == "auto" else _val(speed), media=_val(str(r.get("type", ""))))
        if state:
            row.update(oper_up=state in ("connected", "up"), admin_up=state != "disabled")
            row["present"] = False if state in ("sfpabsent", "xcvrabsent", "xcvrabsen") else row["present"]
        _vlan_column(row, str(r.get("vlan", "")).lower())
    for r in _walk(doc("show interface switchport | json"), "oper_mode"):
        mode, vlan, trunk = str(r.get("oper_mode", "")).lower(), str(r.get("access_vlan", "")), str(r.get("trunk_vlans", ""))
        if str(r.get("switchport", "")).lower() == "enabled" and mode in ("access", "trunk"):
            rows[str(r["interface"])].update(mode=mode, vlans=([int(vlan)] if vlan.isdigit() else []) if mode == "access" else [] if trunk == "1-4094" else _vlan_list(trunk))
    for r in _walk(doc("show ip interface vrf all | json"), "intf-name"):
        row = rows[_nxos_name(str(r["intf-name"]))]
        prefix = _prefix(str(r.get("prefix", "")), str(r["masklen"])) if r.get("masklen") else ""  # no length, no address
        if prefix:
            row["ip_prefixes"].append(prefix)
        if row["admin_up"] is None:  # an SVI or loopback is not in the status table
            row.update(admin_up=r.get("admin-state") == "TRUE", oper_up=r.get("proto-state") == "TRUE")
    for r in _walk(doc("show lldp neighbors detail | json"), "l_port_id"):
        row = rows.get(_nxos_name(str(r["l_port_id"])))
        if row is not None and not row["neighbor_name"]:
            row.update(neighbor_name=str(r.get("sys_name") or r.get("chassis_id") or ""), neighbor_port=str(r.get("port_id") or ""))
    out = Parsed(list(rows.values()))
    for r in _walk(doc("show inventory | json"), "productid"):  # the names and descriptions come back wrapped in quotes
        name, descr = (str(r.get(k, "")).strip().strip('"') for k in ("name", "desc"))
        out.components.append(_component(name, descr, str(r["productid"]), str(r.get("serialnum", ""))))
        if out.components[-1]["kind"] == "transceiver" and _nxos_name(name) in rows:
            rows[_nxos_name(name)]["present"] = True
    return out


_EOS_MODE = {"bridged": "access", "trunk": "trunk", "routed": "routed"}


def _eos(commands: dict[str, Any]) -> Parsed:
    rows = _Rows()
    doc = lambda cmd: _doc(commands.get(cmd))  # noqa: E731
    for name, f in _items(doc("show interfaces description | json").get("interfaceDescriptions")):
        status, line = str(f.get("interfaceStatus", "")), str(f.get("lineProtocolStatus", ""))
        rows[name].update(description=_val(str(f.get("description", ""))), admin_up=status != "adminDown" if status else None, oper_up=line == "up" if line else None)
    for name, f in _items(doc("show interfaces status | json").get("interfaceStatuses")):
        row = rows[name]
        link, kind, bandwidth = str(f.get("linkStatus") or f.get("status") or ""), str(f.get("interfaceType", "")), f.get("bandwidth")
        info = f.get("vlanInformation") if isinstance(f.get("vlanInformation"), dict) else {}
        row.update(
            description=row["description"] or _val(str(f.get("description", ""))), media=_val(kind),
            speed=_speed(bandwidth // 1000) if isinstance(bandwidth, int) else "",  # bits per second
            present=False if kind.lower() == "not present" else row["present"],
        )
        if link and row["oper_up"] is None:
            row.update(oper_up=link == "connected", admin_up=link != "disabled")
        mode = _EOS_MODE.get(str(info.get("interfaceMode", "")), "")
        if mode:
            row.update(mode=mode, vlans=[info["vlanId"]] if mode == "access" and isinstance(info.get("vlanId"), int) else [])
    for name, f in _items(doc("show ip interface | json").get("interfaces")):
        for a in f["interfaceAddress"] if isinstance(f.get("interfaceAddress"), list) else [f.get("interfaceAddress")]:
            if isinstance(a, dict):
                more = a.get("secondaryIpsOrderedList") if isinstance(a.get("secondaryIpsOrderedList"), list) else []
                for ip in (a.get("primaryIp"), *more):
                    if isinstance(ip, dict) and ip.get("maskLen") is not None and (p := _prefix(str(ip.get("address", "")), str(ip["maskLen"]))):
                        rows[name]["ip_prefixes"].append(p)
    for port, f in _items(doc("show lldp neighbors detail | json").get("lldpNeighbors")):
        found = f.get("lldpNeighborInfo")
        n = found[0] if isinstance(found, list) and found and isinstance(found[0], dict) else {}
        i = n.get("neighborInterfaceInfo") if isinstance(n.get("neighborInterfaceInfo"), dict) else {}
        if n and port in rows:
            rows[port].update(neighbor_name=str(n.get("systemName") or n.get("chassisId") or ""), neighbor_port=str(i.get("interfaceId_v2") or i.get("interfaceId") or ""))
    out = Parsed(list(rows.values()))
    inv = doc("show inventory | json")
    system = inv.get("systemInformation") if isinstance(inv.get("systemInformation"), dict) else {}
    if system.get("name") or system.get("serialNum"):
        out.components.append(_component("chassis", str(system.get("description", "")), str(system.get("name", "")), str(system.get("serialNum", "")), "chassis"))
    for slot, x in _items(inv.get("xcvrSlots")):  # `1` or `2/1`; an empty slot says `Not Present` or nothing
        if _val(str(x.get("mfgName", ""))):
            out.components.append(_component(f"Ethernet{slot}", str(x["mfgName"]), str(x.get("modelName", "")), str(x.get("serialNum", "")), "transceiver"))
            if f"Ethernet{slot}" in rows:
                rows[f"Ethernet{slot}"]["present"] = True
    return out


def _j(node: Any) -> str:
    """A Junos JSON leaf: `[{"data": x}]` (verbose), `{"data": x}` or `x` (compact, Junos 17.3 on)."""
    if isinstance(node, list):
        node = node[0] if node else ""
    if isinstance(node, dict):
        node = node.get("data", "")
    return "" if isinstance(node, (list, dict)) else str(node).strip()


def _jl(node: Any) -> list[dict[str, Any]]:
    """A Junos JSON element as a list of objects: verbose is always a list, compact only when it repeats."""
    return [x for x in (node if isinstance(node, list) else [node]) if isinstance(x, dict)]


def _chassis_parts(items: Any, trail: str | None, out: list[dict[str, Any]]) -> None:
    """`show chassis hardware` nests chassis > chassis-module > chassis-sub-module > chassis-sub-sub-module, and a
    name (`PIC 0`, `Xcvr 0`) repeats under each parent, so the stored name is the path from the first module down."""
    for item in _jl(items):
        name, descr = _j(item.get("name")), _j(item.get("description"))
        pid = _j(item.get("model-number")) or _j(item.get("part-number"))
        path = f"{trail}/{name}" if trail else name
        out.append(_component(path, descr, pid, _j(item.get("serial-number")), "transceiver" if name.startswith("Xcvr") else _ios_kind(name, descr, pid)))
        for key, sub in item.items():
            if key.startswith("chassis-"):
                _chassis_parts(sub, path if trail is not None else "", out)


def _junos(commands: dict[str, Any]) -> Parsed:
    rows = _Rows()
    doc = lambda cmd: _doc(commands.get(cmd))  # noqa: E731
    for info in _jl(doc("show interfaces terse | display json").get("interface-information")):
        for phy in _jl(info.get("physical-interface")):
            name = _j(phy.get("name"))
            if not name:
                continue
            rows[name].update(admin_up=_up(_j(phy.get("admin-status"))), oper_up=_up(_j(phy.get("oper-status"))))
            for unit in _jl(phy.get("logical-interface")):
                # an address belongs to its port, except on irb, lo0 and the like, where the unit is the interface
                home = rows[_j(unit.get("name")) or name] if VIRTUAL.match(name) else rows[name]
                if home is not rows[name]:
                    home.update(admin_up=_up(_j(unit.get("admin-status"))), oper_up=_up(_j(unit.get("oper-status"))))
                for family in _jl(unit.get("address-family")):
                    if _j(family.get("address-family-name")) in ("inet", "inet6"):
                        for a in _jl(family.get("interface-address")):
                            p = _prefix(_j(a.get("ifa-local")), "")
                            if p and not ipaddress.ip_interface(p).ip.is_link_local:
                                home["ip_prefixes"].append(p)
    for info in _jl(doc("show interfaces descriptions | display json").get("interface-information")):
        for phy in _jl(info.get("physical-interface")):
            name = _j(phy.get("name"))
            if not name:
                continue
            row = rows[name]
            row["description"] = _val(_j(phy.get("description")))
            if row["admin_up"] is None:
                row.update(admin_up=_up(_j(phy.get("admin-status"))), oper_up=_up(_j(phy.get("oper-status"))))
            for unit in _jl(phy.get("logical-interface")):
                home = rows.get(_j(unit.get("name"))) if VIRTUAL.match(name) else row
                if home is not None:
                    home["description"] = home["description"] or _val(_j(unit.get("description")))
    for n in _walk(doc("show lldp neighbors | display json"), "lldp-remote-chassis-id"):
        local = _j(n.get("lldp-local-interface")) or _j(n.get("lldp-local-port-id"))
        row = rows.get(local) or rows.get(local.split(".")[0])
        if row is not None and not row["neighbor_name"]:
            row.update(
                neighbor_name=_j(n.get("lldp-remote-system-name")) or _j(n.get("lldp-remote-chassis-id")),
                neighbor_port=_j(n.get("lldp-remote-port-id")) or _j(n.get("lldp-remote-port-description")),
            )
    out = Parsed(list(rows.values()))
    for inventory in _jl(doc("show chassis hardware | display json").get("chassis-inventory")):
        _chassis_parts(inventory.get("chassis"), None, out.components)
    return out


def _mbps(speed: Any) -> str:
    try:
        return _speed(int(float(speed)) * 1000)
    except (TypeError, ValueError):
        return ""


def _address(ip: Any, mask: Any = "") -> str:
    """`192.0.2.2` + `30` -> `192.0.2.2/30`; an unset address (`0.0.0.0`, `N/A`) is none."""
    text = str(ip or "").strip()
    return "" if text.startswith("0.0.0.0") or not text else _prefix(text, str(mask or ""))


def _firewall_chassis(model: str, serial: str, description: str) -> list[dict[str, Any]]:
    return [{"kind": "chassis", "name": "chassis", "part_number": _val(model), "serial": _val(serial), "description": description, "expires_at": None}] if model or serial else []


def _fortios(commands: dict[str, Any]) -> Parsed:
    """`ports` is the `eagent_fw` shape of reads/inventory_ports: the interface monitor (link, speed, address, MAC), the
    cmdb interfaces (admin state, VLAN id), the zones and the system status. Written from documented output, not run
    against a device. No LLDP: the FortiGate monitor lists neighbours, but no page this was written from shows its fields."""
    rows = _Rows()
    doc = commands.get("ports") if isinstance(commands.get("ports"), dict) else {}
    zone = {str(i): z["name"] for z in doc.get("zones") or [] if isinstance(z, dict) and z.get("name") for i in z.get("interfaces") or []}
    for r in doc.get("interfaces") or []:
        row = rows[str(r["name"])]
        row.update(
            alias=_val(str(r.get("alias", ""))), description=_val(str(r.get("description", ""))), mac=_val(str(r.get("mac", ""))),
            media=_val(str(r.get("type", ""))), speed=_mbps(r.get("speed")), oper_up=r["link"] if isinstance(r.get("link"), bool) else None,
        )
        if p := _address(r.get("ip"), r.get("mask")):
            row["ip_prefixes"].append(p)
    for r in doc.get("config") or []:
        row = rows[str(r["name"])]
        status = str(r.get("status", "")).lower()
        row.update(
            alias=row["alias"] or _val(str(r.get("alias", ""))), description=row["description"] or _val(str(r.get("description", ""))),
            media=row["media"] or _val(str(r.get("type", ""))), admin_up=status == "up" if status else None,
        )
        if str(r.get("vlanid", "0")).isdigit() and int(r["vlanid"]):
            row["vlans"] = [int(r["vlanid"])]
    for name, row in rows.items():
        if row["ip_prefixes"] or row["vlans"]:
            row["mode"] = "routed"  # a firewall interface is a Layer 3 port, a VLAN interface a tagged one
        if name in zone:  # the model has no zone column: it goes where an engineer looks
            row["description"] = "; ".join(x for x in (row["description"], f"zone {zone[name]}") if x)
    system = doc.get("system") if isinstance(doc.get("system"), dict) else {}
    out = Parsed(list(rows.values()))
    out.components += _firewall_chassis(
        str(system.get("model", "")), str(system.get("serial", "")),
        " ".join(x for x in (str(system.get("model_name", "")), str(system.get("model_number", ""))) if x))
    return out


def _panos(commands: dict[str, Any]) -> Parsed:
    """`ports` is the `eagent_fw` shape: `show interface all` (logical `ifnet` and `hw` entries) and `show system info`.
    Written from documented output, not run against a device. No LLDP: `show lldp neighbors all` exists, but no page
    this was written from shows its XML. `hw` state is the link; there is no admin state in it."""
    rows = _Rows()
    doc = commands.get("ports") if isinstance(commands.get("ports"), dict) else {}
    for r in doc.get("hw") or []:
        row = rows[str(r["name"])]
        state = str(r.get("state", "")).lower()
        row.update(oper_up=_up(state), mac=_val(str(r.get("mac", ""))), speed=_mbps(r.get("speed")))
    for r in doc.get("ifnet") or []:
        row = rows[str(r["name"])]
        if p := _address(r.get("ip")):
            row["ip_prefixes"].append(p)
        tag = str(r.get("tag", "0"))
        if tag.isdigit() and int(tag):
            row["vlans"] = [int(tag)]
        if row["ip_prefixes"] or row["vlans"]:
            row["mode"] = "routed"
        if r.get("zone"):
            row["description"] = f"zone {r['zone']}"
    system = doc.get("system") if isinstance(doc.get("system"), dict) else {}
    out = Parsed(list(rows.values()))
    out.components += _firewall_chassis(str(system.get("model", "")), str(system.get("serial", "")), "")
    return out


def parse(platform: str, payload: dict[str, Any]) -> Parsed:
    """Rows from an `EAGENT_PORTS` payload. An unknown platform, or a payload of the wrong shape, gives nothing."""
    commands, facts = payload.get("commands"), payload.get("facts")
    commands = commands if isinstance(commands, dict) else {}
    facts = facts if isinstance(facts, dict) else {}
    try:
        out = (
            _sonic(commands) if platform == "sonic_community" else _ios(commands, facts) if platform == "ios"
            else _nxos(commands) if platform == "nxos" else _eos(commands) if platform == "eos"
            else _junos(commands) if platform == "junos" else _fortios(commands) if platform == "fortios"
            else _panos(commands) if platform == "panos" else Parsed()
        )
    except Exception:  # noqa: BLE001 - parsing is best effort; odd output must not fail a refresh
        return Parsed()
    seen: dict[tuple, dict] = {(c["kind"], c["name"]): c for c in out.components}  # one row per key
    return Parsed(out.interfaces, list(seen.values()))
