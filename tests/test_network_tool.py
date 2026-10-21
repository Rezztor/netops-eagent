"""The appliance's network tool (`deploy/iso/host/rezztor-network`), without a machine.

Four halves. The plain functions from an answers file to the four files (netplan,
the network list, guard.json, the Compose variables) and what they refuse. The
netplan files, read by the real `netplan generate` in a throwaway Debian 13
container. "Keep or go back" with a clock and a machine that are both fake. The
console questions, typed from a script.

What none of this proves: that `netplan apply` on a real Debian 13 leaves the
other interfaces up, what `arping -D` returns there, or that the systemd timer
fires after a lost session. Those need a machine; the report says so.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDES = ROOT / "docs" / "guides"
IMAGE = "rezztor-test-netplan:trixie"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    # No bytecode beside the tool: the ISO build copies that directory as it is.
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writes
    return module


tool = _load("rezztor_network", ROOT / "deploy" / "iso" / "host" / "rezztor-network")
guard = _load("rezztor_guard_for_network", ROOT / "deploy" / "customer" / "rezztor-guard")

# The owner's scenario. Case B: a physical server, ONE port on a trunk, three VLAN sub-interfaces.
CASE_B = """\
NET1_NAME=VLAN 300
NET1_ROLES=internet users
NET1_IFACE=eno1
NET1_VLAN=300
NET1_MODE=static
NET1_ADDRESS=10.0.30.5/24
NET1_GATEWAY=10.0.30.1
NET1_DNS=10.0.30.1
NET1_SSH=yes
NET2_NAME=VLAN 100
NET2_ROLES=devices
NET2_IFACE=eno1
NET2_VLAN=100
NET2_MODE=static
NET2_ADDRESS=10.0.100.5/24
NET2_SSH=no
NET2_TEST=10.0.100.10:22
NET3_NAME=VLAN 200
NET3_ROLES=devices
NET3_IFACE=eno1
NET3_VLAN=200
NET3_MODE=static
NET3_ADDRESS=10.0.200.5/24
NET3_GATEWAY=10.0.200.1
NET3_ROUTES=10.20.0.0/16
NET3_SSH=no
NET3_TEST=10.20.0.9:443
"""
# Case A: a virtual machine with one virtual card per port group. VMware writes the tag.
CASE_A = """\
NET1_NAME=VLAN 300
NET1_ROLES=internet users
NET1_IFACE=ens192
NET1_MAC=00:50:56:aa:bb:01
NET1_MODE=static
NET1_ADDRESS=10.0.30.5/24
NET1_GATEWAY=10.0.30.1
NET1_DNS=10.0.30.1
NET1_SSH=yes
NET2_NAME=VLAN 100
NET2_ROLES=devices
NET2_IFACE=ens224
NET2_MAC=00:50:56:aa:bb:02
NET2_MODE=static
NET2_ADDRESS=10.0.100.5/24
NET2_SSH=no
NET2_TEST=10.0.100.10:22
NET3_NAME=VLAN 200
NET3_ROLES=devices
NET3_IFACE=ens256
NET3_MAC=00:50:56:aa:bb:03
NET3_MODE=static
NET3_ADDRESS=10.0.200.5/24
NET3_GATEWAY=10.0.200.1
NET3_ROUTES=10.20.0.0/16
NET3_SSH=no
NET3_TEST=10.20.0.9:443
"""
FLAT_DHCP = "NET_IFACE=ens192\nNET_MODE=dhcp\n"
FLAT_STATIC = "NET_IFACE=ens192\nNET_MODE=static\nNET_ADDRESS=192.168.10.20/24\nNET_GATEWAY=192.168.10.1\nNET_DNS=192.168.10.1 1.1.1.1\n"
OFF = {"dhcp6": False, "accept-ra": False, "link-local": []}


def answers(text, **changes):
    a = tool.read_answers(text)
    for key, value in changes.items():
        a.pop(key, None) if value is None else a.__setitem__(key, value)
    return a


def plan(text, observed=None, **changes):
    checked = tool.check(answers(text, **changes), observed)
    assert not checked.errors, checked.errors
    return checked.plan


def errors(text, **changes):
    return "\n".join(tool.check(answers(text, **changes)).errors)


def yaml(text):
    """The generated file is JSON under one comment line, which is YAML too."""
    comment, _, body = text.partition("\n")
    assert comment.startswith("#")
    return json.loads(body)["network"]


# --- answers to files ----------------------------------------------------------


def test_case_b_one_port_on_a_trunk_becomes_three_vlan_interfaces():
    """The file of research 04, section 1.1: the port itself has no address, the
    way to the internet is on VLAN 300 alone, and VLAN 200 has one route."""
    net = yaml(tool.netplan(plan(CASE_B)))
    assert net["version"] == 2 and net["renderer"] == "networkd"
    assert net["ethernets"] == {"eno1": {"dhcp4": False, **OFF}}, "the trunk port carries tagged frames only"
    assert net["vlans"] == {
        "vlan300": {"id": 300, "link": "eno1", "dhcp4": False, **OFF, "addresses": ["10.0.30.5/24"],
                    "routes": [{"to": "default", "via": "10.0.30.1"}], "nameservers": {"addresses": ["10.0.30.1"]}},
        "vlan100": {"id": 100, "link": "eno1", "dhcp4": False, **OFF, "addresses": ["10.0.100.5/24"]},
        "vlan200": {"id": 200, "link": "eno1", "dhcp4": False, **OFF, "addresses": ["10.0.200.5/24"],
                    "routes": [{"to": "10.20.0.0/16", "via": "10.0.200.1"}]},
    }


def test_case_a_three_cards_are_matched_by_mac_address_and_nothing_is_tagged():
    net = yaml(tool.netplan(plan(CASE_A)))
    assert "vlans" not in net
    assert net["ethernets"]["ens224"] == {"match": {"macaddress": "00:50:56:aa:bb:02"}, "set-name": "ens224",
                                          "dhcp4": False, **OFF, "addresses": ["10.0.100.5/24"]}
    assert net["ethernets"]["ens256"]["routes"] == [{"to": "10.20.0.0/16", "via": "10.0.200.1"}]
    assert net["ethernets"]["ens192"]["routes"] == [{"to": "default", "via": "10.0.30.1"}]


@pytest.mark.parametrize("case", [CASE_A, CASE_B])
def test_exactly_one_default_route_and_ipv6_off_everywhere(case):
    net = yaml(tool.netplan(plan(case)))
    bodies = [*net["ethernets"].values(), *net.get("vlans", {}).values()]
    assert sum(route["to"] == "default" for body in bodies for route in body.get("routes", [])) == 1
    for body in bodies:
        assert body["accept-ra"] is False and body["link-local"] == [] and body["dhcp6"] is False


@pytest.mark.parametrize("case, interfaces", [(CASE_A, ["ens192", "ens224", "ens256"]), (CASE_B, ["vlan300", "vlan100", "vlan200"])])
def test_the_network_list_is_the_one_the_guard_and_the_product_read(case, interfaces):
    """The contract of the N0/N1 report, unchanged, and accepted by the real generator."""
    p = plan(case)
    doc = json.loads(tool.network_list(p, {}, 1759700000))
    assert doc["version"] == 1 and doc["generated_at"] == 1759700000
    assert [n["interface"] for n in doc["networks"]] == interfaces
    assert set(doc["networks"][0]) == {"name", "roles", "interface", "parent", "vlan", "address", "gateway", "subnet",
                                       "routed_subnets", "state"}
    by_name = {n["name"]: n for n in doc["networks"]}
    assert by_name["VLAN 100"] | {"interface": 0, "parent": 0, "vlan": 0} == {
        "name": "VLAN 100", "roles": ["devices"], "interface": 0, "parent": 0, "vlan": 0, "address": "10.0.100.5/24",
        "gateway": None, "subnet": "10.0.100.0/24", "routed_subnets": [], "state": "up"}
    assert by_name["VLAN 200"]["routed_subnets"] == ["10.20.0.0/16"] and by_name["VLAN 200"]["gateway"] == "10.0.200.1"
    assert by_name["VLAN 300"]["roles"] == ["internet", "users"]
    assert (by_name["VLAN 100"]["parent"], by_name["VLAN 100"]["vlan"]) == (("eno1", 100) if case is CASE_B else (None, None))
    conf = guard.check_conf(json.loads(tool.guard_conf(p)))
    assert conf["form"] == "full" and conf["ssh_if"] == [interfaces[0]]
    rules = guard.rules(guard.check_networks(doc, conf["container_block"]), conf, ssh=True)
    assert f'set dev_if {{ type ifname; elements = {{ "{min(interfaces[1:])}", "{max(interfaces[1:])}" }} }}' in rules
    assert "set dev_only4 { type ipv4_addr; flags interval; elements = { 10.0.100.0/24, 10.0.200.0/24, 10.20.0.0/16 } }" in rules
    assert f'set ssh_if {{ type ifname; elements = {{ "{interfaces[0]}" }} }}' in rules


def test_one_flat_network_writes_no_list_and_never_says_vlan_role_or_route():
    """The simple customer. With no list the guard and the product know nothing
    about the network and behave as on any single network."""
    for text in (FLAT_DHCP, FLAT_STATIC):
        p = plan(text)
        assert tool.network_list(p, {}, 0) is None
        assert json.loads(tool.guard_conf(p)) == {"form": "full", "container_block": "172.31.248.0/21", "infra": [], "app_services": []}
        live = {"ens192": {"address": ipaddress.IPv4Interface("192.168.10.57/24"), "gateway": "192.168.10.1", "note": "up, gateway answers"}}
        assert not re.search(r"vlan|role|route", tool.result(p, live), re.I)
    assert yaml(tool.netplan(plan(FLAT_DHCP)))["ethernets"] == {"ens192": {"dhcp4": True, **OFF}}
    static = yaml(tool.netplan(plan(FLAT_STATIC)))["ethernets"]["ens192"]
    assert static["addresses"] == ["192.168.10.20/24"] and static["routes"] == [{"to": "default", "via": "192.168.10.1"}]
    assert static["nameservers"] == {"addresses": ["192.168.10.1", "1.1.1.1"]}


def test_a_dhcp_uplink_keeps_its_lease_routes_and_no_other_dhcp_network_may_add_one():
    text = ("NET1_NAME=Office\nNET1_ROLES=internet users\nNET1_IFACE=ens192\nNET1_MODE=dhcp\n"
            "NET2_NAME=Lab\nNET2_ROLES=users\nNET2_IFACE=ens224\nNET2_MODE=dhcp\n"
            "NET3_NAME=Devices\nNET3_ROLES=devices\nNET3_IFACE=ens256\nNET3_ADDRESS=10.0.100.5/24\n")
    net = yaml(tool.netplan(plan(text)))["ethernets"]
    assert net["ens192"] == {"dhcp4": True, **OFF}, "the internet network takes its default route and DNS from the lease"
    assert net["ens224"]["dhcp4-overrides"] == {"use-routes": False, "use-dns": False}
    # The leased address is only known afterwards; the list is written from what was seen.
    lease = ipaddress.IPv4Interface("10.0.30.57/24")
    live = {"ens192": {"address": lease, "gateway": ipaddress.IPv4Address("10.0.30.1"), "link": True}}
    listed = json.loads(tool.network_list(plan(text, {"ens192": lease}), live, 0))["networks"]
    assert [n["name"] for n in listed] == ["Office", "Devices"], "a network with no lease yet is not in the list"
    assert listed[0] | {"state": 0} == {
        "name": "Office", "roles": ["internet", "users"], "interface": "ens192", "parent": None, "vlan": None,
        "address": "10.0.30.57/24", "gateway": "10.0.30.1", "subnet": "10.0.30.0/24", "routed_subnets": [], "state": 0}
    # A lease that lands on a subnet somebody typed is an overlap like any other.
    clash = tool.check(answers(text), {"ens192": ipaddress.IPv4Interface("10.0.100.77/24")}).errors
    assert clash and "overlaps" in clash[0]
    assert "DHCP is not offered" in errors(text, NET3_ADDRESS=None, NET3_MODE="dhcp")


def test_no_internet_at_all_is_valid_and_has_no_default_route():
    text = ("NET1_NAME=Users\nNET1_ROLES=users\nNET1_IFACE=ens192\nNET1_ADDRESS=10.0.30.5/24\nNET1_GATEWAY=10.0.30.1\n"
            "NET1_ROUTES=10.0.40.0/24\nNET2_NAME=Devices\nNET2_ROLES=devices\nNET2_IFACE=ens224\nNET2_ADDRESS=10.0.100.5/24\n")
    net = yaml(tool.netplan(plan(text)))["ethernets"]
    assert net["ens192"]["routes"] == [{"to": "10.0.40.0/24", "via": "10.0.30.1"}], "users in another subnet still get an answer"
    assert "default" not in json.dumps(net)
    assert plan(FLAT_STATIC, NET_GATEWAY=None)["nets"][0]["roles"] == ("users", "devices")
    assert "needs a gateway" in errors(text, NET1_ROLES="internet users", NET1_GATEWAY=None, NET1_ROUTES=None)


def test_an_overlap_is_refused_and_both_sides_are_named():
    text = errors(CASE_B, NET3_ROUTES="10.20.0.0/16 10.0.100.128/25")
    assert "10.0.100.128/25 behind the gateway of VLAN 200 overlaps the subnet 10.0.100.0/24 of VLAN 100" in text
    assert "the same subnet in two networks is not in this build" in text
    assert "overlaps the subnet 10.0.30.0/24 of VLAN 300" in errors(CASE_B, NET2_ADDRESS="10.0.30.9/24", NET2_TEST=None)


def test_a_routed_subnet_that_contains_another_networks_gateway_is_refused():
    """The gateway of VLAN 300 inside a subnet "behind VLAN 200": the internet
    traffic would go into the device network and die there."""
    text = errors(CASE_B, NET3_ROUTES="10.0.0.0/18")
    assert "10.0.0.0/18 behind the gateway of VLAN 200 overlaps the subnet 10.0.30.0/24 of VLAN 300" in text
    # A DNS or NTP server or the proxy inside a routed subnet is the customer's call: a warning.
    checked = tool.check(answers(CASE_B, NET1_DNS="10.20.1.1", NTP="10.20.1.2", PROXY="http://10.20.1.3:3128"))
    assert not checked.errors
    assert [w.split(". ")[0] for w in checked.warnings] == [
        "10.20.0.0/16, behind the gateway of VLAN 200, contains the DNS server 10.20.1.1",
        "10.20.0.0/16, behind the gateway of VLAN 200, contains the NTP server 10.20.1.2",
        "10.20.0.0/16, behind the gateway of VLAN 200, contains the proxy 10.20.1.3",
    ]


def test_a_collision_with_the_container_block_proposes_the_next_free_one():
    checked = tool.check(answers(CASE_B, NET3_ROUTES="10.20.0.0/16 172.31.0.0/16 172.30.0.0/16"))
    assert checked.free == "172.29"
    assert ("172.31.0.0/16 behind the gateway of VLAN 200 overlaps 172.31.248.0/21, which the containers on this machine use. "
            "The next free block is 172.29.248.0/21: answer CONTAINER_PREFIX=172.29.") in checked.errors
    moved = plan(CASE_B, NET3_ROUTES="10.20.0.0/16 172.31.0.0/16 172.30.0.0/16", CONTAINER_PREFIX="172.29")
    assert json.loads(tool.guard_conf(moved))["container_block"] == "172.29.248.0/21"
    assert "REZZTOR_NET_PREFIX=172.29\n" in tool.compose_env(moved)
    assert "inside 172.31.248.0/21" in errors(CASE_B, NET1_DNS="172.31.250.9")
    assert tool.check(answers(FLAT_STATIC, NET_ADDRESS="172.31.250.9/24", NET_GATEWAY="172.31.250.1", NET_DNS=None)).free == "172.30"


def test_the_compose_variables_keep_the_web_port_on_loopback_and_carry_the_proxy():
    env = tool.read_answers(tool.compose_env(plan(CASE_B)))
    assert env == {"REZZTOR_BIND_ADDRESS": "127.0.0.1", "REZZTOR_NET_PREFIX": "172.31", "FORWARDED_ALLOW_IPS": "172.31.249.1",
                   "HTTP_PROXY": "", "HTTPS_PROXY": "", "NO_PROXY": ""}
    # The proxy the product believes is the gateway of its own network, so it moves with the block.
    assert "FORWARDED_ALLOW_IPS=10.99.249.1\n" in tool.compose_env(plan(CASE_B, CONTAINER_PREFIX="10.99"))
    proxied = plan(CASE_B, PROXY="http://10.0.30.8:3128")
    env = tool.read_answers(tool.compose_env(proxied))
    assert env["HTTPS_PROXY"] == "http://10.0.30.8:3128"
    assert env["NO_PROXY"] == "172.31.248.0/21,10.0.100.0/24,10.0.200.0/24,10.20.0.0/16"
    assert json.loads(tool.guard_conf(proxied))["infra"] == [{"address": "10.0.30.8", "protocol": "tcp", "port": 3128}]


def test_services_inside_a_devices_only_network_are_named_one_by_one():
    p = plan(CASE_B, APP_SERVICES="10.20.0.9:17774 10.0.100.20:514/udp")
    assert json.loads(tool.guard_conf(p))["app_services"] == [
        {"address": "10.20.0.9", "protocol": "tcp", "port": 17774}, {"address": "10.0.100.20", "protocol": "udp", "port": 514}]
    assert "not inside a network for devices only" in errors(CASE_B, APP_SERVICES="10.0.30.9:443")
    assert "is not a service like" in errors(CASE_B, APP_SERVICES="siem.example.com:514")


@pytest.mark.parametrize("changes, message", [
    ({"NET2_VLAN": "300"}, "VLAN 300 on eno1 is already used by VLAN 300"),          # a VLAN ID twice on one port
    ({"NET2_VLAN": None}, "tagged and untagged at once"),
    ({"NET2_IFACE": "eno2", "NET2_VLAN": "300"}, "The same VLAN ID on two ports is not in this build"),
    ({"NET2_VLAN": "4095"}, "from 1 to 4094"),
    ({"NET2_VLAN": "0"}, "from 1 to 4094"),
    ({"NET2_ADDRESS": "10.0.100.5"}, "not an address with its prefix"),
    ({"NET2_ADDRESS": "10.0.100.300/24"}, "not an address with its prefix"),
    ({"NET2_ADDRESS": "10.0.100.0/24"}, "the first or the last address"),
    ({"NET2_ADDRESS": "10.0.100.5/31"}, "from 8 to 30"),
    ({"NET2_ADDRESS": "fd00::5/64"}, "IPv6 is not in this build"),
    ({"NET3_GATEWAY": "10.0.201.1"}, "must be another address inside 10.0.200.0/24"),
    ({"NET3_GATEWAY": "10.0.200.5"}, "must be another address inside 10.0.200.0/24"),
    ({"NET3_GATEWAY": None}, "need the gateway's address"),
    ({"NET3_ROUTES": "10.20.0.9/16"}, "Did you mean 10.20.0.0/16?"),
    ({"NET3_ROUTES": "0.0.0.0/0"}, "wider than /8"),
    ({"NET2_ROLES": "devices printers"}, "one or more of: internet, users, devices"),
    ({"NET2_ROLES": "internet devices", "NET2_GATEWAY": "10.0.100.1"}, "A second internet uplink is not in this build"),
    ({"NET1_ROLES": "internet"}, "No network is for users"),
    ({"NET2_NAME": "vlan 300"}, "Two networks are called"),
    ({"NET2_NAME": "x" * 31}, "1 to 30"),
    ({"NET2_IFACE": 'eno1" } ; flush ruleset ; #'}, "is not the name of a network port"),
    ({"NET2_IFACE": "rz-app"}, "is not the name of a network port"),
    ({"NET2_MTU": "9000"}, "Jumbo frames are not in this build"),
    ({"NET2_MTU": "100"}, "from 576 to 1500"),
    ({"NET2_DNS": "10.0.100.1"}, "DNS servers belong to the network for the internet or for users"),
    ({"NET1_DNS": "1.1.1.1 2.2.2.2 3.3.3.3 4.4.4.4"}, "At most three DNS servers"),
    ({"NET1_DNS": "dns.example.com"}, "is not an IPv4 address"),
    ({"NET1_TEST": "10.0.30.9:22"}, "belongs to a network for devices"),
    ({"NET2_TEST": "10.0.200.10:22"}, "is not in 10.0.100.0/24"),
    ({"NET2_TEST": "10.0.100.10:0"}, "needs an address and a port"),
    ({"NET2_SSH": "maybe"}, "Type yes or no"),
    ({"NET2_MODE": "auto"}, "dhcp or static"),
    ({"NET1_MODE": "dhcp"}, "comes from DHCP, so no address or gateway can be typed"),
    ({"NET2_ADRESS": "10.0.100.5/24"}, "NET2_ADRESS is not an answer this tool knows"),
    ({"NET_ADDRESS": "10.0.100.5/24"}, "belongs to a file with one network"),
    ({"CONTAINER_PREFIX": "172.31.248.0/21"}, "first two numbers of an address"),
    ({"PROXY": "10.0.30.8:3128"}, "must look like http://10.0.30.8:3128"),
    ({"NET2_MAC": "not-a-mac"}, "is not a MAC address"),
    ({"NET3_ROUTES": "10.20.0.0/16@10.0.201.9"}, "the gateway 10.0.201.9 of the route to 10.20.0.0/16 must be another address inside 10.0.200.0/24"),
    ({"NET3_ROUTES": "10.20.0.0/16@10.0.200.5"}, "must be another address inside 10.0.200.0/24"),
    ({"NET3_ROUTES": "10.20.0.0/16@gateway"}, "The gateway 'gateway' is not an IPv4 address"),
    ({"NET2_ROUTES": "10.21.0.0/16"}, "need the gateway's address"),
    ({"NET3_ROUTES": "10.20.0.0/16@"}, "The gateway '' is not an IPv4 address"),
])
def test_what_a_person_types_is_checked_before_anything_is_written(changes, message):
    assert message in errors(CASE_B, **changes)


def test_a_dotted_mask_is_accepted_and_an_answers_file_is_never_run(tmp_path):
    assert str(plan(CASE_B, NET2_ADDRESS="10.0.100.5/255.255.255.0")["nets"][1]["address"]) == "10.0.100.5/24"
    marker = tmp_path / "ran"
    a = tool.read_answers(f"NET_IFACE=$(touch {marker})\nNET_MODE=dhcp\ntouch {marker}\nexport X=1\n lower=1\n")
    assert a == {"NET_IFACE": f"$(touch {marker})", "NET_MODE": "dhcp"}
    assert "is not the name of a network port" in "\n".join(tool.check(a).errors) and not marker.exists()
    assert tool.split(tool.join(*tool.split(answers(CASE_A)))) == tool.split(answers(CASE_A))


def test_what_is_not_in_this_build_is_said_where_a_person_looks(capsys):
    for words in ("bonds", "second internet uplink", "same subnet in two VLANs", "jump hosts", "traps", "jumbo frames", "IPv6"):
        assert words in tool.NOT_HERE
    with pytest.raises(SystemExit):
        tool.main(["--help"])
    assert "Not in this build: bonds" in capsys.readouterr().out


# --- the netplan files, read by netplan itself ---------------------------------

NETPLAN_CASES = {
    "case-a": CASE_A, "case-b": CASE_B, "flat-dhcp": FLAT_DHCP, "flat-static": FLAT_STATIC,
    "two-cards": "NET1_NAME=Interface 1\nNET1_ROLES=internet users devices\nNET1_IFACE=ens192\nNET1_MODE=dhcp\n"
                 "NET1_ROUTES=10.60.0.0/16@192.168.10.254\nNET2_NAME=Interface 2\nNET2_ROLES=devices\nNET2_IFACE=ens224\n"
                 "NET2_ADDRESS=10.20.30.10/24\nNET2_GATEWAY=10.20.30.1\nNET2_ROUTES=172.16.0.0/24 10.100.100.0/24\n",
    "flat-route": FLAT_STATIC + "NET_ROUTES=10.50.0.0/16@192.168.10.254\n",
    "flat-trunk": FLAT_STATIC + "NET_VLAN=300\n",
    "dhcp-uplink": "NET1_NAME=Office\nNET1_ROLES=internet users\nNET1_IFACE=ens192\nNET1_MODE=dhcp\n"
                   "NET2_NAME=Lab\nNET2_ROLES=users\nNET2_IFACE=ens224\nNET2_MODE=dhcp\nNET2_MTU=1400\n",
}


def _netplan_image():
    """(image, None), or (None, why not). Built once from debian:13; nothing is installed on this machine."""
    if not shutil.which("docker"):
        return None, "docker is not installed, so the generated netplan files were not read by netplan itself"
    try:
        if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=30).returncode == 0:
            return IMAGE, None
        recipe = ("FROM debian:13\nRUN apt-get update && apt-get install -y --no-install-recommends netplan.io "
                  "&& rm -rf /var/lib/apt/lists/*\n")
        built = subprocess.run(["docker", "build", "-q", "-t", IMAGE, "-"], input=recipe, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"docker did not answer, so the generated netplan files were not read by netplan itself: {error}"
    if built.returncode:
        return None, ("the debian:13 image with netplan.io could not be pulled or built here, so the generated netplan "
                      f"files were not read by netplan itself: {built.stderr.strip()[-200:]}")
    return IMAGE, None


def test_netplan_itself_accepts_every_generated_file(tmp_path):
    """`netplan generate` of Debian 13 (netplan.io 1.1.2) in a throwaway container
    with no network: it reads our file and writes the networkd files it stands for."""
    image, why_not = _netplan_image()
    if image is None:
        pytest.skip(why_not)
    for name, text in NETPLAN_CASES.items():
        target = tmp_path / name / "etc" / "netplan"
        target.mkdir(parents=True)
        (target / "90-rezztor.yaml").write_text(tool.netplan(plan(text)))
        (target / "90-rezztor.yaml").chmod(0o600)
    script = 'for d in /work/*/; do netplan generate --root-dir "$d" || exit 1; done'
    done = subprocess.run(["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                           "-v", f"{tmp_path}:/work", image, "sh", "-c", script], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr

    def made(name):
        files = sorted((tmp_path / name / "run" / "systemd" / "network").iterdir())
        return {path.name: path.read_text() for path in files}

    b = made("case-b")
    assert sorted(b) == ["10-netplan-eno1.network", "10-netplan-vlan100.netdev", "10-netplan-vlan100.network",
                         "10-netplan-vlan200.netdev", "10-netplan-vlan200.network", "10-netplan-vlan300.netdev",
                         "10-netplan-vlan300.network"]
    assert "Kind=vlan" in b["10-netplan-vlan100.netdev"] and "Id=100" in b["10-netplan-vlan100.netdev"]
    assert {"VLAN=vlan100", "VLAN=vlan200", "VLAN=vlan300"} <= set(b["10-netplan-eno1.network"].split())
    assert "Address=" not in b["10-netplan-eno1.network"], "the trunk port got an address"
    assert "Address=10.0.100.5/24" in b["10-netplan-vlan100.network"] and "Gateway" not in b["10-netplan-vlan100.network"]
    assert "Destination=10.20.0.0/16" in b["10-netplan-vlan200.network"] and "Gateway=10.0.200.1" in b["10-netplan-vlan200.network"]
    gateways = [line for text in b.values() for line in text.splitlines() if line.startswith("Gateway=")]
    assert sorted(gateways) == ["Gateway=10.0.200.1", "Gateway=10.0.30.1"]
    assert "Destination=" not in b["10-netplan-vlan300.network"].replace("Destination=0.0.0.0/0", ""), "one default route, on VLAN 300"
    for text in b.values():
        assert "LinkLocalAddressing=ipv4" not in text and "IPv6AcceptRA=yes" not in text

    a = made("case-a")
    assert "MACAddress=00:50:56:aa:bb:02" in a["10-netplan-ens224.network"] or "MACAddress=00:50:56:aa:bb:02" in a["10-netplan-ens224.link"]
    assert not [name for name in a if name.endswith(".netdev")], "a virtual card per port group needs no VLAN interface"
    assert "DHCP=ipv4" in made("flat-dhcp")["10-netplan-ens192.network"]
    lab = made("dhcp-uplink")["10-netplan-ens224.network"]
    assert "UseRoutes=false" in lab and "UseDNS=false" in lab and "MTUBytes=1400" in lab
    assert "UseRoutes=false" not in made("dhcp-uplink")["10-netplan-ens192.network"]
    # The owner's scenario of 2026-10-10: a second card whose gateway carries two routes and no default route.
    two = made("two-cards")
    second = two["10-netplan-ens224.network"]
    assert second.count("[Route]") == 2 and "Destination=172.16.0.0/24" in second and "Destination=10.100.100.0/24" in second
    assert second.count("Gateway=10.20.30.1") == 2 and "0.0.0.0/0" not in second and "DHCP=" not in second
    first = two["10-netplan-ens192.network"]
    assert "DHCP=ipv4" in first and "Destination=10.60.0.0/16" in first and "Gateway=192.168.10.254" in first
    flat = made("flat-route")["10-netplan-ens192.network"]
    assert "Destination=10.50.0.0/16" in flat and "Gateway=192.168.10.254" in flat and "Gateway=192.168.10.1" in flat
    assert "Address=192.168.10.20/24" in made("flat-trunk")["10-netplan-vlan300.network"]


# --- keep or go back, with a fake clock and a fake machine ---------------------


class Clock:
    def __init__(self):
        self.now = 1759700000.0

    def __call__(self):
        return self.now


class FakeHost:
    """The machine, as far as the tool can tell. It "applies" a netplan file by
    reading the addresses out of it, and records every call in order."""

    def __init__(self, *ports, leases=None):
        self.ports = [{"name": name, "mac": f"00:50:56:aa:bb:{n:02x}", "link": True} for n, name in enumerate(ports, 1)]
        self.leases = leases or {}     # interface -> the address DHCP gives it
        self.taken = {}                # address -> the MAC address that already has it
        self.silent = set()            # addresses that answer nothing
        self.refusing = set()          # addresses that answer but refuse the port
        self.fail = {}                 # step -> what it reports once, or an exception it raises once
        self.calls, self.addresses, self.links, self.timer, self.session = [], {}, set(), None, None
        self.env = None                # the stack's .env variables, once the product is installed

    def _step(self, name):
        self.calls.append(name)
        failure = self.fail.pop(name, None)  # once: going back must be able to run the step again
        if isinstance(failure, BaseException):
            raise failure
        return failure

    def interfaces(self, wake=False):
        return [dict(port) for port in self.ports]

    def link(self, dev):
        return True

    def address(self, dev):
        return self.addresses.get(dev)

    def gateway(self, dev):
        return ipaddress.IPv4Address(str(next(self.leases[dev].network.hosts()))) if dev in self.leases and dev in self.addresses else None

    def lease(self, dev, seconds=30):
        return self.addresses.get(dev)

    def route(self, address):
        """The longest match in the netplan file that is applied, as the kernel would find it."""
        text = tool._read(tool.NETPLAN)
        net = yaml(text) if text else {"ethernets": {}}
        found = []
        for dev, body in {**net["ethernets"], **net.get("vlans", {})}.items():
            mine = self.addresses.get(dev)
            if mine is None:
                continue
            ways = [(mine.network, None), *[(ipaddress.IPv4Network("0.0.0.0/0" if r["to"] == "default" else r["to"]), r["via"])
                                            for r in body.get("routes", [])]]
            if body.get("dhcp4") and "dhcp4-overrides" not in body:
                ways.append((ipaddress.IPv4Network("0.0.0.0/0"), str(self.gateway(dev))))
            found += [(subnet.prefixlen, dev, via, str(mine.ip)) for subnet, via in ways if ipaddress.IPv4Address(str(address)) in subnet]
        return max(found)[1:] if found else None

    def ensure_link(self, net):
        self.links.add(net["dev"])

    def duplicate(self, dev, address):
        self.calls.append(f"probe {address}")
        return self.taken.get(str(address))

    def arp(self, dev, address):
        return str(address) not in self.silent

    def ping(self, address):
        return str(address) not in self.silent

    def tcp(self, address, port):
        return "none" if str(address) in self.silent else "refused" if str(address) in self.refusing else "ok"

    def dns(self, server):
        return str(server) not in self.silent

    ntp = dns

    def netplan(self):
        problem = self._step("netplan")
        if problem:
            return problem
        text = tool._read(tool.NETPLAN)
        net = yaml(text) if text else {"ethernets": {}}
        self.addresses = {}
        for dev, body in {**net["ethernets"], **net.get("vlans", {})}.items():
            if body.get("addresses"):
                self.addresses[dev] = ipaddress.IPv4Interface(body["addresses"][0])
            elif body.get("dhcp4") and dev in self.leases:
                self.addresses[dev] = self.leases[dev]
        self.links = {dev for dev in self.links if dev in self.addresses} | set(net.get("vlans", {}))
        return None

    def drop_links(self, devs):
        self.calls += [f"drop {dev}" for dev in devs]

    def guard(self):
        return self._step("guard")

    def compose(self, text):
        wanted = tool.read_answers(text)
        if self.env is None or self.env == wanted:
            return None
        self.env = wanted
        return self._step("compose up")

    def arm(self, seconds):
        self.calls.append(f"arm {seconds}")
        self.timer = seconds

    def disarm(self):
        self.timer = None

    def session_from(self):
        return self.session


class Script:
    """The console, typed from a list. It keeps what was printed and what was typed, as a person would see it."""

    def __init__(self, *lines):
        self.lines, self.out = list(lines), []

    def say(self, text=""):
        self.out.append(text)

    def ask(self, prompt, default=""):
        assert self.lines, f"the script ended at: {prompt}"
        typed = self.lines.pop(0)
        self.out.append(((f"{prompt} [{default}]: " if default else f"{prompt}: ") + typed).rstrip())
        return typed or default

    def keep(self, deadline, clock):
        assert self.lines, "the script ended at: Keep these settings?"
        typed = self.lines.pop(0)
        self.out.append(f"Keep these settings?  Type keep or back, then Enter.   Going back by itself in {int(deadline - clock())} s")
        self.out.append(typed)
        return typed == "keep"

    @property
    def text(self):
        return "\n".join(self.out)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "ROOT", str(tmp_path))
    return tmp_path


def files(root):
    """Every file the tool manages, as it is on disk now."""
    return {path: tool._read(path) for path in tool.MANAGED}


TWO = "".join(line + "\n" for line in CASE_B.splitlines() if not line.startswith("NET3_"))


def test_keep_makes_the_change_the_settings_to_go_back_to(machine):
    host, io, clock = FakeHost("eno1"), Script("keep"), Clock()
    assert tool.change(answers(CASE_B), host, io, clock) is True
    assert host.calls[0] == "arm 300", "the timer runs before anything is touched"
    assert host.calls.index("arm 300") < host.calls.index("probe 10.0.30.5") < host.calls.index("netplan") < host.calls.index("guard")
    assert host.calls[-1] == "arm 120", "from the question on, the person has the whole 120 seconds"
    assert host.timer is None and tool._read(tool.PENDING) is None
    assert tool.read_answers(tool._read(tool.GOOD)) == answers(CASE_B)
    assert yaml(tool._read(tool.NETPLAN))["vlans"]["vlan100"]["addresses"] == ["10.0.100.5/24"]
    assert json.loads(tool._read(tool.LIST))["networks"][1]["interface"] == "vlan100"
    assert json.loads(tool._read(tool.GUARD))["ssh_if"] == ["vlan300"]
    assert oct((machine / tool.NETPLAN.lstrip("/")).stat().st_mode & 0o777) == "0o600"
    assert oct((machine / tool.LIST.lstrip("/")).stat().st_mode & 0o777) == "0o644", "the executor (uid 1000) reads the list"
    assert "Going back by itself in 120 s" in io.text and io.out[-1] == "Kept."


def test_no_answer_goes_back_to_the_settings_from_before(machine):
    host, clock = FakeHost("eno1"), Clock()
    assert tool.change(answers(TWO), host, Script("keep"), clock)
    before = files(machine)
    io = Script("")  # nobody answered: the console's question ran out of time
    assert tool.change(answers(CASE_B), host, io, clock) is False
    assert files(machine) == before and "vlan200" not in host.addresses, "the third network is still there"
    assert "drop vlan200" in host.calls, "netplan does not remove a VLAN interface that left the file"
    assert host.timer is None and tool._read(tool.PENDING) is None
    assert tool.read_answers(tool._read(tool.GOOD)) == answers(TWO)
    assert io.out[-1] == "Went back: the settings from before are in force again."


def test_a_lost_session_is_undone_by_the_timer_and_a_late_keep_is_refused(machine):
    """The session dies while the question is on the screen. Nothing in it runs
    any more; the timer's own `revert`, 120 seconds later, puts everything back."""
    host, clock = FakeHost("eno1"), Clock()
    assert tool.change(answers(TWO), host, Script("keep"), clock)
    before = files(machine)

    class Gone(BaseException):
        pass

    class Dropped(Script):
        def keep(self, deadline, clock):
            raise Gone  # the SSH session or the console is gone

    with pytest.raises(Gone):
        tool.change(answers(CASE_B), host, Dropped(), clock)
    assert files(machine) != before and host.timer == 120, "the change is applied, unconfirmed, and the timer is running"
    clock.now += 121
    assert tool.keep(host, clock) is False, "a keep after the time is over must not win against the timer"
    assert tool.main(["revert"], host=host, io=Script()) == 0
    assert files(machine) == before and "vlan200" not in host.addresses
    assert tool.revert(host) is False, "going back twice changes nothing"


