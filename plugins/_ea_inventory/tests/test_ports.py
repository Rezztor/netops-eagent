"""Observed ports and parts: the snapshot store, the refresh, the endpoints and the agent tool (ADR 0045, 0049)."""

import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest

from helpers import api_policy, rbac, tool_permissions
from plugins._ea_ansible.helpers import executor_client
from plugins._ea_inventory.api.inventory_port_uplink import InventoryPortUplink
from plugins._ea_inventory.api.inventory_ports import InventoryPorts
from plugins._ea_inventory.api.inventory_refresh_ports import InventoryRefreshPorts
from plugins._ea_inventory.api.inventory_vlan import InventoryVlan
from plugins._ea_inventory.helpers import ports
from plugins._ea_inventory.helpers.facts import collect_ports, ports_parse
from plugins._ea_inventory.tools.inventory_ports import InventoryPorts as PortsTool

FIXTURES = Path(__file__).parent / "fixtures" / "ports"
SONIC = json.loads((FIXTURES / "sonic_community_documented.json").read_text())
NOW = dt.datetime(2026, 10, 2, 12, 0, tzinfo=dt.timezone.utc)


def call(handler, payload):
    return asyncio.new_event_loop().run_until_complete(handler(app=None, thread_lock=None).process(payload, None))


def tool(args):
    t = PortsTool.__new__(PortsTool)
    t.args, t.agent = args, None
    return asyncio.new_event_loop().run_until_complete(t.execute()).message


def snapshot(device, payload=SONIC, platform="sonic_community"):
    ports.replace_snapshot(device["id"], ports_parse.parse(platform, payload), NOW)


class Executor:
    def __init__(self, payload=SONIC):
        self.payload, self.calls, self.status = payload, [], "succeeded"

    def health(self, agent=None):
        return {"catalogue_commit": "abc"}

    def run(self, agent, playbook, commit, variables, limit, check_mode, wait_seconds=None):
        self.calls.append((playbook, limit, check_mode))
        text = f"EAGENT_PORTS {limit} " + json.dumps({"platform": "sonic_community", **self.payload}, separators=(",", ":"))
        output = json.dumps({"msg": text})[1:-1].join(['"msg": "', '"'])  # as the debug callback prints it
        return executor_client.JobResult(f"job-{limit}", self.status, 0 if self.status == "succeeded" else 2, (), output)


@pytest.fixture
def ex(database, events, monkeypatch):
    e = Executor()
    monkeypatch.setattr(executor_client, "health", e.health)
    monkeypatch.setattr(executor_client, "run", e.run)
    return e


def test_a_refresh_replaces_the_snapshot_and_keeps_the_uplink_mark(make):
    d = make("leaf1", status="active", platform="sonic_community")
    snapshot(d)
    assert ports.set_uplink(d["id"], "Ethernet0", True) and not ports.set_uplink(d["id"], "Ethernet999", True)
    smaller = {"commands": {k: v for k, v in SONIC["commands"].items() if k != "show ip interfaces"}}
    smaller["commands"]["show interfaces status"] = [l for l in SONIC["commands"]["show interfaces status"] if "Ethernet8 " not in l]
    snapshot(d, smaller)
    found = ports.device_ports(d["id"])
    names = {i["name"]: i for i in found["interfaces"]}
    assert "Ethernet8" not in names and names["Ethernet0"]["is_uplink"] and names["Ethernet0"]["uplink_source"] == "marked"
    assert names["Ethernet0"]["ip_prefixes"] == [] and found["observed_at"] == NOW.isoformat()  # replaced, not merged


def test_neighbour_in_the_inventory_makes_an_uplink_and_the_summary_counts(make):
    d = make("leaf1", status="active", platform="sonic_community", mgmt_ip="192.168.1.5")
    spine = make("spine1", status="active", platform="sonic_community")
    snapshot(d)
    found = ports.device_ports(d["id"])
    e0 = next(i for i in found["interfaces"] if i["name"] == "Ethernet0")
    assert e0["neighbor"] == {"name": "spine1", "port": "Ethernet12", "device_id": spine["id"]}
    assert e0["is_uplink"] and e0["uplink_source"] == "neighbour"
    s = ports.summary(found["interfaces"], found["components"], d["mgmt_ip"])
    assert (s["total"], s["up"], s["down"], s["unknown"]) == (3, 2, 1, 0)  # Loopback0 and eth0 are not ports
    assert s["vlans"] == [10, 20] and s["mgmt_subnet"] == "192.168.1.0/24" and s["poe"] is None
    assert s["optics"] == [{"media": "QSFP28 or later", "count": 1}]
    assert s["uplinks"] == [{"port": "Ethernet0", "neighbor": "spine1", "neighbor_port": "Ethernet12", "device_id": spine["id"]}]


