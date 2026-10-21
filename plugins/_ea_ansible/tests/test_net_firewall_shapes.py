"""The FortiOS and PAN-OS answers become the shapes the reads and the inventory expect.

Fixtures are copied from the vendors' documented output (tests/fixtures/firewall/README.md cites the
pages), not captured from a device. The shaper is plain Python, so this needs no Ansible.
"""

from __future__ import annotations

import importlib.util
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "firewall")
_spec = importlib.util.spec_from_file_location(
    "eagent_firewall", os.path.join(os.path.dirname(HERE), "playbooks", "reads", "filter_plugins", "eagent_firewall.py")
)
fw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fw)

FORTIOS = json.load(open(os.path.join(FIXTURES, "fortios.json"), encoding="utf-8"))
PANOS = json.load(open(os.path.join(FIXTURES, "panos.json"), encoding="utf-8"))

FORTI_READS = {
    "facts": ["/api/v2/monitor/system/status", "/api/v2/cmdb/system/ha?format=mode%7Cgroup-name", "/api/v2/monitor/system/ha-peer"],
    "interfaces": ["/api/v2/monitor/system/interface?include_vlan=true"],
    "routing_table": ["/api/v2/monitor/router/ipv4"],
    "firewall_policy_summary": ["/api/v2/cmdb/firewall/policy?format=policyid%7Cname%7Cstatus%7Caction%7Csrcintf%7Cdstintf"],
    "vpn_tunnels": ["/api/v2/monitor/vpn/ipsec"],
    "system_resources": ["/api/v2/monitor/system/resource/usage?interval=1-min"],
    "session_summary": ["/api/v2/monitor/system/resource/usage?interval=1-min"],
}
PAN_READS = {
    "facts": ["<show><system><info></info></system></show>", "<show><high-availability><state></state></high-availability></show>"],
    "interfaces": ["<show><interface>all</interface></show>"],
    "routing_table": ["<show><routing><route></route></routing></show>"],
    "firewall_policy_summary": ["/config/devices/entry[@name='localhost.localdomain']/vsys/entry/rulebase/security/rules"],
    "vpn_tunnels": ["<show><vpn><flow></flow></vpn></show>"],
    "system_resources": ["<show><system><resources></resources></system></show>"],
    "session_summary": ["<show><session><info></info></session></show>"],
}


def answer(platform, read, *, status=200):
    table, reads = (FORTIOS, FORTI_READS) if platform == "fortios" else (PANOS, PAN_READS)
    return [
        {"status": status, "content": json.dumps(table[r]) if platform == "fortios" else table[r]} for r in reads[read]
    ]


def shaped(platform, read):
    return fw.eagent_fw(answer(platform, read), platform, read)


def test_every_read_has_a_shape_for_both_platforms():
    for platform, reads in (("fortios", FORTI_READS), ("panos", PAN_READS)):
        for read in reads:
            assert shaped(platform, read), (platform, read)


def test_fortios_facts_are_the_shape_the_inventory_normaliser_reads():
    from plugins._ea_inventory.helpers.facts.normalisers import normalise_facts

    facts = shaped("fortios", "facts")
    assert facts["system_status"]["serial"] == "FGT60F0000000001"
    got = normalise_facts("fortios", facts)
    assert got.hostname == "fw-edge1"
    assert got.fields["model"] == "FGT60F" and got.fields["software_version"] == "7.2.4"
    assert got.fields["custom"] == {"ha_role": "secondary", "ha_peer": "FGT60F0000000002"}


def test_panos_facts_are_the_shape_the_inventory_normaliser_reads():
    from plugins._ea_inventory.helpers.facts.normalisers import normalise_facts

    got = normalise_facts("panos", shaped("panos", "facts"))
    assert (got.hostname, got.fields["model"], got.fields["serial"], got.fields["software_version"]) == (
        "pa-edge1", "PA-440", "012345678901", "10.2.4-h2")
    assert got.fields["custom"] == {"ha_role": "primary"}


def test_facts_keep_only_what_the_inventory_reads():
    assert "log_disk_status" not in json.dumps(shaped("fortios", "facts"))
    assert "mac-address" not in json.dumps(shaped("panos", "facts")) and "192.0.2.10" not in json.dumps(shaped("panos", "facts"))


def test_ha_is_best_effort_but_the_status_is_not():
    facts = fw.eagent_fw(answer("fortios", "facts")[:1] + [{"status": 404, "content": "{}"}] * 2, "fortios", "facts")
    assert "system_ha" not in facts and facts["system_status"]["version"] == "v7.2.4"
    with pytest.raises(fw.FirewallAnswerError, match="Access denied"):
        fw.eagent_fw(answer("fortios", "facts", status=401), "fortios", "facts")


