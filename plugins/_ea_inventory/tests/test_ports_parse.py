"""Device output -> port and part rows. Pure; no database. The lab fixture is real output from the
SONiC VS switch (noise lines, commands that fail); `ios_lab` is real output from a Cisco 3725 + NM-16ESW on
IOS 12.4(15)T14; the `*_documented` fixtures are UNVERIFIED shapes. `nxos`, `eos` and `junos` fixtures are copied
from vendor pages named in each file's `_comment`; branches no cited example reaches (trunk, routed, Xcvr, compact
Junos JSON) are tested on copies altered here."""

import copy
import json
from pathlib import Path

from plugins._ea_inventory.helpers.facts import ports_parse

FIXTURES = Path(__file__).parent / "fixtures" / "ports"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def by_name(parsed):
    return {i["name"]: i for i in parsed.interfaces}


def test_lab_switch_skips_noise_and_failed_commands():
    parsed = ports_parse.parse("sonic_community", {"commands": load("sonic_community_lab")})
    ports = by_name(parsed)
    assert len(ports) == 32 and ports["Ethernet0"]["alias"] == "fortyGigE0/0" and ports["Ethernet124"]["speed"] == "100G"
    assert ports["Ethernet0"]["mode"] == "routed" and ports["Ethernet0"]["oper_up"] is None  # N/A is unknown, not down
    assert ports["Ethernet0"]["present"] is False and ports["Ethernet0"]["description"] == ""
    assert all(not p["vlans"] and not p["neighbor_name"] and not p["ip_prefixes"] for p in ports.values())  # those commands failed
    assert parsed.components == [{
        "kind": "chassis", "name": "chassis", "part_number": "", "serial": "", "description": "HwSKU Force10-S6000", "expires_at": None,
    }]


def test_sonic_tables_vlans_neighbours_ips_and_optics():
    parsed = ports_parse.parse("sonic_community", load("sonic_community_documented"))
    ports = by_name(parsed)
    assert (
        ports["Ethernet0"]["oper_up"], ports["Ethernet0"]["admin_up"], ports["Ethernet0"]["description"],
        ports["Ethernet0"]["neighbor_name"], ports["Ethernet0"]["neighbor_port"], ports["Ethernet0"]["ip_prefixes"],
        ports["Ethernet0"]["media"], ports["Ethernet0"]["present"],
    ) == (True, True, "to spine1", "spine1", "Ethernet12", ["10.0.0.0/31"], "QSFP28 or later", True)
    assert (ports["Ethernet4"]["mode"], ports["Ethernet4"]["vlans"], ports["Ethernet4"]["oper_up"]) == ("trunk", [10, 20], False)
    assert (ports["Ethernet8"]["mode"], ports["Ethernet8"]["vlans"]) == ("access", [10])  # the grid's continuation row
    assert ports["eth0"]["ip_prefixes"] == ["192.168.1.5/24"]
    kinds = {(c["kind"], c["name"]): c for c in parsed.components}
    assert kinds[("transceiver", "Ethernet0")]["description"] == "QSFP28 or later" and ("transceiver", "Ethernet4") not in kinds
    assert kinds[("chassis", "chassis")]["part_number"] == "VND-48X" and kinds[("chassis", "chassis")]["serial"] == "SN123"


def test_ios_status_vlans_poe_neighbours_and_parts():
    parsed = ports_parse.parse("ios", load("ios_documented"))
    ports = by_name(parsed)
    g1 = ports["GigabitEthernet1/0/1"]
    assert (g1["mode"], g1["oper_up"], g1["speed"], g1["poe_watts"], g1["neighbor_name"]) == ("trunk", True, "1000M", 15.4, "core1.example.net")
    assert ports["GigabitEthernet1/0/3"]["admin_up"] is False  # disabled
    assert ports["GigabitEthernet1/0/4"]["vlans"] == [10] and ports["GigabitEthernet1/0/5"]["vlans"] == [10]  # continuation line
    assert ports["GigabitEthernet1/0/2"]["poe_watts"] == 0.0 and ports["TenGigabitEthernet1/1/1"]["speed"] == "10G"
    assert ports["TenGigabitEthernet1/1/2"]["present"] is False  # sfpAbsent
    assert ports["Vlan10"]["ip_prefixes"] == ["10.10.0.2/24"]
    kinds = {c["name"]: c["kind"] for c in parsed.components}
    assert kinds == {
        "c93xx Stack": "chassis", "Switch 1": "stack_member", "Switch 1 - Power Supply A": "psu",
        "TenGigabitEthernet1/1/1": "transceiver", "network-advantage": "licence",
    }


