"""The network list: the five sentences of research 04 section 8.3, the no-list case, the label and the view's endpoint."""

from __future__ import annotations

import asyncio

import pytest

from helpers import api_policy, rbac

from plugins._ea_ansible.api.network_list import NetworkList
from plugins._ea_ansible.helpers import executor_client, network_list, preflight
from types import SimpleNamespace

RAW = {"version": 1, "generated_at": 1760000000, "networks": [
    {"name": "VLAN 100", "roles": ["devices"], "interface": "vlan100", "parent": "eno1", "vlan": 100,
     "address": "10.0.100.5/24", "gateway": None, "subnet": "10.0.100.0/24", "routed_subnets": [], "state": "up"},
    {"name": "VLAN 200", "roles": ["devices"], "interface": "vlan200", "parent": "eno1", "vlan": 200,
     "address": "10.0.200.5/24", "gateway": "10.0.200.1", "subnet": "10.0.200.0/24",
     "routed_subnets": ["10.20.0.0/16"], "state": "up"},
    {"name": "VLAN 300", "roles": ["internet", "users"], "interface": "vlan300", "parent": "eno1", "vlan": 300,
     "address": "192.0.2.5/24", "gateway": "192.0.2.1", "subnet": "192.0.2.0/24", "routed_subnets": [], "state": "up"},
]}
LISTING = network_list.parse(RAW)
ENTRY = SimpleNamespace(name="reads/interfaces", platforms=("ios",), read_commands={"ios": ("show version",)})


def device(address):
    return {"name": "sw", "mgmt_ip": address, "status": "active", "platform": "ios", "site": {"name": "lab"}}


def reachable_detail(monkeypatch, address, row, listing=LISTING):
    seen = {"in_inventory": True, "address": address, "port": 22, "credential_present": True, "reachable": False, **row}
    monkeypatch.setattr(executor_client, "preflight", lambda agent, hosts: {"sw": seen})
    monkeypatch.setattr(executor_client, "networks", lambda agent=None: listing)
    result = preflight.run_preflight(None, [device(address)], ENTRY)
    return next(c["detail"] for c in result["checks"] if c["label"] == "sw: reachable"), result


@pytest.mark.parametrize(
    "address, row, expected",
    [
        ("10.50.1.9", {"reason": "no_route_known"},
         "10.50.1.9 is not in any device network this appliance knows. It knows: VLAN 100 (10.0.100.0/24); "
         "VLAN 200 (10.0.200.0/24, 10.20.0.0/16). An administrator can add a network or a routed subnet "
         "on the appliance console (menu Network)."),
        ("10.0.100.10", {"reason": "no_answer_on_link", "network": "VLAN 100"},
         "Nothing answers at 10.0.100.10 on VLAN 100. Check that the device is up and that VLAN 100 is "
         "allowed on the appliance's switch port."),
        ("10.20.0.9", {"reason": "no_answer_via_gateway", "network": "VLAN 200", "routed": True},
         "No answer from 10.20.0.9 through gateway 10.0.200.1. Check the route on the gateway and that "
         "the device's management ACL allows 10.0.200.5."),
        ("10.0.100.10", {"reason": "refused", "network": "VLAN 100"},
         "10.0.100.10 answers but refuses port 22. Is SSH enabled, or is this device managed over HTTPS?"),
        ("10.0.100.10", {"reason": "network_down", "network": "VLAN 100"},
         "The appliance's VLAN 100 interface is down. This is a problem on the appliance or its switch port, "
         "not on the device."),
    ],
)
def test_each_of_the_five_cases_gets_its_sentence(monkeypatch, address, row, expected):
    assert reachable_detail(monkeypatch, address, row)[0] == expected


def test_with_no_list_today_s_sentence_stays(monkeypatch):
    detail, _ = reachable_detail(monkeypatch, "10.0.100.10", {"reason": ""}, network_list.parse(None))
    assert detail == "TCP connect to 10.0.100.10:22 failed"
    # An executor that predates the list sends no reason at all.
    assert reachable_detail(monkeypatch, "10.0.100.10", {})[0] == "TCP connect to 10.0.100.10:22 failed"


def test_a_list_the_executor_cannot_give_falls_back_to_today_s_sentence(monkeypatch):
    def refuse(agent=None):
        raise executor_client.ExecutorUnreachable("down")

    seen = {"in_inventory": True, "address": "10.0.100.10", "port": 22, "reachable": False, "reason": "no_answer_on_link"}
    monkeypatch.setattr(executor_client, "preflight", lambda agent, hosts: {"sw": seen})
    monkeypatch.setattr(executor_client, "networks", refuse)
    result = preflight.run_preflight(None, [device("10.0.100.10")], ENTRY)
    assert [c["detail"] for c in result["checks"] if c["label"] == "sw: reachable"] == ["TCP connect to 10.0.100.10:22 failed"]


def test_the_network_label_rides_with_the_device_facts(monkeypatch):
    _, result = reachable_detail(monkeypatch, "10.20.0.9", {"reason": "ok", "network": "VLAN 200", "routed": True, "reachable": True})
    assert result["devices"][0]["network"] == "VLAN 200, routed"


def test_the_label_is_worked_out_from_the_address_and_directly_attached_wins():
    assert network_list.label("10.0.100.10", LISTING) == "VLAN 100"
    assert network_list.label("10.20.7.7", LISTING) == "VLAN 200, routed"
    assert network_list.label("192.0.2.9", LISTING) == ""  # a users network is not a device network
    assert network_list.label("nonsense", LISTING) == ""
    assert network_list.label("10.0.100.10", network_list.parse(None)) == ""


def test_a_broken_list_is_nothing_known():
    for broken in (None, [], {"networks": "x"}, {"networks": [{"name": ""}, 3]}):
        assert network_list.parse(broken)["known"] is False


def test_the_view_endpoint_gives_networks_hint_and_labels(monkeypatch):
    monkeypatch.setattr(executor_client, "networks", lambda agent=None: LISTING)
    handler = NetworkList(app=None, thread_lock=None)  # type: ignore[arg-type]
    got = asyncio.new_event_loop().run_until_complete(handler.process({"addresses": ["10.20.0.9"]}, None))
    assert got["ok"] and got["known"] and len(got["networks"]) == 3
    assert got["labels"] == {"10.20.0.9": "VLAN 200, routed"}
    assert got["hint"] == "On your devices, allow management from 10.0.100.5 (VLAN 100), 10.0.200.5 (VLAN 200)."


def test_the_view_endpoint_says_nothing_known_when_the_executor_is_silent(monkeypatch):
    def refuse(agent=None):
        raise executor_client.ExecutorUnreachable("down")

    monkeypatch.setattr(executor_client, "networks", refuse)
    handler = NetworkList(app=None, thread_lock=None)  # type: ignore[arg-type]
    got = asyncio.new_event_loop().run_until_complete(handler.process({}, None))
    assert got["ok"] and got["known"] is False and got["networks"] == [] and got["hint"] == ""


def test_the_view_needs_network_view_in_the_central_table_only():
    assert api_policy.required_permission_for("plugins/_ea_ansible/network_list") == rbac.PERM_NETWORK_VIEW
    assert NetworkList.required_permission() is None
