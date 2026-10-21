import pytest

from plugins._ea_inventory.helpers.adapters.solarwinds import normalise_node
from plugins._ea_inventory.helpers.normalise import InventoryError


def node(**over):
    base = {
        "node_id": "1", "caption": "Core-SW1", "dns": "", "sys_name": "", "ip_address": "10.0.0.1",
        "vendor": "Cisco", "machine_type": "Cisco Catalyst 9300", "software_version": "17.3.4 IOS XE",
        "sys_object_id": "1.3.6.1.4.1.9.1.1", "location": "", "status": "up", "serial": "FOC123", "last_poll": None,
    }
    return {**base, **over}


def test_maps_fields_and_lower_cases_the_caption():
    n = normalise_node(node())
    assert (n["name"], n["vendor"], n["model"], n["mgmt_ip"], n["serial"]) == (
        "core-sw1", "Cisco", "Cisco Catalyst 9300", "10.0.0.1", "FOC123")
    assert (n["platform"], n["software_version"]) == ("iosxe", "17.3.4 IOS XE")


@pytest.mark.parametrize("over,platform,low", [
    ({"software_version": "NX-OS 9.3(8)"}, "nxos", False),
    ({"software_version": "15.2(4)E7 IOS"}, "ios", False),
    ({"vendor": "Fortinet", "machine_type": "FortiGate 100F", "software_version": ""}, "fortios", False),
    ({"vendor": "Palo Alto Networks", "software_version": "10.2"}, "panos", False),
    ({"vendor": "Juniper Networks, Inc.", "machine_type": "EX4300"}, "junos", False),
    ({"vendor": "Arista Networks"}, "eos", False),
    ({"vendor": "", "machine_type": "", "software_version": "SONiC.202205", "sys_object_id": ""}, "sonic", False),
    ({"vendor": "", "machine_type": "", "software_version": "", "sys_object_id": "1.3.6.1.4.1.12356.101"}, "fortios", False),
    ({"software_version": ""}, "ios", True),  # Cisco without version text: a weak guess
    ({"vendor": "Acme", "machine_type": "box", "software_version": "1", "sys_object_id": ""}, "other", True),
])
def test_platform_guess(over, platform, low):
    n = normalise_node(node(**over))
    assert n["platform"] == platform
    assert (n["confidence"] < 0.6) is low


def test_name_falls_back_to_sys_name_then_dns():
    assert normalise_node(node(caption="Core Switch 1", sys_name="SW-One"))["name"] == "sw-one"
    n = normalise_node(node(caption="Core Switch 1", sys_name="", dns="sw1.corp.local"))
    assert n["name"] == "sw1.corp.local"
    assert normalise_node(node(caption="A", dns="b.corp"))["aliases"] == ["a", "b.corp"]


@pytest.mark.parametrize("serial,expected", [("N/A", ""), (None, ""), ("0000000", ""), (" foc9 ", "FOC9")])
def test_serial_placeholders(serial, expected):
    assert normalise_node(node(serial=serial))["serial"] == expected


@pytest.mark.parametrize("over", [
    {"caption": "", "sys_name": "", "dns": ""}, {"caption": "bad name!", "sys_name": "", "dns": ""},
    {"ip_address": ""}, {"ip_address": "999.1.1.1"}, {"ip_address": "not-an-ip"},
])
def test_unnamable_or_unaddressable_nodes_are_refused(over):
    with pytest.raises(InventoryError):
        normalise_node(node(**over))