def test_ios_lab_3725_with_etherswitch_module():
    parsed = ports_parse.parse("ios", load("ios_lab"))  # `show vlan brief`, `show power inline`, `show license summary` came back empty
    ports = by_name(parsed)
    assert len(ports) == 22 and ports["FastEthernet0/0"]["ip_prefixes"] == ["172.30.30.20/24"]
    assert (ports["FastEthernet0/0"]["oper_up"], ports["FastEthernet1/0"]["oper_up"], ports["FastEthernet1/0"]["admin_up"]) == (True, False, True)
    assert (ports["FastEthernet1/0"]["mode"], ports["FastEthernet1/0"]["vlans"], ports["FastEthernet1/0"]["description"]) == ("access", [10], "access VLAN 10 USERS")
    assert (ports["FastEthernet1/2"]["mode"], ports["FastEthernet1/2"]["vlans"]) == ("trunk", [])  # the status table says VLAN 1; switchport says trunk
    assert ports["FastEthernet1/15"]["vlans"] == [1] and ports["Vlan10"]["ip_prefixes"] == ["10.10.10.1/24"]
    assert [(c["kind"], c["part_number"], c["serial"]) for c in parsed.components] == [("chassis", "", "FTX0945W0MY"), ("module", "NM-16ESW=", "")]


def test_ios_vlan_fallback_and_switchport_vlan_lists():
    only_old = {"commands": {"show vlan-switch brief": ["VLAN Name Status Ports", "---- ---- ------ ----", "10   USERS   active    Fa1/0"],
                             "show interfaces switchport": ["Name: Fa1/1", "Administrative Mode: trunk", "Trunking VLANs Enabled: 1,10,20-22,", "  30"]},
                "facts": {"net_interfaces": {"FastEthernet1/0": {}, "FastEthernet1/1": {"lineprotocol": "up (connected)"}}}}
    ports = by_name(ports_parse.parse("ios", only_old))
    assert (ports["FastEthernet1/0"]["mode"], ports["FastEthernet1/0"]["vlans"]) == ("access", [10])
    assert (ports["FastEthernet1/1"]["mode"], ports["FastEthernet1/1"]["vlans"], ports["FastEthernet1/1"]["oper_up"]) == ("trunk", [1, 10, 20, 21, 22, 30], True)


def test_odd_input_gives_nothing_not_an_error():
    for platform, payload in (
        ("sonic_community", {}), ("sonic_community", {"commands": "x"}), ("sonic_community", {"commands": {"show interfaces status": "x"}}),
        ("sonic_community", {"commands": {"show interfaces status": ["Error: No such command", "Usage: show"]}}),
        ("ios", {"commands": None, "facts": [1]}), ("eos", load("ios_documented")),
    ):
        parsed = ports_parse.parse(platform, payload)
        assert parsed.interfaces == [] and parsed.components == []


def test_mgmt_subnet_is_the_one_holding_the_address_even_through_nat():
    from plugins._ea_inventory.helpers import ports

    rows = [{**r, "is_uplink": False} for r in by_name(ports_parse.parse("ios", load("ios_lab"))).values()]
    assert ports.summary(rows, [], "172.30.30.21")["mgmt_subnet"] == "172.30.30.0/24"  # the device holds .20
    assert ports.summary(rows, [], "172.30.30.20")["mgmt_subnet"] == "172.30.30.0/24"
    assert ports.summary(rows, [], "10.9.9.9")["mgmt_subnet"] == "" and ports.summary(rows, [], "")["mgmt_subnet"] == ""


