"""FortiGate and Palo Alto hosts: the API token reaches the playbook, the connection is local, and the port is 443."""

from __future__ import annotations

import os

import pytest
import yaml

from executor import app as executor_app
from executor import credentials, inventory

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
GROUPS = {
    "inv_fortios": {"hosts": ["fgt1"], "credential": "fgt-token",
                    "vars": {"ansible_network_os": "fortinet.fortios.fortios", "ansible_connection": "ansible.netcommon.httpapi"}},
    "inv_panos": {"hosts": ["pan1"], "credential": "pan-key",
                  "vars": {"ansible_network_os": "paloaltonetworks.panos.panos", "ansible_connection": "ansible.netcommon.httpapi"}},
    "inv_ios": {"hosts": ["sw1"], "credential": "",
                "vars": {"ansible_network_os": "cisco.ios.ios", "ansible_connection": "ansible.netcommon.network_cli"}},
}
DOC = {"groups": GROUPS, "hosts": {
    "fgt1": {"ansible_host": "10.0.0.1", "vars": {}}, "pan1": {"ansible_host": "10.0.0.2", "vars": {"ansible_port": 8443}},
    "sw1": {"ansible_host": "10.0.0.3", "vars": {}}}}


@pytest.fixture
def stores(monkeypatch, tmp_path):
    monkeypatch.setenv(credentials.ENV_KEY, KEY)
    creds = credentials.reset_for_tests(str(tmp_path / "credentials.json"))
    creds.put("fgt-token", kind="api_token", secret="TOKEN-fgt-Zq9")
    creds.put("pan-key", kind="api_token", secret="KEY-pan-Zq9")
    yield inventory.reset_for_tests(str(tmp_path / "inventory.json"))
    credentials.reset_for_tests(None)
    inventory.reset_for_tests(None)


def test_the_api_token_is_the_playbooks_ansible_httpapi_pass_and_the_connection_is_local(stores, tmp_path):
    stores.save(DOC)
    rendered = yaml.safe_load(open(stores.render(str(tmp_path)), encoding="utf-8"))["all"]["children"]
    for group, token in (("inv_fortios", "TOKEN-fgt-Zq9"), ("inv_panos", "KEY-pan-Zq9")):
        assert rendered[group]["vars"]["ansible_httpapi_pass"] == token
        assert rendered[group]["vars"]["ansible_connection"] == "local"
    assert rendered["inv_ios"]["vars"]["ansible_connection"] == "ansible.netcommon.network_cli"  # nothing else changes
    assert oct(os.stat(os.path.join(tmp_path, "inventory.yml")).st_mode & 0o777) == "0o600"


def test_a_document_stored_with_httpapi_is_kept_as_stored_so_preflight_reports_it_stale_while_it_still_renders_local(stores):
    assert stores.save(DOC)["groups"]["inv_panos"]["vars"]["ansible_connection"] == "ansible.netcommon.httpapi"


def test_preflight_knocks_on_443_unless_the_host_names_a_port(stores, monkeypatch):
    seen = {}
    monkeypatch.setattr(executor_app, "_tcp_connect", lambda address, port: seen.setdefault(address, port) and "ok")
    for host in ("fgt1", "pan1", "sw1"):
        executor_app._host_preflight(host, stores.save(DOC), {"fgt-token", "pan-key"})
    assert seen == {"10.0.0.1": 443, "10.0.0.2": 8443, "10.0.0.3": 22}
