import json
from pathlib import Path

import pytest

from plugins._ea_inventory.helpers.facts.normalisers import normalise_facts

FX = Path(__file__).parent / "fixtures" / "facts"


def load(name):
    return json.loads((FX / f"{name}.json").read_text())["facts"]


def run(platform, name):
    return normalise_facts(platform, load(name))


@pytest.mark.parametrize("name", sorted(p.stem for p in FX.glob("*.json") if not p.stem.endswith("_lab")))  # `_lab` fixtures are real captures
def test_fixtures_say_unverified(name):
    comment = json.loads((FX / f"{name}.json").read_text())["_comment"]
    assert comment.startswith("UNVERIFIED")
    assert next(iter(json.loads((FX / f"{name}.json").read_text()))) == "_comment"


@pytest.mark.parametrize("platform,name,vendor,model,serial,version,host", [
    ("ios", "ios", "Cisco", "C9300-48P", "FOC2245X0AB", "17.3.4", "core-sw1"),
    ("ios", "ios_lab", "Cisco", "3725", "FTX0945W0MY", "12.4(15)T14", "c3725-lab"),  # real, from a Cisco 3725
    ("iosxe", "ios", "Cisco", "C9300-48P", "FOC2245X0AB", "17.3.4", "core-sw1"),
    ("nxos", "nxos", "Cisco", "Nexus9000 C93180YC-EX", "FDO21120ABC", "9.3(8)", "nx-leaf1"),
    ("eos", "eos", "Arista", "DCS-7050SX3-48YC8", "JPE20230123", "4.28.3M", "eos-spine1"),
    ("junos", "junos", "Juniper", "EX4300-48T", "PE3716370001", "20.4R3-S2.6", "ex-core"),
    ("fortios", "fortios", "Fortinet", "FGT60F", "FGT60F0000000001", "7.2.4", "fw-edge1"),
    ("panos", "panos", "Palo Alto", "PA-440", "012345678901", "10.2.4-h2", "pa-edge1"),
])
def test_every_platform(platform, name, vendor, model, serial, version, host):
    r = run(platform, name)
    f = r.fields
    assert (f["vendor"], f["model"], f["serial"], f["software_version"], r.hostname) == (vendor, model, serial, version, host)


def test_sonic_empty_and_unknown_platform():
    r = run("sonic", "sonic_empty")
    assert r.fields == {} and r.hostname == "" and r.warnings
    r = normalise_facts("sonic", {"ansible_net_gather_subset": ["min"]})
    assert r.fields == {} and r.warnings
    r = normalise_facts("other", load("ios"))
    assert r.fields == {} and r.warnings


def test_sonic_with_facts_sets_vendor():
    r = normalise_facts("sonic", {"ansible_net_hostname": "Sw1", "ansible_net_version": "SONiC-OS-4.1"})
    assert r.fields == {"vendor": "SONiC", "software_version": "SONiC-OS-4.1"} and r.hostname == "sw1"


def test_sonic_community_records_only_what_the_device_printed():
    r = normalise_facts("sonic_community", {"ansible_net_hostname": "lab-sonic", "ansible_net_model": "Force10-S6000"})
    assert r.fields == {"vendor": "SONiC", "model": "Force10-S6000"} and r.hostname == "lab-sonic" and not r.warnings


def test_missing_fields_are_absent_never_empty():
    r = normalise_facts("ios", {"ansible_net_hostname": "r1", "ansible_net_version": "15.2", "ansible_net_model": ""})
    assert r.fields == {"vendor": "Cisco", "software_version": "15.2"}
    assert any("serial" in w for w in r.warnings)


@pytest.mark.parametrize("serial", ["N/A", "unknown", "-", "0000000", "None"])
def test_placeholder_serials_dropped(serial):
    r = normalise_facts("eos", {"ansible_net_serialnum": serial, "ansible_net_model": "x"})
    assert "serial" not in r.fields


def test_serial_is_upper_cased():
    assert normalise_facts("ios", {"ansible_net_serialnum": " foc1 "}).fields["serial"] == "FOC1"


def test_ha_fortinet():
    f = run("fortios", "fortios").fields
    assert f["ha_group"] == "edge-cluster"
    assert f["custom"] == {"ha_role": "secondary", "ha_peer": "FGT60F0000000002"}
    assert run("fortios", "fortios_standalone").fields["custom"] == {"ha_role": "standalone"}
    facts = load("fortios")
    facts["system_ha-peer"] = {"results": []}
    assert normalise_facts("fortios", facts).fields["custom"]["ha_role"] == "unknown"


@pytest.mark.parametrize("state,role", [("active", "primary"), ("passive", "secondary"), ("tentative", "unknown")])
def test_ha_panos(state, role):
    facts = {**load("panos"), "ansible_net_ha_localstate": state}
    assert normalise_facts("panos", facts).fields["custom"]["ha_role"] == role
    assert normalise_facts("panos", facts).fields["custom"]["ha_peer"] == "012345678902"


def test_ha_panos_disabled():
    r = normalise_facts("panos", {**load("panos"), "ansible_net_ha_enabled": False})
    assert r.fields["custom"] == {"ha_role": "standalone"}


def test_ha_junos_virtual_chassis_and_cisco_stack():
    f = run("junos", "junos_vc").fields
    assert f["ha_group"] == "ex-vc" and f["custom"] == {"ha_role": "unknown", "ha_peer": "PE3716370002"}
    f = run("ios", "ios_stack").fields
    assert "ha_group" not in f and f["custom"] == {"ha_role": "unknown", "ha_peer": "FOC2245X0CD"}
    assert "custom" not in run("junos", "junos").fields and "custom" not in run("ios", "ios").fields


@pytest.mark.parametrize("platform", ["ios", "iosxe", "nxos", "eos", "junos", "fortios", "panos", "sonic", "other"])
@pytest.mark.parametrize("garbage", [None, "x", 5, [], {}, [1], {"a": 1}, {"system_status": "x", "system_ha": [1], "system_ha-peer": 3},
                                     {"ansible_net_model": {"a": 1}, "ansible_net_serialnum": ["a"], "ansible_net_multi_ne": 7},
                                     {"ansible_net_model": "x" * 1000, "ansible_net_hostname": 3.5},
                                     {"ansible_net_stacked_serialnums": [None, 1, {"a": 1}], "ansible_net_ha_enabled": "maybe"}])
def test_never_raises(platform, garbage):
    r = normalise_facts(platform, garbage)
    assert isinstance(r.fields, dict) and isinstance(r.warnings, list) and isinstance(r.hostname, str)


def test_overlong_value_is_dropped_with_warning():
    r = normalise_facts("ios", {"ansible_net_model": "x" * 1000, "ansible_net_version": "1"})
    assert "model" not in r.fields and any("model" in w for w in r.warnings)


@pytest.mark.parametrize("platform,name", [("ios", "ios"), ("fortios", "fortios"), ("panos", "panos"), ("junos", "junos_vc")])
def test_idempotent_and_input_untouched(platform, name):
    facts = load(name)
    before = json.dumps(facts, sort_keys=True)
    a, b = normalise_facts(platform, facts), normalise_facts(platform, facts)
    assert a == b and json.dumps(facts, sort_keys=True) == before