def test_nxos_documented_status_switchport_ips_neighbours_and_parts():
    parsed = ports_parse.parse("nxos", load("nxos_documented"))
    ports = by_name(parsed)
    assert (ports["mgmt0"]["mode"], ports["mgmt0"]["speed"], ports["mgmt0"]["oper_up"], ports["mgmt0"]["media"]) == ("routed", "1000M", True, "")
    assert (ports["mgmt0"]["neighbor_name"], ports["mgmt0"]["neighbor_port"]) == ("SW-INSBU-JWALA-PP52.cisco.com", "Gi1/0/35")
    e1 = ports["Ethernet9/1"]
    assert (e1["mode"], e1["vlans"], e1["oper_up"], e1["admin_up"], e1["media"]) == ("access", [2], False, True, "SFP-H10GB-CU3M")
    assert (ports["Ethernet9/2"]["speed"], ports["Ethernet9/2"]["oper_up"]) == ("10G", True) and ports["Ethernet9/4"]["present"] is False  # sfpAbsent
    assert (ports["port-channel12"]["mode"], ports["port-channel12"]["vlans"]) == ("access", [1])
    assert ports["Ethernet1/1.53"]["ip_prefixes"] == ["89.1.4.1/24"] and ports["Ethernet1/1.53"]["oper_up"] is True
    assert (ports["Ethernet9/1"]["neighbor_name"], ports["Ethernet9/1"]["poe_watts"]) == ("", None)
    kinds = {c["name"]: (c["kind"], c["part_number"]) for c in parsed.components}
    assert kinds["Chassis"] == ("chassis", "N9K-C9508") and kinds["Slot 27"] == ("module", "N9K-SUP-A")
    assert kinds["Power Supply 1"][0] == "psu" and kinds["Fan 3"][0] == "fan" and len(kinds) == 18


def test_nxos_trunks_one_row_tables_vrf_nesting_short_names_and_optics():
    doc = load("nxos_documented")
    trunk = copy.deepcopy(doc["commands"]["show interface switchport | json"])
    rows = trunk["TABLE_interface"]["ROW_interface"]
    rows[0].update(oper_mode="trunk", trunk_vlans="1,10,20-22")
    rows[1].update(oper_mode="trunk", trunk_vlans="1-4094")  # every VLAN is not a list
    rows[2]["switchport"] = "Disabled"
    commands = {
        "show interface switchport | json": trunk,
        "show interface status | json": {"TABLE_interface": {"ROW_interface": doc["commands"]["show interface status | json"]["TABLE_interface"]["ROW_interface"][1]}},  # one row: a dict
        "show ip interface vrf all | json": {"TABLE_vrf": {"ROW_vrf": [{"TABLE_intf": {"ROW_intf": [
            {"intf-name": "Vlan10", "prefix": "10.0.0.1", "masklen": "24", "admin-state": "TRUE", "proto-state": "FALSE"},
            {"intf-name": "loopback0", "prefix": "10.9.9.9"}]}}]}},  # no masklen: no address
        "show lldp neighbors detail | json": {"TABLE_nbor_detail": {"ROW_nbor_detail": {"l_port_id": "Eth9/1", "chassis_id": "18ef.6332.e5a3", "port_id": "Gi1/0/35"}}},
        "show inventory | json": {"TABLE_inv": {"ROW_inv": [{"name": '"Ethernet9/1"', "desc": '"10Gbase-SR SFP+"', "productid": "SFP-10G-SR", "serialnum": "0000000"}]}},
    }
    parsed = ports_parse.parse("nxos", {"commands": commands})
    ports = by_name(parsed)
    assert (ports["Ethernet9/1"]["mode"], ports["Ethernet9/1"]["vlans"]) == ("trunk", [1, 10, 20, 21, 22])
    assert ports["Ethernet9/1"]["neighbor_name"] == "18ef.6332.e5a3" and ports["Ethernet9/1"]["neighbor_port"] == "Gi1/0/35"  # no sys_name: the chassis id
    assert ports["Ethernet9/1"]["present"] is True and set(ports) == {"Ethernet9/1", "Ethernet9/2", "Vlan10", "loopback0"}
    assert (ports["Ethernet9/2"]["mode"], ports["Ethernet9/2"]["vlans"]) == ("trunk", [])  # port-channel12 is `Disabled`: no row
    assert (ports["Vlan10"]["ip_prefixes"], ports["Vlan10"]["admin_up"], ports["Vlan10"]["oper_up"]) == (["10.0.0.1/24"], True, False)
    assert ports["loopback0"]["ip_prefixes"] == []
    assert parsed.components == [{"kind": "transceiver", "name": "Ethernet9/1", "part_number": "SFP-10G-SR", "serial": "", "description": "10Gbase-SR SFP+", "expires_at": None}]