def test_interfaces_state_and_addresses():
    forti = {r["name"]: r for r in shaped("fortios", "interfaces")["rows"]}
    assert forti["port1"]["link"] == "up" and forti["port1"]["ip"] == "192.0.2.2/30" and forti["port2"]["link"] == "down"
    pan = {r["name"]: r for r in shaped("panos", "interfaces")["rows"]}
    assert pan["ethernet1/1"] == {"name": "ethernet1/1", "state": "up", "speed_duplex": "1000/full/up",
                                  "mac": "00:1b:17:00:00:11", "zone": "untrust", "ip": "192.0.2.2/30", "vsys": "1"}
    assert pan["ethernet1/2"]["state"] == "down" and "ip" not in pan["ethernet1/2"] and "tunnel.1" in pan


def test_routing_tables():
    assert shaped("fortios", "routing_table")["rows"][0] == {
        "destination": "0.0.0.0/0", "gateway": "192.0.2.1", "interface": "port1", "type": "static",
        "distance": 10, "metric": 0, "priority": 1, "vrf": 0}
    pan = shaped("panos", "routing_table")
    assert pan["total"] == 2 and pan["rows"][0]["next_hop"] == "192.0.2.1" and pan["rows"][0]["flags"] == "A S"


def test_policy_summary_is_counts_and_names_only():
    forti, pan = shaped("fortios", "firewall_policy_summary"), shaped("panos", "firewall_policy_summary")
    assert forti["by_action"] == {"accept": 1, "deny": 1} and forti["rows"][0]["srcintf"] == ["port2"]
    assert pan["by_action"] == {"allow": 1, "deny": 1} and pan["disabled"] == 1
    assert [r["name"] for r in pan["rows"]] == ["allow-web", "block-guest"]
    assert set(pan["rows"][0]) == {"name", "action", "disabled", "from_zones", "to_zones"}  # no addresses, apps or profiles


def test_vpn_tunnel_state():
    forti = shaped("fortios", "vpn_tunnels")
    assert forti["by_state"] == {"up": 1, "down": 1} and forti["rows"][0]["remote_gateway"] == "198.51.100.7"
    pan = shaped("panos", "vpn_tunnels")
    assert pan["by_state"] == {"active": 1, "init": 1} and pan["rows"][0]["inner_interface"] == "tunnel.1"


def test_resources_and_sessions():
    assert shaped("fortios", "system_resources") == {"cpu": 4, "mem": 38, "disk": 12}
    assert shaped("fortios", "session_summary") == {"session": 1241, "session6": 0, "setup_rate": 6, "setup_rate6": 0}
    pan = shaped("panos", "system_resources")
    assert pan["cpu_used_percent"] == 2.3 and len(pan["summary"]) == 3 and "pan_task" not in json.dumps(pan)
    assert shaped("panos", "session_summary") == {
        "num-max": "65536", "num-active": "1241", "num-tcp": "1007", "num-udp": "230", "num-icmp": "4",
        "num-installed": "9981", "cps": "6", "pps": "1200", "kbps": "8800"}


def test_a_long_table_is_capped_with_its_total():
    rows = [{"ip_mask": f"10.{i // 256}.{i % 256}.0/24"} for i in range(fw.CAP + 50)]
    out = fw.eagent_fw([{"status": 200, "content": json.dumps({"results": rows})}], "fortios", "routing_table")
    assert out["total"] == fw.CAP + 50 and out["shown"] == fw.CAP and len(out["rows"]) == fw.CAP


@pytest.mark.parametrize("platform,read,result,message", [
    ("fortios", "routing_table", {"status": -1, "msg": "Request failed: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]>"}, "certificate was not trusted"),
    ("fortios", "routing_table", {"status": 403, "content": "{}"}, "Access denied"),
    ("fortios", "routing_table", {"status": 500, "content": "{\"error\": -3}"}, "HTTP 500"),
    ("panos", "routing_table", {"status": 403, "content": '<response status="error" code="403"><result><msg>Invalid Credential</msg></result></response>'}, "Access denied"),
    ("panos", "routing_table", {"status": 200, "content": '<response status="error" code="13"><result><msg>Object not present</msg></result></response>'}, "Object not present"),
    ("panos", "routing_table", {"status": 200, "content": "<html>login</html>"}, "code"),
])
def test_a_refusal_is_a_plain_reason_without_the_request(platform, read, result, message):
    result = {**result, "invocation": {"module_args": {"headers": {"X-PAN-KEY": "SECRETKEY123", "Authorization": "Bearer SECRETTOKEN"}}}}
    with pytest.raises(fw.FirewallAnswerError, match=message) as error:
        fw.eagent_fw([result], platform, read)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("text", [
    '<!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><response status="success"><result>&b;</result></response>',
    "<response status='success'><result>" + "x" * (fw.MAX_BYTES + 1) + "</result></response>",
])
def test_an_entity_bomb_or_an_oversized_answer_is_refused_unparsed(text):
    with pytest.raises(fw.FirewallAnswerError, match="refused"):
        fw.eagent_fw([{"status": 200, "content": text}], "panos", "routing_table")


def test_the_garbage_a_device_might_send_never_crashes():
    for platform in ("fortios", "panos"):
        for read in FORTI_READS:
            for content in ("", "[]", "null", "{}", "<response/>", '<response status="success"/>'):
                try:
                    fw.eagent_fw([{"status": 200, "content": content}], platform, read)
                except fw.FirewallAnswerError:
                    pass
