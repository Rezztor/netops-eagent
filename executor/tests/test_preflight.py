"""POST /v1/preflight — inventory facts + injectable TCP check, no secrets."""

from __future__ import annotations

import pytest

from executor import app as executor_app
from executor import credentials as executor_credentials
from executor import inventory as executor_inventory
from executor import store as executor_store

TOKEN = "test-token-value"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ADMIN_TOKEN = "test-admin-token-value"
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
CREDENTIAL_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

INVENTORY = {
    "hosts": {
        "lab-sonic": {"ansible_host": "172.30.30.11"},
        "sw1": {"ansible_host": "10.1.1.1", "vars": {"ansible_port": 2222}},
    },
    "groups": {
        "inv_sonic_community": {
            "hosts": ["lab-sonic"],
            "vars": {
                "ansible_network_os": "sonic_community",
                "ansible_connection": "paramiko_ssh",
            },
            "credential": "lab-sonic",
        },
        "inv_ios": {
            "hosts": ["sw1"],
            "vars": {
                "ansible_network_os": "cisco.ios.ios",
                "ansible_connection": "ansible.netcommon.network_cli",
            },
            "credential": "missing-cred",
        },
    },
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv(executor_app.ENV_TOKEN, TOKEN)
    monkeypatch.setenv(executor_app.ENV_ADMIN_TOKEN, ADMIN_TOKEN)
    monkeypatch.setenv(executor_credentials.ENV_KEY, CREDENTIAL_KEY)
    monkeypatch.delenv(executor_app.ENV_COMMIT, raising=False)
    executor_store.reset_for_tests(str(tmp_path / "jobs.sqlite"))
    executor_credentials.reset_for_tests(str(tmp_path / "credentials.json"))
    executor_inventory.reset_for_tests(str(tmp_path / "inventory.json"))
    executor_app._scheduler.reset_for_tests()
    # No real network: every connect answer comes from here.
    monkeypatch.setenv("REZZTOR_NETWORKS_FILE", str(tmp_path / "no-such-list.json"))
    executor_app._tcp_connect = lambda address, port: (
        "ok" if (address, port) == ("172.30.30.11", 22) else "silent"
    )
    executor_app.app.config.update(TESTING=True)
    try:
        yield executor_app.app.test_client()
    finally:
        executor_app._tcp_connect = executor_app.tcp_connect
        executor_app._scheduler.reset_for_tests()
        executor_store.reset_for_tests(None)
        executor_credentials.reset_for_tests(None)
        executor_inventory.reset_for_tests(None)


def _seed(client) -> None:
    assert client.put("/inventory", json=INVENTORY, headers=ADMIN).status_code == 200
    assert (
        client.put(
            "/credentials/lab-sonic",
            json={"kind": "password", "secret": "hunter2", "username": "admin"},
            headers=ADMIN,
        ).status_code
        == 200
    )


def test_preflight_reports_inventory_reachability_and_credential(client):
    _seed(client)
    response = client.post("/v1/preflight", json={"hosts": ["lab-sonic", "sw1", "ghost"]}, headers=AUTH)
    assert response.status_code == 200
    results = response.get_json()["results"]
    assert results["lab-sonic"] == {
        "in_inventory": True,
        "address": "172.30.30.11",
        "port": 22,
        "reachable": True,
        "reason": "ok",
        "network": "",
        "routed": False,
        "credential": "lab-sonic",
        "credential_present": True,
        "network_os": "sonic_community",
        "connection": "paramiko_ssh",
    }
    assert results["sw1"]["reachable"] is False
    assert results["sw1"]["port"] == 2222
    assert results["sw1"]["credential_present"] is False
    assert results["ghost"]["in_inventory"] is False
    assert results["ghost"]["reachable"] is False


def test_preflight_never_returns_the_secret(client):
    _seed(client)
    text = client.post(
        "/v1/preflight", json={"hosts": ["lab-sonic"]}, headers=AUTH
    ).get_data(as_text=True)
    assert "hunter2" not in text


def test_preflight_needs_the_job_token(client):
    _seed(client)
    assert client.post("/v1/preflight", json={"hosts": ["lab-sonic"]}).status_code == 401
    assert (
        client.post("/v1/preflight", json={"hosts": ["lab-sonic"]}, headers=ADMIN).status_code
        == 401
    )


def test_preflight_caps_at_ten_hosts(client):
    _seed(client)
    hosts = [f"h{i}" for i in range(11)]
    response = client.post("/v1/preflight", json={"hosts": hosts}, headers=AUTH)
    assert response.status_code == 400
    assert "at most 10" in response.get_json()["error"]