def test_eos_documented_status_description_ips_neighbours_and_parts():
    parsed = ports_parse.parse("eos", load("eos_documented"))
    ports = by_name(parsed)
    e8 = ports["Ethernet8"]
    assert (e8["mode"], e8["vlans"], e8["speed"], e8["media"], e8["admin_up"], e8["oper_up"]) == ("access", [1], "10G", "10GBASE-SR", True, True)
    assert (ports["Ethernet2"]["admin_up"], ports["Ethernet2"]["oper_up"]) == (False, False)  # adminDown
    assert ports["Ethernet9"]["present"] is False and ports["Ethernet9"]["media"] == "" and ports["Ethernet9"]["oper_up"] is False  # `Not Present`
    assert ports["Ethernet2"]["ip_prefixes"] == ["172.30.11.1/31", "10.10.10.1/31", "10.10.10.10/31"]
    assert (ports["Ethernet2"]["neighbor_name"], ports["Ethernet2"]["neighbor_port"]) == ("DC1-SPINE2.local.com", "Ethernet1")
    kinds = {(c["kind"], c["name"]): c for c in parsed.components}
    assert kinds[("chassis", "chassis")]["part_number"] == "DCS-7050SX-128" and kinds[("chassis", "chassis")]["serial"] == "JPE14080459"
    assert set(kinds) == {("chassis", "chassis"), ("transceiver", "Ethernet1"), ("transceiver", "Ethernet2")}  # slot 3 is `Not Present`
    assert kinds[("transceiver", "Ethernet1")]["part_number"] == "QSFP-100G-DR" and ports["Ethernet2"]["present"] is True


def test_eos_modes_revision_one_addresses_and_errdisabled():
    commands = copy.deepcopy(load("eos_documented")["commands"])
    status = commands["show interfaces status | json"]["interfaceStatuses"]
    status["Ethernet8"]["vlanInformation"] = {"interfaceMode": "trunk", "interfaceForwardingModel": "bridged"}
    status["Ethernet9"]["vlanInformation"] = {"interfaceMode": "routed"}
    status["Ethernet10"] = {"linkStatus": "disabled", "bandwidth": 1000000000, "interfaceType": "1000BASE-T"}
    status["Ethernet11"] = {"status": "errdisabled", "description": "spare"}  # the `... errdisabled` form calls the key `status`
    commands["show interfaces description | json"] = {}
    commands["show ip interface | json"] = {"interfaces": {"Vlan10": {"interfaceAddress": [{"primaryIp": {"address": "10.0.0.1", "maskLen": 24}}]}}}  # revision 1
    ports = by_name(ports_parse.parse("eos", {"commands": commands}))
    assert (ports["Ethernet8"]["mode"], ports["Ethernet8"]["vlans"]) == ("trunk", []) and (ports["Ethernet9"]["mode"], ports["Ethernet9"]["vlans"]) == ("routed", [])
    assert (ports["Ethernet10"]["admin_up"], ports["Ethernet10"]["oper_up"], ports["Ethernet10"]["speed"]) == (False, False, "1G")
    assert (ports["Ethernet11"]["oper_up"], ports["Ethernet11"]["admin_up"], ports["Ethernet11"]["description"]) == (False, True, "spare")
    assert ports["Vlan10"]["ip_prefixes"] == ["10.0.0.1/24"]


def junos_commands():
    return load("junos_documented")["commands"]


def compact(node):
    """Junos 17.3+ compact JSON: a one-value array is its value, `{"data": x}` is `x`."""
    if isinstance(node, list):
        node = [compact(x) for x in node]
        return node[0] if len(node) == 1 else node
    if isinstance(node, dict):
        return compact(node["data"]) if set(node) == {"data"} else {k: compact(v) for k, v in node.items()}
    return node