def test_a_restart_before_anyone_kept_goes_back_too():
    """systemd forgets a transient timer at a restart, so the boot runs `revert` itself."""
    unit = (ROOT / "deploy" / "iso" / "host" / "rezztor-firewall.service").read_text()
    assert "ExecStartPre=-/usr/local/sbin/rezztor-network revert" in unit and "Before=network-pre.target" in unit


@pytest.mark.parametrize("step, failure, said", [
    ("netplan", RuntimeError("the machine fell over"), "The change stopped half way (the machine fell over)."),
    ("netplan", "Error in network definition", "The network settings were refused: Error in network definition"),
    ("guard", "nft refused the rules", "The firewall refused these networks, so they cannot be kept: nft refused the rules"),
    ("guard", OSError("no nft"), "The change stopped half way (no nft)."),
])
def test_a_crash_in_the_middle_or_a_failed_guard_check_goes_back_and_never_asks(machine, step, failure, said):
    host, clock = FakeHost("eno1"), Clock()
    assert tool.change(answers(TWO), host, Script("keep"), clock)
    before = files(machine)
    host.fail[step] = failure
    calls = len(host.calls)
    io = Script()  # an empty script: "Keep these settings?" would fail the test
    assert tool.change(answers(CASE_B), host, io, clock) is False
    assert files(machine) == before and "vlan200" not in host.addresses
    assert io.out == [said, "Nothing was changed: the settings from before are in force."]
    assert host.calls[calls:].count("netplan") == 2 and host.calls[-1] == "guard", "going back applies the old network and the old guard again"
    assert host.timer is None and tool._read(tool.PENDING) is None