def test_refresh_endpoint_runs_the_playbook_and_audits_counts_only(ex, make, events):
    make("leaf1", status="active", platform="sonic_community")
    make("sw9", status="active", platform="sonic")  # no ports collection yet
    out = call(InventoryRefreshPorts, {"devices": ["leaf1", "sw9", "nope"]})
    assert out["ok"] and out["refreshed"] == 1 and out["failed"] == [] and out["skipped"] == 2
    assert out["interfaces"] == 5 and out["components"] == 2
    assert ex.calls == [("reads/inventory_ports", "leaf1", True)]
    audit = [f for e, f in events if e == "inventory.ports_refreshed"]
    assert audit == [{"refreshed": 1, "failed": 0, "skipped": 2, "interfaces": 5, "components": 2}]


def test_refresh_failures_are_per_device_and_an_empty_read_wipes_nothing(ex, make, events):
    d = make("leaf1", status="active", platform="sonic_community")
    snapshot(d)
    ex.payload = {"commands": {"show interfaces status": ["/bin/sh: 1: sudo: not found"]}}
    out = call(InventoryRefreshPorts, {"devices": ["leaf1"]})
    assert out["refreshed"] == 0 and "nothing in it could be read" in out["failed"][0]["error"]
    assert len(ports.device_ports(d["id"])["interfaces"]) == 5
    ex.status = "failed"
    assert "ended failed" in call(InventoryRefreshPorts, {"devices": ["leaf1"]})["failed"][0]["error"]
    assert call(InventoryRefreshPorts, {"devices": "leaf1"})["ok"] is False
    assert call(InventoryRefreshPorts, {"devices": ["leaf1"], "dry_run": True})["failed"]
    assert [f["failed"] for e, f in events if e == "inventory.ports_refreshed"] == [1, 1]  # the dry run is not audited


def test_dry_run_stores_nothing(ex, make):
    d = make("leaf1", status="active", platform="sonic_community")
    out = call(InventoryRefreshPorts, {"devices": ["leaf1"], "dry_run": True})
    assert out["refreshed"] == 1 and out["interfaces"] == 5 and ports.device_ports(d["id"])["interfaces"] == []


def test_ports_endpoint_shape_uplink_toggle_and_vlan_lookup(make, events):
    d = make("leaf1", status="active", platform="sonic_community")
    assert call(InventoryPorts, {"device_id": "nope"})["ok"] is False
    snapshot(d)
    out = call(InventoryPorts, {"device_id": d["id"]})
    assert out["ok"] and set(out) == {"ok", "observed_at", "interfaces", "components", "summary"}
    assert set(out["interfaces"][0]) >= {"name", "alias", "description", "admin_up", "oper_up", "speed", "media", "present", "poe_watts",
                                         "mode", "vlans", "ip_prefixes", "mac", "neighbor", "is_uplink", "uplink_source"}
    json.dumps(out)
    assert call(InventoryPortUplink, {"device_id": d["id"], "name": "Ethernet4", "is_uplink": "yes"})["ok"] is False
    assert call(InventoryPortUplink, {"device_id": d["id"], "name": "Ethernet99", "is_uplink": True})["ok"] is False
    assert call(InventoryPortUplink, {"device_id": d["id"], "name": "Ethernet4", "is_uplink": True}) == {"ok": True}
    assert [f["port"] for e, f in events if e == "inventory.uplink_marked"] == ["Ethernet4"]
    found = call(InventoryVlan, {"vlan": "10"})
    assert [(p["device"], p["port"], p["mode"]) for p in found["ports"]] == [("leaf1", "Ethernet4", "trunk"), ("leaf1", "Ethernet8", "access")]
    assert call(InventoryVlan, {"vlan": 5000})["ok"] is False and call(InventoryVlan, {"vlan": "²"})["ok"] is False