def test_junos_documented_terse_descriptions_lldp_and_parts():
    parsed = ports_parse.parse("junos", {"commands": junos_commands()})
    ports = by_name(parsed)
    assert (ports["ge-0/0/0"]["admin_up"], ports["ge-0/0/0"]["oper_up"], ports["ge-0/0/1"]["oper_up"]) == (True, False, True)
    assert "ge-0/0/1.0" not in ports and ports["ge-0/0/1"]["ip_prefixes"] == []  # an aenet unit carries no address
    assert ports["fxp0.0"]["ip_prefixes"] == ["192.168.71.249/21"] and ports["lo0.0"]["ip_prefixes"] == ["10.0.1.4/32", "127.0.0.1/32"]
    assert ports["ge-3/0/0"]["description"] == "SMB-OSPF_Area300" and ports["so-1/0/0"]["description"] == "M20-3#1"
    assert (ports["ge-0/0/5"]["neighbor_name"], ports["ge-0/0/5"]["neighbor_port"]) == ("EX4600-2", "ge-0/0/5")  # ge-0/0/20 is not in the table
    assert (ports["ge-0/0/5"]["mode"], ports["ge-0/0/5"]["speed"], ports["ge-0/0/5"]["poe_watts"]) == ("", "", None)
    assert [(c["kind"], c["name"], c["part_number"], c["serial"]) for c in parsed.components] == [
        ("chassis", "Chassis", "", "XX00X00XXXXX"), ("module", "Midplane", "CHAS-BP-MX960-S", "XX0001")]


def test_junos_text_with_a_banner_compact_json_and_the_transceiver_path():
    commands = junos_commands()
    text = {k: "{master:0}\n" + "\n".join(v) for k, v in commands.items()}  # EX virtual chassis print a banner first
    assert ports_parse.parse("junos", {"commands": text}) == ports_parse.parse("junos", {"commands": commands})
    shrunk = {k: compact(json.loads("\n".join(v))) for k, v in commands.items()}
    assert ports_parse.parse("junos", {"commands": shrunk}) == ports_parse.parse("junos", {"commands": commands})
    hardware = json.loads("\n".join(commands["show chassis hardware | display json"]))
    fpc = lambda n, sub: {"name": [{"data": f"FPC {n}"}], "chassis-sub-module": [{"name": [{"data": "PIC 0"}], "chassis-sub-sub-module": [
        {"name": [{"data": "Xcvr 0"}], "serial-number": [{"data": sub}], "part-number": [{"data": "740-031980"}], "model-number": [{"data": "SFP+-10G-SR"}],
         "description": [{"data": "SFP+-10G-SR"}]}]}]}
    hardware["chassis-inventory"][0]["chassis"][0]["chassis-module"] = [
        fpc(0, "AA1"), fpc(1, "BUILTIN"), {"name": [{"data": "PEM 0"}], "description": [{"data": "PS 1.4-2.5kW; 90-264V AC in"}]}]
    parts = {c["name"]: c for c in ports_parse.parse("junos", {"commands": {"show chassis hardware | display json": json.dumps(hardware)}}).components}
    assert parts["FPC 0/PIC 0/Xcvr 0"]["kind"] == "transceiver" and parts["FPC 1/PIC 0/Xcvr 0"]["serial"] == ""  # BUILTIN is not a serial
    assert (parts["FPC 0/PIC 0/Xcvr 0"]["part_number"], parts["FPC 0/PIC 0/Xcvr 0"]["serial"]) == ("SFP+-10G-SR", "AA1")
    assert parts["PEM 0"]["kind"] == "psu" and parts["FPC 0"]["kind"] == "module"


def test_structured_platforms_ignore_junk_and_failed_commands():
    for platform in ("nxos", "eos", "junos"):
        for value in ("", "% Invalid command", "{broken", [], ["{master:0}"], None, 7, {"x": 1}, {"interfaceStatuses": []}, [{"a": 1}]):
            parsed = ports_parse.parse(platform, {"commands": {c: value for c in load(f"{platform}_documented")["commands"]}})
            assert parsed.interfaces == [] and parsed.components == [], (platform, value)