def test_an_address_somebody_else_has_is_refused_before_it_is_set(machine):
    host, io = FakeHost("eno1"), Script()
    host.taken["10.0.100.5"] = "00:11:22:33:44:55"
    assert tool.change(answers(CASE_B), host, io, Clock()) is False
    assert "VLAN 100: the address 10.0.100.5 is already used by another machine (00:11:22:33:44:55)" in io.out[0]
    assert host.calls.count("netplan") == 1 and host.calls.index("probe 10.0.100.5") < host.calls.index("netplan"), \
        "the only netplan run is the one that goes back"
    assert all(text is None for text in files(machine).values()) and not host.addresses


def test_no_lease_and_a_missing_port_go_back(machine):
    io = Script()
    assert tool.change(answers(FLAT_DHCP), FakeHost("ens192"), io, Clock()) is False
    assert io.out[0].startswith("no address came from DHCP on ens192 within 30 seconds.")
    io = Script()
    assert tool.change(answers(CASE_A), FakeHost("ens192", "ens224"), io, Clock()) is False
    assert io.out[0] == "VLAN 200: network port ens256 does not exist on this machine."
    assert all(text is None for text in files(machine).values())


def test_a_lease_inside_the_container_block_is_seen_after_the_lease_and_goes_back(machine):
    host, io = FakeHost("ens192", leases={"ens192": ipaddress.IPv4Interface("172.31.250.9/24")}), Script()
    assert tool.change(answers(FLAT_DHCP), host, io, Clock(), auto=True) is False
    assert "overlaps 172.31.248.0/21" in io.out[0] and "CONTAINER_PREFIX=172.30" in io.out[0]
    assert "guard" not in host.calls[:host.calls.index("netplan") + 1]