def test_routes_and_tool_need_the_right_permission():
    for name, perm in (("inventory_ports", rbac.PERM_INVENTORY_VIEW), ("inventory_vlan", rbac.PERM_INVENTORY_VIEW),
                       ("inventory_refresh_ports", rbac.PERM_INVENTORY_MANAGE), ("inventory_port_uplink", rbac.PERM_INVENTORY_MANAGE)):
        assert api_policy.required_permission_for(f"plugins/_ea_inventory/{name}") == perm
    assert tool_permissions.required_permission("inventory_ports") == rbac.PERM_INVENTORY_VIEW


def test_tool_is_read_only_and_redacts_descriptions(make):
    text = (Path(__file__).parents[1] / "tools" / "inventory_ports.py").read_text()
    assert "replace_snapshot" not in text and "set_uplink" not in text and "propose" not in text and "refresh_ports" not in text
    assert (Path(__file__).parents[1] / "prompts" / "agent.system.tool.inventory_ports.md").exists()
    d = make("leaf1", status="active", platform="sonic_community")
    assert "no observed ports" in tool({"device": "leaf1"})
    secret = {"commands": {**SONIC["commands"], "show interfaces description": [
        "Interface Oper Admin Alias Description", "--------- ---- ----- ----- -----------------------------------------------",
        "Ethernet0    up   up    e1    enable secret 5 $1$abcd$hashvalue"]}}
    snapshot(d, secret)
    said = tool({"device": "leaf1"})
    assert "hashvalue" not in said and "Ethernet0" in said and "observed_at" in said
    assert "Give either" in tool({}) and "Give either" in tool({"device": "leaf1", "vlan": "1"}) and "1 to 4094" in tool({"vlan": "0"})
    assert "No device named" in tool({"device": "zzz"})
    assert "leaf1" in tool({"vlan": "20"})


def test_tool_caps_rows(make):
    d = make("big1", status="active", platform="ios")
    facts = {"ansible_net_interfaces": {f"GigabitEthernet1/0/{i}": {"operstatus": "up"} for i in range(80)}}
    snapshot(d, {"facts": facts}, "ios")
    text = tool({"device": "big1"})
    body = json.loads(text.split("\n\n", 1)[1])
    assert "Showing the first 60" in text and body["ports_total"] == 80 and len(body["ports"]) == 60


def test_parse_output_plain_and_escaped():
    line = "EAGENT_PORTS sw1 " + json.dumps({"platform": "ios", "commands": {"show x": ["a \"b\""]}, "facts": {}})
    for text in (line, json.dumps({"msg": line})[1:-1]):
        assert collect_ports.parse_output("noise\n" + text)["sw1"]["commands"]["show x"] == ['a "b"']
    assert collect_ports.parse_output("EAGENT_PORTS sw1 {broken") == {}


def test_refresh_reads_nxos_from_the_documented_reply(ex, make):
    d = make("n9k1", status="active", platform="nxos")
    ex.payload = json.loads((FIXTURES / "nxos_documented.json").read_text())  # the commands' values are objects, not lines
    out = call(InventoryRefreshPorts, {"devices": ["n9k1"]})
    assert out["ok"] and out["refreshed"] == 1 and out["failed"] == [] and (out["interfaces"], out["components"]) == (7, 18)
    names = {i["name"]: i for i in ports.device_ports(d["id"])["interfaces"]}
    assert names["Ethernet9/2"]["speed"] == "10G" and names["Ethernet9/1"]["vlans"] == [2]


def test_collect_platforms_match_the_catalogue_entry_and_the_playbook():
    from plugins._ea_ansible.helpers import catalogue

    entry = catalogue.load()["reads/inventory_ports"]
    assert set(collect_ports.PLATFORMS) == set(entry.platforms) == set(entry.read_commands)
    text = open(catalogue.playbook_path(entry), encoding="utf-8").read()
    assert all(f"'{p}'" in text for p in collect_ports.PLATFORMS)  # a branch or a list names each


def test_a_read_with_parts_but_no_ports_wipes_nothing(ex, make, monkeypatch):
    """The interface command failed and `show inventory` worked: every stored port and uplink mark was deleted."""
    d = make("leaf1", status="active", platform="sonic_community")
    snapshot(d)
    part = {"kind": "module", "name": "PSU1", "part_number": "", "serial": "", "description": "", "expires_at": None}
    monkeypatch.setattr(ports_parse, "parse", lambda platform, payload: ports_parse.Parsed(components=[part]))
    out = call(InventoryRefreshPorts, {"devices": ["leaf1"]})
    assert out["refreshed"] == 0 and out["failed"]
    assert len(ports.device_ports(d["id"])["interfaces"]) == 5