def firewall_payload(platform):
    """The documented API answers, through the executor's own filter, as the playbook would print them."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "eagent_firewall", Path(__file__).parents[2] / "_ea_ansible" / "playbooks" / "reads" / "filter_plugins" / "eagent_firewall.py")
    fw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fw)
    answers = {k: v for k, v in load(f"{platform}_documented").items() if k != "_comment"}
    requests = {  # what reads/inventory_ports sends, in order
        "fortios": ["/api/v2/monitor/system/interface?include_vlan=true",
                    "/api/v2/cmdb/system/interface?format=name%7Calias%7Cdescription%7Cstatus%7Ctype%7Cvlanid%7Cinterface%7Crole",
                    "/api/v2/cmdb/system/zone?format=name%7Cinterface", "/api/v2/monitor/system/status"],
        "panos": ["<show><interface>all</interface></show>", "<show><system><info></info></system></show>"],
    }[platform]
    results = [{"status": 200, "content": json.dumps(answers[r]) if platform == "fortios" else answers[r]} for r in requests]
    return {"platform": platform, "commands": {"ports": fw.eagent_fw(results, platform, "ports")}, "facts": {}}


def test_fortios_ports_merge_monitor_and_config_and_carry_zone_vlan_and_chassis():
    parsed = ports_parse.parse("fortios", firewall_payload("fortios"))
    ports = by_name(parsed)
    assert set(ports) == {"port1", "port2", "port3", "port4", "vlan10"}  # port4 is only in the config: still a port
    p1 = ports["port1"]
    assert (p1["oper_up"], p1["admin_up"], p1["speed"], p1["ip_prefixes"], p1["mode"], p1["alias"], p1["mac"]) == (
        True, True, "1G", ["192.0.2.2/30"], "routed", "wan", "00:09:0f:00:00:01")
    assert (ports["port2"]["oper_up"], ports["port2"]["admin_up"], ports["port2"]["ip_prefixes"], ports["port2"]["mode"]) == (False, False, [], "")  # 0.0.0.0 is no address
    assert ports["port3"]["speed"] == "10G" and ports["port3"]["description"] == "zone LAN"
    assert (ports["vlan10"]["vlans"], ports["vlan10"]["description"], ports["vlan10"]["ip_prefixes"]) == ([10], "staff; zone LAN", ["10.10.0.1/24"])
    assert all(p["poe_watts"] is None and not p["neighbor_name"] for p in ports.values())
    assert parsed.components == [{"kind": "chassis", "name": "chassis", "part_number": "FGT60F", "serial": "FGT60F0000000001",
                                  "description": "FortiGate 60F", "expires_at": None}]


def test_panos_ports_join_hardware_and_logical_entries():
    parsed = ports_parse.parse("panos", firewall_payload("panos"))
    ports = by_name(parsed)
    assert set(ports) == {"ethernet1/1", "ethernet1/2", "ethernet1/2.100", "tunnel.1"}
    e1 = ports["ethernet1/1"]
    assert (e1["oper_up"], e1["speed"], e1["mac"], e1["ip_prefixes"], e1["description"], e1["mode"]) == (
        True, "1G", "00:1b:17:00:00:11", ["192.0.2.2/30"], "zone untrust", "routed")
    assert (ports["ethernet1/2"]["oper_up"], ports["ethernet1/2"]["speed"]) == (False, "")  # `auto` is no speed
    assert (ports["ethernet1/2.100"]["vlans"], ports["ethernet1/2.100"]["ip_prefixes"]) == ([100], ["10.1.0.1/24"])
    assert ports["tunnel.1"]["ip_prefixes"] == [] and ports["tunnel.1"]["description"] == "zone vpn"  # `N/A` is no address
    assert [(c["kind"], c["part_number"], c["serial"]) for c in parsed.components] == [("chassis", "PA-440", "012345678901")]


def test_a_firewall_payload_of_the_wrong_shape_gives_nothing():
    for platform in ("fortios", "panos"):
        assert ports_parse.parse(platform, {"commands": {"ports": "garbage"}}).interfaces == []
        assert ports_parse.parse(platform, {"commands": {"ports": {"interfaces": [{"no": "name"}], "hw": [3]}}}).interfaces == []


def test_a_vlan_range_past_4094_is_not_expanded():
    """`1-200000000` from a device built a list of two hundred million numbers."""
    assert ports_parse._vlan_list("1,10,20-22") == [1, 10, 20, 21, 22]
    assert ports_parse._vlan_list("1-200000000,7") == [7]