def test_adding_a_device_network_restarts_no_container_and_moving_the_block_does(machine):
    host, clock = FakeHost("eno1"), Clock()
    assert tool.change(answers(TWO), host, Script("keep"), clock)
    host.env = tool.read_answers(tool._read(tool.COMPOSE))  # the product is installed with these
    assert tool.change(answers(CASE_B), host, Script("keep"), clock)
    assert "compose up" not in host.calls, "adding a device network must not touch the containers"
    assert not [call for call in host.calls if call.startswith("drop")], "and must not remove an interface"
    assert "probe 10.0.30.5" in host.calls and host.calls.count("probe 10.0.30.5") == 1, "an address that is ours already is not probed again"
    assert tool.change(answers(CASE_B, CONTAINER_PREFIX="10.99"), host, Script("keep"), clock)
    assert host.calls.count("compose up") == 1 and host.env["REZZTOR_NET_PREFIX"] == "10.99"


def test_the_stack_env_is_merged_line_by_line_and_never_replaced(machine, monkeypatch):
    """.env holds the session key and the database passwords. A new file would lock the product out of its own data."""
    env = machine / tool.STACK.lstrip("/") / ".env"
    env.parent.mkdir(parents=True)
    env.write_text("FLASK_SECRET_KEY=keep-me\nREZZTOR_NET_PREFIX=172.31\n# a comment\nREZZTOR_BIND_ADDRESS=127.0.0.1\n"
                   "FORWARDED_ALLOW_IPS=172.31.249.1\n")
    ran = []
    monkeypatch.setattr(tool.subprocess, "run", lambda argv, **kw: ran.append((argv, kw["cwd"])) or subprocess.CompletedProcess(argv, 0, "", ""))
    host = tool.Host()
    assert host.compose(tool.compose_env(plan(CASE_B))) is None and not ran, "nothing changed, so Compose is not called"
    assert host.compose(tool.compose_env(plan(CASE_B, CONTAINER_PREFIX="10.99", PROXY="http://10.0.30.8:3128"))) is None
    assert env.read_text().splitlines()[:4] == ["FLASK_SECRET_KEY=keep-me", "REZZTOR_NET_PREFIX=10.99", "# a comment",
                                                "REZZTOR_BIND_ADDRESS=127.0.0.1"]
    assert "HTTPS_PROXY=http://10.0.30.8:3128" in env.read_text() and "FORWARDED_ALLOW_IPS=10.99.249.1" in env.read_text()
    assert ran == [(["docker", "compose", "up", "-d"], str(machine) + tool.STACK)]
    (machine / tool.STACK.lstrip("/") / ".env").unlink()
    assert host.compose("REZZTOR_NET_PREFIX=10.98\n") is None and not env.exists(), "before the install there is no .env to write"


