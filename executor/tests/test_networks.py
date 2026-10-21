"""The network list in the pre-check: five reason classes, the no-list case, and a broken list."""

from __future__ import annotations

import json

import pytest

from executor import app as executor_app
from executor import networks as executor_networks

from .test_preflight import ADMIN, AUTH, INVENTORY, client  # noqa: F401 - the fixture

LIST = {
    "version": 1,
    "generated_at": 1760000000,
    "networks": [
        {"name": "VLAN 100", "roles": ["devices"], "interface": "vlan100", "parent": "eno1", "vlan": 100,
         "address": "10.0.100.5/24", "gateway": None, "subnet": "10.0.100.0/24", "routed_subnets": [], "state": "up"},
        {"name": "VLAN 200", "roles": ["devices"], "interface": "vlan200", "parent": "eno1", "vlan": 200,
         "address": "10.0.200.5/24", "gateway": "10.0.200.1", "subnet": "10.0.200.0/24",
         "routed_subnets": ["10.20.0.0/16"], "state": "up"},
        {"name": "VLAN 300", "roles": ["internet", "users"], "interface": "vlan300", "parent": "eno1", "vlan": 300,
         "address": "192.0.2.5/24", "gateway": "192.0.2.1", "subnet": "192.0.2.0/24", "routed_subnets": [], "state": "up"},
    ],
}


def row(client, address, outcome):
    inventory = {"hosts": {"d": {"ansible_host": address}}, "groups": {"g": {"hosts": ["d"], "vars": {}, "credential": ""}}}
    assert client.put("/inventory", json=inventory, headers=ADMIN).status_code == 200
    executor_app._tcp_connect = lambda a, p: outcome
    return client.post("/v1/preflight", json={"hosts": ["d"]}, headers=AUTH).get_json()["results"]["d"]


@pytest.fixture
def listed(client, tmp_path, monkeypatch):
    path = tmp_path / "networks.json"
    path.write_text(json.dumps(LIST))
    monkeypatch.setenv("REZZTOR_NETWORKS_FILE", str(path))
    return path


@pytest.mark.parametrize(
    "address, outcome, reason, network, routed",
    [
        ("10.50.1.9", "silent", "no_route_known", "", False),
        ("10.0.100.10", "silent", "no_answer_on_link", "VLAN 100", False),
        ("10.20.0.9", "silent", "no_answer_via_gateway", "VLAN 200", True),
        ("10.0.100.10", "refused", "refused", "VLAN 100", False),
        ("10.0.100.10", "ok", "ok", "VLAN 100", False),
    ],
)
def test_the_reason_class_comes_from_the_connect_and_the_list(client, listed, address, outcome, reason, network, routed):
    got = row(client, address, outcome)
    assert (got["reason"], got["network"], got["routed"]) == (reason, network, routed)
    assert got["reachable"] is (outcome == "ok")


def test_a_down_network_is_named_whatever_the_silence(client, listed):
    down = json.loads(listed.read_text())
    down["networks"][0]["state"] = "down"
    listed.write_text(json.dumps(down))
    assert row(client, "10.0.100.10", "silent")["reason"] == "network_down"


def test_the_list_is_read_again_when_the_file_changes(client, listed):
    assert row(client, "10.0.100.10", "silent")["reason"] == "no_answer_on_link"
    listed.write_text(json.dumps({**LIST, "networks": LIST["networks"][1:]}) + " ")
    assert row(client, "10.0.100.10", "silent")["reason"] == "no_route_known"


@pytest.mark.parametrize("content", [None, "{not json", "[]", '{"networks": "x"}'])
def test_a_missing_or_broken_list_is_nothing_known_not_an_error(client, listed, content):
    listed.unlink() if content is None else listed.write_text(content)
    got = row(client, "10.0.100.10", "silent")
    assert got["reason"] == "" and got["network"] == "" and got["reachable"] is False
    assert client.get("/v1/networks", headers=AUTH).get_json() == {"known": False, "generated_at": None, "networks": []}


def test_the_endpoint_returns_the_list_to_the_job_token_only(client, listed):
    assert client.get("/v1/networks").status_code == 401
    assert client.get("/v1/networks", headers=ADMIN).status_code == 401
    got = client.get("/v1/networks", headers=AUTH).get_json()
    assert got["known"] and [n["name"] for n in got["networks"]] == ["VLAN 100", "VLAN 200", "VLAN 300"]
    assert executor_networks.load() is executor_networks.load()  # cached