def test_an_answers_file_is_applied_in_steps_and_a_bad_third_network_leaves_the_first_two(machine, tmp_path):
    """Unattended, as from the answers disk: the same path, one device network at a time."""
    host = FakeHost("eno1")
    host.taken["10.0.200.5"] = "00:11:22:33:44:55"
    path = tmp_path / "answers.env"
    path.write_text("HOSTNAME=eagent\nNTP=10.0.30.1\n" + CASE_B + "APP_SERVICES=10.20.0.9:17774\n")
    io = Script()
    assert tool.main(["apply", str(path), "--yes"], host=host, io=io) == 1
    assert io.out.count("Kept.") == 2 and "VLAN 200: the address 10.0.200.5 is already used" in io.text
    assert [n["name"] for n in json.loads(tool._read(tool.LIST))["networks"]] == ["VLAN 300", "VLAN 100"]
    assert set(host.addresses) == {"vlan300", "vlan100"}
    host.taken.clear()
    io = Script()
    assert tool.main(["apply", str(path), "--yes"], host=host, io=io) == 0
    assert io.out.count("Kept.") == 1, "the two networks already in force are not applied again one by one"
    wanted = answers(CASE_B, APP_SERVICES="10.20.0.9:17774", NTP="10.0.30.1")
    assert tool.read_answers(tool._read(tool.ANSWERS)) == wanted, "HOSTNAME is not this tool's answer"
    assert json.loads(tool._read(tool.GUARD))["app_services"] == [{"address": "10.20.0.9", "protocol": "tcp", "port": 17774}]
    assert [len(tool.split(step)[1]) for step in tool.stages(answers(CASE_B), {})] == [1, 2, 3]
    assert tool.stages(answers(FLAT_DHCP), {}) == [answers(FLAT_DHCP)]
    # A file with a mistake changes nothing at all, and `check` says the same without applying.
    path.write_text(CASE_B.replace("NET3_VLAN=200", "NET3_VLAN=100"))
    before, io = files(machine), Script()
    assert tool.main(["apply", str(path), "--yes"], host=host, io=io) == 1 and files(machine) == before
    assert io.out == ["VLAN 200: VLAN 100 on eno1 is already used by VLAN 100."]
    assert tool.main(["check", str(path)], host=host, io=Script()) == 1


def test_warnings_do_not_block_and_say_the_likely_reason(machine):
    host, io = FakeHost("eno1"), Script("keep")
    host.silent |= {"10.0.30.1", "10.0.100.10"}
    host.refusing.add("10.20.0.9")
    assert tool.change(answers(CASE_B, NTP="10.0.30.1"), host, io, Clock()) is True
    for said in (
        "Warning: VLAN 300: the gateway 10.0.30.1 does not answer. Ask the switch administrator to allow VLAN 300 on this port.",
        "Warning: VLAN 100: nothing answers at 10.0.100.10. Check that the device is up and that VLAN 100 is allowed on this machine's switch port.",
        "Warning: VLAN 200: 10.20.0.9 answers but refuses port 443. Is SSH enabled, or is this device managed over HTTPS?",
        "Warning: The DNS server 10.0.30.1 does not answer.",
        "Warning: The NTP server 10.0.30.1 does not answer. A wrong clock breaks sign-in and the licence check.",
    ):
        assert said in io.text
    assert "up, gateway does not answer" in io.text and "up, test device does not answer" in io.text


# --- the machine half, with real commands, in a private network namespace -------

MACHINE_PROOF = r"""
import importlib.machinery, importlib.util, ipaddress, subprocess, sys, time
sys.dont_write_bytecode = True
loader = importlib.machinery.SourceFileLoader("n", sys.argv[1])
n = importlib.util.module_from_spec(importlib.util.spec_from_loader("n", loader)); loader.exec_module(n)
sh = lambda line: subprocess.run(line, shell=True, check=True)
# A "switch port" on a trunk: the far end carries VLAN 100 with one device in it, which listens on port 22.
far = subprocess.Popen(["unshare", "-n", "sleep", "600"]); time.sleep(0.3)
sh(f"ip link add trunk0 type veth peer name sw0 netns {far.pid}")
inside = f"nsenter -t {far.pid} -n"
sh(f"{inside} ip link set sw0 up && {inside} ip link add link sw0 name sw0.100 type vlan id 100 && {inside} ip link set sw0.100 up")
sh(f"{inside} ip addr add 10.0.100.10/24 dev sw0.100 && {inside} ip link set lo up")
listener = subprocess.Popen(f"{inside} socat TCP-LISTEN:22,reuseaddr,fork SYSTEM:true", shell=True); time.sleep(0.5)
host, net = n.Host(), {"iface": "trunk0", "vlan": 100, "dev": "vlan100"}
try:
    host.ensure_link(net); host.ensure_link(net)
    print("link", host.link("vlan100"), host.address("vlan100"))
    print("taken", host.duplicate("vlan100", ipaddress.IPv4Address("10.0.100.10")))
    print("free", host.duplicate("vlan100", ipaddress.IPv4Address("10.0.100.5")))
    sh("ip addr add 10.0.100.5/24 dev vlan100")
    print("address", host.address("vlan100"), host.gateway("vlan100"))
    print("arp", host.arp("vlan100", "10.0.100.10"), host.arp("vlan100", "10.0.100.99"))
    print("ping", host.ping("10.0.100.10"), host.ping("10.0.100.99"))
    print("tcp", host.tcp("10.0.100.10", 22), host.tcp("10.0.100.10", 443), host.tcp("10.0.100.99", 22))
    host.drop_links(["vlan100"])
    print("dropped", host.address("vlan100"), host.link("vlan100"))
finally:
    listener.kill(); far.kill()
"""


def test_the_real_commands_do_what_the_tool_thinks_in_a_private_network(tmp_path):
    """The VLAN interface, the duplicate address probe (`arping -D`: 0 is free, 1
    is taken), ARP, ping and the TCP connect, with the real `ip` and `arping` of
    this machine. Nothing real is touched: `unshare -Urn` gives the test its own
    network stack. netplan, systemd and Docker are not in it."""
    needed = [name for name in ("unshare", "nsenter", "ip", "arping", "ping", "socat") if not shutil.which(name)]
    if needed:
        pytest.skip(f"the private network proof of the tool's commands needs {', '.join(needed)} on this machine")
    probe = subprocess.run(["unshare", "-Urn", "ip", "link", "add", "probe0", "type", "dummy"], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip(f"this machine does not let an ordinary user make a private network namespace: {probe.stderr.strip()}")
    script = tmp_path / "proof.py"
    script.write_text(MACHINE_PROOF)
    # Its own /sys too: the one of this machine shows this machine's interfaces, not the private ones.
    done = subprocess.run(["unshare", "-Urnm", "sh", "-c", 'mount -t sysfs none /sys && exec "$@"', "sh", sys.executable, str(script),
                           str(ROOT / "deploy" / "iso" / "host" / "rezztor-network")], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    said = dict(line.split(" ", 1) for line in done.stdout.splitlines())
    assert said["link"] == "True None", "the VLAN interface is up and has no address before the probe"
    assert re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", said["taken"]), f"the probe did not name who has the address: {said['taken']}"
    assert said["free"] == "None"
    assert said["address"] == "10.0.100.5/24 None"
    assert said["arp"] == "True False" and said["ping"] == "True False"
    assert said["tcp"] == "ok refused none"
    assert said["dropped"] == "None False"


# --- the questions, typed from a script ----------------------------------------

LEASE = ipaddress.IPv4Interface("192.168.10.57/24")
# What a person types at first boot. "" is Enter: the answer in brackets.
TYPED_FLAT = ["", "keep"]                                                  # DHCP, keep
# Case A: three virtual cards. The first boot sets up the one the machine is reached on.
TYPED_A = ["1", "n", "10.0.30.5/24", "10.0.30.1", "", "keep"]
MENU_A = [
    "2", "", "", "10.0.100.5/24", "", "keep",                              # add: port 2, no web, address, no gateway
    "2", "", "", "10.0.200.5/24", "10.0.200.1", "10.20.0.0/16", "keep",    # add: port 3, gateway, the network behind it
    "1", "0",
]
# Case B: one port on a trunk. The first try, untagged, finds no DHCP server; then the VLAN ID is asked for.
TYPED_B = ["", "300", "", "10.0.30.5/24", "10.0.30.1", "", "keep"]
MENU_B = [
    "2", "", "100", "", "10.0.100.5/24", "", "keep",
    "2", "", "200", "", "10.0.200.5/24", "10.0.200.1", "10.20.0.0/16", "keep",
    "1", "0",
]
# The owner's scenario of 2026-10-10: DHCP on the first card, a second card with a gateway and two networks behind it.
TWO_CARDS = """\
NET1_NAME=Interface 1
NET1_ROLES=internet users devices
NET1_IFACE=ens192
NET1_MAC=00:50:56:aa:bb:01
NET1_MODE=dhcp
NET1_SSH=yes
NET2_NAME=Interface 2
NET2_ROLES=devices
NET2_IFACE=ens224
NET2_MAC=00:50:56:aa:bb:02
NET2_MODE=static
NET2_ADDRESS=10.20.30.10/24
NET2_GATEWAY=10.20.30.1
NET2_ROUTES=172.16.0.0/24 10.100.100.0/24
NET2_SSH=no
"""
ADD_TWO = ["2", "", "", "10.20.30.10/24", "10.20.30.1", "172.16.0.0/24 10.100.100.0/24", "keep"]
RESULT_B = """\
Interface  Used for                Port     Address        Gateway     Reaches                      State
VLAN 300   Internet+Users+Devices  vlan300  10.0.30.5/24   10.0.30.1   everything else              up, gateway answers
VLAN 100   Devices                 vlan100  10.0.100.5/24  none        10.0.100.0/24                up
VLAN 200   Devices                 vlan200  10.0.200.5/24  10.0.200.1  10.0.200.0/24, 10.20.0.0/16  up, gateway answers

Web interface:  https://10.0.30.5   (answers on VLAN 300 only)
SSH:            VLAN 300 only, while SSH is switched on
On your devices, allow management from:  10.0.30.5 (VLAN 300),  10.0.100.5 (VLAN 100),  10.0.200.5 (VLAN 200)
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s"""


def first_boot(case):
    """(what the console showed, the machine, what the menu showed afterwards) for one of the guides' cases."""
    if case == "flat":
        host, typed, later = FakeHost("ens192", leases={"ens192": LEASE}), TYPED_FLAT, None
    elif case == "case-a":
        host, typed, later = FakeHost("ens192", "ens224", "ens256"), TYPED_A, MENU_A
    else:   # case B on a physical server, or a virtual machine with ONE card on a trunk port group
        host, typed, later = FakeHost("ens192" if case == "trunk-vm" else "eno1"), TYPED_B, MENU_B
    io, clock = Script(*typed), Clock()
    assert tool.wizard(io, host, clock) == 0 and not io.lines, io.text
    menu = Script(*later) if later else None
    if menu:
        assert tool.menu(menu, host, clock) == 0 and not menu.lines, menu.text
    return io, host, menu


def two_cards():
    """The machine after the first boot with DHCP on the first of two cards."""
    host, clock = FakeHost("ens192", "ens224", leases={"ens192": LEASE}), Clock()
    assert tool.wizard(Script("", "", "keep"), host, clock) == 0
    return host, clock


def test_the_first_boot_is_one_interface_and_never_says_vlan_role_or_route(machine, monkeypatch):
    io, host, _ = first_boot("flat")
    assert tool.read_answers(tool._read(tool.ANSWERS)) == {"NET_IFACE": "ens192", "NET_MAC": "00:50:56:aa:bb:01", "NET_MODE": "dhcp"}
    assert not re.search(r"vlan|role|route", io.text, re.I), io.text
    assert io.text == """\
The network: the interface you reach this machine on. More interfaces are added later, from the console menu.
Does the network give this machine its address by itself (DHCP)? [y]:
Network port:   ens192, up, gateway answers
Address:        192.168.10.57/24  (from DHCP)
Gateway:        192.168.10.1
Web interface:  https://192.168.10.57
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept."""
    assert tool._read(tool.LIST) is None and json.loads(tool._read(tool.GUARD))["form"] == "full"
    # With a second card the port is asked for, by MAC address and link; a typed address is three more answers.
    # The second card is left alone: nothing asks how many networks there are.
    monkeypatch.setattr(tool, "ROOT", str(machine / "another-machine"))
    host, io = FakeHost("ens192", "ens224"), Script("2", "n", "192.168.10.20/255.255.255.0", "192.168.10.1", "", "keep")
    assert tool.wizard(io, host, Clock()) == 0 and not io.lines
    assert "  2) ens224       00:50:56:aa:bb:02  link up" in io.text and not re.search(r"vlan|role|route|how many", io.text, re.I)
    assert host.addresses == {"ens224": ipaddress.IPv4Interface("192.168.10.20/24")}


@pytest.mark.parametrize("case, wanted", [("case-b", CASE_B), ("case-a", CASE_A)])
def test_the_console_ends_in_the_same_network_as_the_answers_file(machine, case, wanted):
    """Three networks, typed at the console: the first boot sets up the one the machine is
    reached on, the menu adds the other two, one at a time. Nobody types a role or a name,
    and what the machine gets is what an answers file with the same addresses gives it."""
    io, host, menu = first_boot(case)
    assert yaml(tool._read(tool.NETPLAN)) == yaml(tool.netplan(plan(wanted)))
    assert menu.text.count("Kept.") == 2, "each interface is applied, tested and kept before the next"
    names = ["VLAN 300", "VLAN 100", "VLAN 200"] if case == "case-b" else ["Interface 1", "Interface 2", "Interface 3"]
    listed = json.loads(tool._read(tool.LIST))["networks"]
    assert [(n["name"], n["roles"]) for n in listed] == [
        (names[0], ["internet", "users", "devices"]), (names[1], ["devices"]), (names[2], ["devices"])]
    assert json.loads(tool._read(tool.GUARD))["ssh_if"] == [listed[0]["interface"]]
    assert not re.search(r"what is this network for|name of this network|one device to test", menu.text, re.I)
    assert menu.text.count(tool.NOT_HERE) == 1, "said once, when one interface becomes several"
    if case == "case-b":
        assert RESULT_B in menu.text
        assert "  1) eno1         00:50:56:aa:bb:01  link up   used by VLAN 300, VLAN 100" in menu.text
        assert "no address came from DHCP on eno1 within 30 seconds." in io.text
        assert io.text.count("If this port is a trunk: the VLAN ID to use on it") == 1, "asked only after the first try failed"
        assert tool.read_answers(tool._read(tool.GOOD))["NET1_VLAN"] == "300"
    else:
        assert "Which port? [2]:\n" in menu.text and "Which port? [3]:\n" in menu.text, "the free port with link is the default"
        assert "VLAN" not in io.text + menu.text.replace(tool.NOT_HERE, ""), "three untagged cards are never asked about a VLAN"
    assert "Does the network give this interface its address by itself (DHCP)?" not in menu.text, \
        "DHCP is not offered for an interface that carries devices only"


def test_add_an_interface_asks_nothing_about_the_first_and_its_gateway_is_never_a_default_route(machine):
    """The owner's scenario: the first card keeps the default route it got from DHCP,
    the second card has a gateway of its own, and two networks are reached through it."""
    host, clock = two_cards()
    io = Script(*ADD_TWO, "0")
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    assert tool.read_answers(tool._read(tool.GOOD)) == answers(TWO_CARDS)
    net = yaml(tool._read(tool.NETPLAN))["ethernets"]
    assert net["ens192"] == {"match": {"macaddress": "00:50:56:aa:bb:01"}, "set-name": "ens192", "dhcp4": True, **OFF}
    assert net["ens224"]["routes"] == [{"to": "172.16.0.0/24", "via": "10.20.30.1"}, {"to": "10.100.100.0/24", "via": "10.20.30.1"}]
    assert "default" not in json.dumps(net), "the only default route is the one the first card's lease brings"
    assert "It never becomes a second default route." in io.text
    assert "This machine has one interface so far, now called Interface 1." in io.text
    assert io.text.count("Does the network give") == 0 and io.text.count("Which port?") == 1, "nothing about the first interface is asked again"
    assert [str(host.route(a)) for a in ("172.16.0.9", "10.100.100.7", "10.20.30.99", "8.8.8.8")] == [
        "('ens224', '10.20.30.1', '10.20.30.10')", "('ens224', '10.20.30.1', '10.20.30.10')",
        "('ens224', None, '10.20.30.10')", "('ens192', '192.168.10.1', '192.168.10.57')"]
    # The guard: web and SSH stay on the first interface, and the executor reaches the two networks.
    conf = guard.check_conf(json.loads(tool._read(tool.GUARD)))
    rules = guard.rules(guard.check_networks(json.loads(tool._read(tool.LIST)), conf["container_block"]), conf, ssh=True)
    assert 'set ui_if { type ifname; elements = { "ens192" } }' in rules and 'set ssh_if { type ifname; elements = { "ens192" } }' in rules
    assert 'set dev_if { type ifname; elements = { "ens224" } }' in rules
    assert "elements = { 10.20.30.0/24, 10.100.100.0/24, 172.16.0.0/24, 192.168.10.0/24 }" in rules
    # A second default route cannot be typed anywhere: not as a network behind the gateway, not in a file.
    assert "Only one interface has the default route" in errors(TWO_CARDS, NET2_ROUTES="0.0.0.0/0")
    assert "A second internet uplink is not in this build" in errors(TWO_CARDS, NET2_ROLES="internet devices")


def test_web_and_ssh_answer_on_a_second_interface_only_when_asked(machine):
    host, clock = two_cards()
    host.leases["ens224"] = ipaddress.IPv4Interface("10.20.30.44/24")
    io = Script("2", "", "y", "y", "keep", "0")       # port 2, web and SSH: yes, DHCP: yes
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    assert "May the web interface and SSH answer on this interface? [n]: y" in io.out
    assert "  It answers users in this interface's own network, and in the networks its routes name." in io.out
    good = tool.read_answers(tool._read(tool.GOOD))
    assert (good["NET2_ROLES"], good["NET2_SSH"], good["NET2_MODE"]) == ("users devices", "yes", "dhcp")
    assert json.loads(tool._read(tool.GUARD))["ssh_if"] == ["ens192", "ens224"]
    assert yaml(tool._read(tool.NETPLAN))["ethernets"]["ens224"]["dhcp4-overrides"] == {"use-routes": False, "use-dns": False}, \
        "a lease on the second card cannot bring a second default route"
    # Changed back to no: the web interface must still answer somewhere, so the first cannot say no.
    io = Script("3", "2", "", "n", "10.20.30.10/24", "", "keep",
                "3", "1", "", "n", "y", "", "keep", "0")
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    assert "  The web interface must answer on one interface, and no other one has it." in io.out
    good = tool.read_answers(tool._read(tool.GOOD))
    assert (good["NET2_ROLES"], good["NET2_SSH"], good["NET1_ROLES"]) == ("devices", "no", "internet users devices")


def test_routes_are_shown_added_and_removed_one_at_a_time(machine):
    host, clock = two_cards()
    assert tool.menu(Script(*ADD_TWO, "0"), host, clock) == 0
    io = Script("1", "2", "10.50.0.0/16", "", "keep",                   # behind the second interface's own gateway
                "1", "1", "10.60.0.0/16", "192.168.10.254", "keep",     # on the first interface, behind another gateway
                "0")
    assert tool.routes_menu(io, host, clock) == 0 and not io.lines, io.text
    assert """\
Routes
Interface    Reaches          Gateway
Interface 1  everything else  192.168.10.1
Interface 1  10.60.0.0/16     192.168.10.254
Interface 2  172.16.0.0/24    10.20.30.1
Interface 2  10.100.100.0/24  10.20.30.1
Interface 2  10.50.0.0/16     10.20.30.1
  1) Add a route
  2) Remove a route
  0) Back""" in io.text
    good = tool.read_answers(tool._read(tool.GOOD))
    assert good["NET1_ROUTES"] == "10.60.0.0/16@192.168.10.254" and good["NET2_ROUTES"] == "172.16.0.0/24 10.100.100.0/24 10.50.0.0/16"
    net = yaml(tool._read(tool.NETPLAN))["ethernets"]
    assert net["ens192"]["routes"] == [{"to": "10.60.0.0/16", "via": "192.168.10.254"}], "beside the lease's default route"
    assert host.route("10.60.1.1") == ("ens192", "192.168.10.254", "192.168.10.57")
    assert json.loads(tool._read(tool.LIST))["networks"][0]["routed_subnets"] == ["10.60.0.0/16"]
    # What is refused, and nothing changes: a network twice, the containers' block, a gateway that is not on the interface.
    before = files(machine)
    for typed, said in (
        (("2", "172.16.0.0/24", ""), "172.16.0.0/24 behind the gateway of Interface 2 overlaps 172.16.0.0/24 behind the gateway of Interface 2."),
        (("1", "10.50.9.0/24", ""), "10.50.0.0/16 behind the gateway of Interface 2 overlaps 10.50.9.0/24 behind the gateway of Interface 1."),
        (("2", "172.31.248.0/24", "", "n"), "overlaps 172.31.248.0/21, which the containers on this machine use."),   # n: do not move them
        (("2", "10.70.0.0/16", "192.168.10.1"), "Interface 2: the gateway 192.168.10.1 of the route to 10.70.0.0/16 must be another address inside 10.20.30.0/24."),
        (("2", "10.20.30.0/24", ""), "10.20.30.0/24 behind the gateway of Interface 2 overlaps the subnet 10.20.30.0/24 of Interface 2."),
    ):
        io = Script("1", *typed, "0")
        assert tool.routes_menu(io, host, clock) == 0 and not io.lines, io.text
        assert said in io.text and files(machine) == before, said
    io = Script("1", "2", "0.0.0.0/0", "10.20.0.5/16", "10.80.0.0/16 10.90.0.0/16", "10.80.0.0/16", "nowhere", "", "back", "0")
    assert tool.routes_menu(io, host, clock) == 0 and not io.lines, io.text
    for said in ("  0.0.0.0/0 is wider than /8. That would be the way to everything, not a subnet behind a gateway. "
                 "Only one interface has the default route: the one that leads to the internet.",
                 "  '10.20.0.5/16' is not a subnet like 10.20.0.0/16. Did you mean 10.20.0.0/16?",
                 "  Type one network with its prefix, like 172.16.0.0/24.", "  The gateway 'nowhere' is not an IPv4 address like 10.0.100.1."):
        assert said in io.out
    assert files(machine) == before, "and 'back' at the question puts everything back"
    # Removed one at a time; the default route is not in the list of what can be removed.
    io = Script("2", "1", "y", "keep", "2", "3", "n", "0")
    assert tool.routes_menu(io, host, clock) == 0 and not io.lines, io.text
    assert "  1) 10.60.0.0/16 through 192.168.10.254 (Interface 1)" in io.out and not any("everything else through" in line for line in io.out)
    assert "Remove the route to 10.50.0.0/16? [n]: n" in io.out
    good = tool.read_answers(tool._read(tool.GOOD))
    assert "NET1_ROUTES" not in good and good["NET2_ROUTES"] == "172.16.0.0/24 10.100.100.0/24 10.50.0.0/16"
    assert "routes" not in yaml(tool._read(tool.NETPLAN))["ethernets"]["ens192"]
    assert tool.main(["routes"], host=host, io=(io := Script("0"))) == 0 and "Interface 2  172.16.0.0/24    10.20.30.1" in io.text


def test_one_interface_can_have_a_route_behind_another_gateway_and_stays_one_flat_network(machine):
    host, clock = FakeHost("ens192"), Clock()
    assert tool.change(answers(FLAT_STATIC), host, Script("keep"), clock)
    io = Script("5", "1", "10.50.0.0/16", "", "1", "10.50.0.0/16", "192.168.10.254", "keep", "0", "1", "0")
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    assert "Everything that has no route of its own leaves through 192.168.10.1 already. Nothing was changed." in io.out
    assert "Route:          10.50.0.0/16 through 192.168.10.254" in io.text
    assert tool.read_answers(tool._read(tool.GOOD))["NET_ROUTES"] == "10.50.0.0/16@192.168.10.254"
    assert yaml(tool._read(tool.NETPLAN))["ethernets"]["ens192"]["routes"] == [
        {"to": "default", "via": "192.168.10.1"}, {"to": "10.50.0.0/16", "via": "192.168.10.254"}]
    assert tool._read(tool.LIST) is None, "a route makes no network list: the guard and the product still know nothing"
    # No default route at all is said, and so is that nothing can be removed yet.
    assert tool.change(answers(FLAT_STATIC, NET_GATEWAY=None), host, Script("keep"), clock)
    io = Script("2", "0")
    assert tool.routes_menu(io, host, clock) == 0
    assert "There is no route yet." in io.out and "There is no route to remove." in io.text
    assert "No default route: this machine reaches the networks above and its own, and nothing else." in io.out
    assert "the gateway 10.9.9.9 of the route to 10.50.0.0/16 must be another address inside 192.168.10.0/24" in errors(
        FLAT_STATIC, NET_ROUTES="10.50.0.0/16@10.9.9.9")
    assert "need the gateway's address" in errors(FLAT_STATIC, NET_GATEWAY=None, NET_ROUTES="10.50.0.0/16")


def test_a_route_that_takes_the_way_back_to_your_own_session_asks_first(machine, monkeypatch):
    host, clock = two_cards()
    assert tool.menu(Script(*ADD_TWO, "0"), host, clock) == 0
    before = files(machine)
    host.session = "10.9.8.7"       # reached through the default route of the first interface
    io = Script("1", "2", "10.9.0.0/16", "", "n", "0")
    assert tool.routes_menu(io, host, clock) == 0 and not io.lines, io.text
    assert ("You are connected from 10.9.8.7. With this change, answers to you take another way (Interface 2, gateway 10.20.30.1), "
            "and this session may be cut off.") in io.out
    assert "Add it anyway? [n]: n" in io.out and files(machine) == before
    # Said yes, and then nobody can answer "keep": the timer puts the route away again.
    io = Script("1", "2", "10.9.0.0/16", "", "y", "", "0")
    assert tool.routes_menu(io, host, clock) == 0 and files(machine) == before
    assert "Went back: the settings from before are in force again." in io.out
    # Another gateway of the same interface is another way back too, also on a machine with one interface.
    io = Script("1", "1", "10.9.0.0/16", "192.168.10.254", "n", "0")
    assert tool.routes_menu(io, host, clock) == 0 and not io.lines, io.text
    assert "answers to you take another way (Interface 1, gateway 192.168.10.254)" in io.text and files(machine) == before
    # Naming the session's network as a device network behind the default route changes no way back, and a
    # route that does not touch the session asks nothing extra either.
    io = Script("1", "1", "10.9.0.0/16", "", "keep", "1", "2", "10.50.0.0/16", "", "keep", "0")
    assert tool.routes_menu(io, host, clock) == 0 and not io.lines and "anyway" not in io.text
    assert tool.read_answers(tool._read(tool.GOOD))["NET1_ROUTES"] == "10.9.0.0/16"
    monkeypatch.setattr(tool, "ROOT", str(machine / "one-interface"))
    host = FakeHost("ens192")
    assert tool.change(answers(FLAT_STATIC), host, Script("keep"), clock)
    host.session = "10.9.8.7"
    io = Script("1", "10.9.0.0/16", "192.168.10.254", "n", "0")
    assert tool.routes_menu(io, host, clock) == 0 and not io.lines, io.text
    assert "With this change, answers to you take another way (gateway 192.168.10.254), and this session may be cut off." in io.text
    assert "NET_ROUTES" not in tool.read_answers(tool._read(tool.GOOD))


def test_test_says_which_interface_and_gateway_an_address_leaves_by(machine, monkeypatch):
    host, clock = two_cards()
    assert tool.menu(Script(*ADD_TWO, "0"), host, clock) == 0
    host.silent.add("10.100.100.7")
    io = Script("6", "172.16.0.9", "6", "10.100.100.7", "6", "10.20.30.99", "6", "8.8.8.8", "6", "", "0")
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    for said in ("172.16.0.9 leaves through ens224 (Interface 2), gateway 10.20.30.1, from 10.20.30.10.", "It answers ping.",
                 "10.100.100.7 leaves through ens224 (Interface 2), gateway 10.20.30.1, from 10.20.30.10.",
                 "It does not answer ping. A device may drop ping and still be reachable.",
                 "10.20.30.99 leaves through ens224 (Interface 2), with no gateway: it is in that interface's own network, from 10.20.30.10.",
                 "8.8.8.8 leaves through ens192 (Interface 1), gateway 192.168.10.1, from 192.168.10.57."):
        assert said in io.out
    assert tool.main(["test", "172.16.0.9"], host=host, io=(io := Script())) == 0 and "leaves through ens224 (Interface 2)" in io.text
    assert tool.main(["test", "not-an-address"], host=host, io=(io := Script())) == 1
    # The real machine is asked with `ip route get`; this is what it prints there.
    said = "172.16.0.9 via 10.20.30.1 dev ens224 src 10.20.30.10 uid 0 \\    cache "
    monkeypatch.setattr(tool, "_run", lambda *argv, **kw: subprocess.CompletedProcess(argv, 0, said, ""))
    assert tool.Host().route("172.16.0.9") == ("ens224", "10.20.30.1", "10.20.30.10")
    monkeypatch.setattr(tool, "_run", lambda *argv, **kw: subprocess.CompletedProcess(argv, 2, "", "RTNETLINK answers: Network is unreachable"))
    assert tool.Host().route("172.16.0.9") is None


def test_a_wrong_answer_is_asked_again_and_a_refused_interface_leaves_the_others(machine):
    host = FakeHost("eno1")
    host.taken["10.0.100.5"] = "00:11:22:33:44:55"
    io = Script("", "4095", "300", "", "10.0.30.5", "10.0.30.5/24", "10.0.30.1", "", "keep")
    assert tool.wizard(io, host, Clock()) == 0 and not io.lines, io.text
    assert "  The VLAN ID must be a number from 1 to 4094." in io.out and "  '10.0.30.5' is not an address with its prefix, like 10.0.100.5/24." in io.out
    io = Script("2", "", "100", "", "10.0.100.5/24", "", "0")
    assert tool.menu(io, host, Clock()) == 0 and not io.lines, io.text
    assert "VLAN 100: the address 10.0.100.5 is already used by another machine (00:11:22:33:44:55). Choose another address." in io.out
    assert "Nothing was changed: the settings from before are in force." in io.out
    good = tool.read_answers(tool._read(tool.GOOD))
    assert set(host.addresses) == {"vlan300"} and good == {
        "NET_IFACE": "eno1", "NET_VLAN": "300", "NET_MODE": "static", "NET_ADDRESS": "10.0.30.5/24", "NET_GATEWAY": "10.0.30.1",
        "NET_DNS": "10.0.30.1"}, "the first interface is still the one flat network it was"


def test_after_dhcp_gave_nothing_the_question_offers_a_typed_address(machine, monkeypatch):
    question = "Does the network give this machine its address by itself (DHCP)?"
    io = Script("", "", "", "172.16.0.5/24", "", "", "keep")     # DHCP: nothing answers; then no VLAN, Enter, the address
    assert tool.wizard(io, FakeHost("enp1s0"), Clock()) == 0 and not io.lines, io.text
    assert [line for line in io.out if line.startswith(question)] == [f"{question} [y]:", f"{question} [n]:"]
    assert "\nThe questions again." in io.out
    assert tool.read_answers(tool._read(tool.GOOD)) == {
        "NET_IFACE": "enp1s0", "NET_MAC": "00:50:56:aa:bb:01", "NET_MODE": "static", "NET_ADDRESS": "172.16.0.5/24"}
    # A person who typed "back" on a network where DHCP works is offered DHCP again.
    monkeypatch.setattr(tool, "ROOT", str(machine / "dhcp-works"))
    io = Script("", "back", "", "", "keep")
    assert tool.wizard(io, FakeHost("ens192", leases={"ens192": LEASE}), Clock()) == 0 and not io.lines
    assert [line for line in io.out if line.startswith(question)] == [f"{question} [y]:"] * 2


def test_changing_the_interface_you_are_connected_through_asks_twice_and_names_your_address(machine):
    host, clock = FakeHost("eno1"), Clock()
    assert tool.change(answers(CASE_B), host, Script("keep"), clock)
    host.session = "10.0.30.77"
    io = Script("3", "1", "y", "n", "0")   # change, VLAN 300, yes, then no at "Are you sure?"
    assert tool.menu(io, host, clock) == 0
    assert "You are connected from 10.0.30.77, through this interface." in io.out
    assert "Change it anyway? [n]: y" in io.out and "Are you sure? [n]: n" in io.out
    assert "netplan" not in host.calls[host.calls.index("arm 120") + 1:], "nothing was changed"
    # Another interface asks nothing extra; removing the connected one asks twice as well.
    io = Script("4", "2", "y", "keep", "4", "1", "n", "0")
    assert tool.menu(io, host, clock) == 0
    assert "Remove VLAN 100? [n]: y" in io.out and "Remove it anyway? [n]: n" in io.out
    assert [n["name"] for n in json.loads(tool._read(tool.LIST))["networks"]] == ["VLAN 300", "VLAN 200"]
    assert "drop vlan100" in host.calls
    # A session from a subnet behind a device network's gateway is warned about the way back.
    host.session = "10.20.5.9"
    io = Script("keep")
    assert tool.change(answers(CASE_B), host, io, clock)
    assert "Warning: Your own address 10.20.5.9 is inside a subnet behind the gateway of VLAN 200." in io.text


def test_an_interface_from_an_answers_file_keeps_what_the_console_does_not_ask_for(machine):
    """Roles, a name and a test device come from a file. Changing the address at the console leaves the role and the
    name; the test device goes, because the console asks for an address to try and stores none."""
    host, clock = FakeHost("eno1"), Clock()
    assert tool.change(answers(CASE_B), host, Script("keep"), clock)
    io = Script("3", "3", "", "", "", "10.0.200.6/24", "", "keep", "0")   # VLAN 200: port, VLAN, web, a new address, gateway
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    assert "Networks reached through that gateway" not in io.text, "the routes belong to the Routes menu once the interface exists"
    assert tool.read_answers(tool._read(tool.GOOD)) == answers(CASE_B, NET3_ADDRESS="10.0.200.6/24", NET3_TEST=None)
    # The interface that leads to the internet is asked for its gateway and its DNS servers, and stays what it was.
    io = Script("3", "1", "", "", "", "", "", "", "", "keep", "0")
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    assert "Gateway (the default route) [10.0.30.1]:" in io.out and "DNS servers, separated by spaces (at most three) [10.0.30.1]:" in io.out
    assert tool.read_answers(tool._read(tool.GOOD))["NET1_ROLES"] == "internet users"


def test_web_and_ssh_set_apart_in_an_answers_file_are_asked_apart_and_enter_changes_neither(machine):
    """Found in review: one question for both, with "y" offered because the web interface answers there, switched SSH on
    for whoever pressed Enter."""
    text = ("NET1_NAME=Office\nNET1_ROLES=internet users\nNET1_IFACE=ens192\nNET1_ADDRESS=10.0.30.5/24\nNET1_GATEWAY=10.0.30.1\n"
            "NET1_SSH=no\nNET2_NAME=Lab\nNET2_ROLES=devices\nNET2_IFACE=ens224\nNET2_ADDRESS=10.0.40.5/24\nNET2_SSH=yes\n")
    host, clock = FakeHost("ens192", "ens224"), Clock()
    assert tool.change(answers(text), host, Script("keep"), clock)
    io = Script("3", "1", "", "", "", "", "", "", "", "keep", "3", "2", "", "", "", "", "", "keep", "0")   # both changed with Enter only
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    assert io.out.count("May the web interface answer on this interface? [y]:") == 1
    assert io.out.count("May SSH answer on this interface, while SSH is switched on? [n]:") == 1
    assert "May the web interface answer on this interface? [n]:" in io.out and "May the web interface and SSH" not in io.text
    assert tool.read_answers(tool._read(tool.GOOD)) == answers(
        text, NET1_MODE="static", NET1_DNS="10.0.30.1", NET2_MODE="static", NET1_MAC="00:50:56:aa:bb:01", NET2_MAC="00:50:56:aa:bb:02")
    assert json.loads(tool._read(tool.GUARD))["ssh_if"] == ["ens224"]


def test_the_menu_shows_tests_restores_and_names_services(machine):
    host, clock = FakeHost("ens192", "ens224", leases={"ens192": LEASE}), Clock()
    assert tool.change(answers(FLAT_DHCP), host, Script("keep"), clock)
    io = Script("1", "6", "", "7", "n", "4", "0")
    assert tool.menu(io, host, clock) == 0 and not io.lines
    assert "Web interface:  https://192.168.10.57" in io.out[io.out.index("Choice [0]: 1") + 1]
    assert "This is the only interface. It can be changed, not removed." in io.out
    assert not re.search(r"vlan|role", io.text, re.I), "the menu of a machine with one interface"
    io = Script("2", "", "", "10.0.100.5/24", "", "keep", "1", "0")
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    listed = json.loads(tool._read(tool.LIST))["networks"]
    assert [(n["name"], n["roles"], n["address"]) for n in listed] == [
        ("Interface 1", ["internet", "users", "devices"], "192.168.10.57/24"), ("Interface 2", ["devices"], "10.0.100.5/24")]
    assert tool.NOT_HERE in io.out and "On your devices, allow management from:  192.168.10.57 (Interface 1),  10.0.100.5 (Interface 2)" in io.text
    assert "The product itself cannot reach that network; only its device connections can." in io.text
    assert "The product may also use" not in io.text
    # Services inside the devices-only network, asked once; then back to what was kept before.
    io = Script("8", "10.0.100.20:514/udp", "keep", "7", "y", "back", "0")
    assert tool.menu(io, host, clock) == 0
    assert "The product may also use:  10.0.100.20:514/udp" in io.text
    assert "Went back: the settings from before are in force again." in io.out
    assert tool.main(["web"], host=host, io=(io := Script())) == 0 and io.out == ["ens192 192.168.10.57"]
    assert tool.main(["list"], host=host, io=(io := Script())) == 0 and "Interface 1  Internet+Users+Devices" in io.out[0]


def test_the_screens_in_the_guides_are_what_the_tool_really_prints(machine, tmp_path, monkeypatch):
    """Every block marked `<!-- screen: NAME -->` in a networking guide must be,
    line for line, part of what the tool prints for that case: `flat`, `case-a`,
    `trunk-vm` and `case-b` are the first boot, `NAME-menu` what the menu showed
    when the other interfaces were added, `two-cards` and `routes` the owner's
    scenario of 2026-10-10 (a second card, then the Routes and Test entries)."""
    shown = {}
    for case in ("flat", "case-a", "trunk-vm", "case-b"):
        monkeypatch.setattr(tool, "ROOT", str(tmp_path / case))
        io, host, menu = first_boot(case)
        shown[case] = io.text
        if menu:
            shown[case + "-menu"] = menu.text
    monkeypatch.setattr(tool, "ROOT", str(tmp_path / "two-cards"))
    host, clock = two_cards()
    io = Script(*ADD_TWO, "1", "0")
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    shown["two-cards"] = io.text
    io = Script("5", "1", "2", "10.50.0.0/16", "", "keep", "1", "1", "10.60.0.0/16", "192.168.10.254", "keep",
                "2", "1", "y", "keep", "0", "6", "172.16.0.9", "0")
    assert tool.menu(io, host, clock) == 0 and not io.lines, io.text
    shown["routes"] = io.text
    if os.environ.get("REZZTOR_PRINT_SCREENS"):   # for whoever writes a guide: every screen, as it is
        Path(os.environ["REZZTOR_PRINT_SCREENS"]).write_text("".join(f"===== {name} =====\n{text}\n\n" for name, text in shown.items()))
    blocks = [(path.name, name, body) for path in [*sorted(GUIDES.glob("appliance-*.md")), GUIDES / "install-from-iso-vmware.md"]
              for name, body in re.findall(r"<!-- screen: ([a-z-]+) -->\n```text\n(.*?)```", path.read_text(), re.S)]
    assert len(blocks) >= 20, "the networking guides show the console screens"
    for guide, name, body in blocks:
        assert body in shown[name] + "\n", f"{guide}: a screen marked '{name}' is not what the tool prints:\n{body}"
