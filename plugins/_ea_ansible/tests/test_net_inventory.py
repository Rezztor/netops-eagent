"""The device inventory the executor owns.

ADR 0030 says the application sends a selector and the executor owns what
resolves it. That was true before this and vacuous: the inventory was a file an
operator mounted, which nobody could change including the operator, without a
redeploy.

Making it editable is the change most able to undo the property the ADR protects,
so the rule that keeps it honest is tested first: the inventory names a
credential, it never contains one. A vars block that accepted `ansible_password`
would be a second credential store with none of the first one's properties.
"""

from __future__ import annotations

import os

import pytest
import yaml

from executor import credentials, inventory
from plugins._ea_ansible.tests.conftest import ADMIN, AUTH

SIMPLE = {
    "hosts": {
        "sw1": {"ansible_host": "10.1.1.1"},
        "sw2": {"ansible_host": "10.1.1.2"},
    },
    "groups": {
        "core-switches": {
            "hosts": ["sw1", "sw2"],
            "vars": {"ansible_network_os": "cisco.ios.ios"},
            "credential": "",
        }
    },
}


# --- the rule that keeps it from becoming a credential store -----------------

@pytest.mark.parametrize(
    "variable",
    ["ansible_password", "ansible_ssh_pass", "ansible_become_password", "password", "api_key"],
)
def test_a_secret_cannot_be_written_into_group_vars(client, variable):
    document = {
        "hosts": {"sw1": {"ansible_host": "10.1.1.1"}},
        "groups": {"core": {"hosts": ["sw1"], "vars": {variable: "hunter2"}}},
    }
    response = client.put("/inventory", json=document, headers=ADMIN)
    assert response.status_code == 400
    assert variable in response.get_json()["error"]
    assert "credential" in response.get_json()["error"]


def test_a_secret_cannot_be_written_into_host_vars(client):
    document = {
        "hosts": {"sw1": {"ansible_host": "10.1.1.1", "vars": {"ansible_password": "x"}}},
        "groups": {},
    }
    assert client.put("/inventory", json=document, headers=ADMIN).status_code == 400


def test_an_ordinary_connection_variable_is_fine(client):
    response = client.put("/inventory", json=SIMPLE, headers=ADMIN)
    assert response.status_code == 200
    stored = response.get_json()["inventory"]
    assert stored["groups"]["core-switches"]["vars"]["ansible_network_os"] == "cisco.ios.ios"


# --- the token split ---------------------------------------------------------

def test_the_job_token_cannot_read_the_inventory(client):
    assert client.get("/inventory", headers=AUTH).status_code == 401


def test_the_job_token_cannot_rewrite_the_inventory(client):
    """This is the one that matters. A compromised agent must not be able to
    point a reviewed playbook at a device nobody chose for it."""
    assert client.put("/inventory", json=SIMPLE, headers=AUTH).status_code == 401


# --- validation --------------------------------------------------------------

def test_a_group_naming_a_host_that_does_not_exist_is_refused(client):
    """It would resolve to an empty --limit, which runs against nothing and
    reports success."""
    document = {"hosts": {}, "groups": {"core": {"hosts": ["ghost"]}}}
    response = client.put("/inventory", json=document, headers=ADMIN)
    assert response.status_code == 400
    assert "ghost" in response.get_json()["error"]


def test_a_host_without_an_address_is_refused(client):
    document = {"hosts": {"sw1": {}}, "groups": {}}
    assert client.put("/inventory", json=document, headers=ADMIN).status_code == 400


@pytest.mark.parametrize("name", ["has space", "../etc", "sw1;reboot", ""])
def test_a_name_that_is_not_an_identifier_is_refused(client, name):
    document = {"hosts": {name: {"ansible_host": "10.1.1.1"}}, "groups": {}}
    assert client.put("/inventory", json=document, headers=ADMIN).status_code == 400


def test_an_unknown_section_is_refused_rather_than_ignored(client):
    document = {**SIMPLE, "plays": []}
    assert client.put("/inventory", json=document, headers=ADMIN).status_code == 400


def test_an_empty_inventory_reads_back_as_empty_not_as_an_error(client):
    body = client.get("/inventory", headers=ADMIN).get_json()
    assert body["inventory"] == {"groups": {}, "hosts": {}}


# --- credentials are named, not held -----------------------------------------

def test_a_credential_that_is_not_stored_yet_is_reported_not_refused(client):
    """Loading an inventory before its credentials is a reasonable order to do
    them in. Being told is more use than being stopped."""
    document = {
        "hosts": {"sw1": {"ansible_host": "10.1.1.1"}},
        "groups": {"core": {"hosts": ["sw1"], "credential": "core-login"}},
    }
    response = client.put("/inventory", json=document, headers=ADMIN)
    assert response.status_code == 200
    assert response.get_json()["unresolved_credentials"] == ["core-login"]


def test_once_stored_the_credential_is_no_longer_unresolved(client):
    client.put(
        "/credentials/core-login",
        json={"kind": "password", "secret": "hunter2", "username": "netops"},
        headers=ADMIN,
    )
    document = {
        "hosts": {"sw1": {"ansible_host": "10.1.1.1"}},
        "groups": {"core": {"hosts": ["sw1"], "credential": "core-login"}},
    }
    response = client.put("/inventory", json=document, headers=ADMIN)
    assert response.get_json()["unresolved_credentials"] == []


def test_the_stored_inventory_never_contains_the_secret(client):
    client.put(
        "/credentials/core-login",
        json={"kind": "password", "secret": "hunter2-horse", "username": "netops"},
        headers=ADMIN,
    )
    client.put(
        "/inventory",
        json={
            "hosts": {"sw1": {"ansible_host": "10.1.1.1"}},
            "groups": {"core": {"hosts": ["sw1"], "credential": "core-login"}},
        },
        headers=ADMIN,
    )
    assert "hunter2-horse" not in client.get("/inventory", headers=ADMIN).get_data(as_text=True)


# --- rendering ---------------------------------------------------------------

def test_the_rendered_inventory_carries_the_resolved_credential(client, tmp_path):
    client.put(
        "/credentials/core-login",
        json={"kind": "password", "secret": "hunter2-horse", "username": "netops"},
        headers=ADMIN,
    )
    client.put(
        "/inventory",
        json={
            "hosts": {"sw1": {"ansible_host": "10.1.1.1"}},
            "groups": {
                "core": {
                    "hosts": ["sw1"],
                    "vars": {"ansible_network_os": "cisco.ios.ios"},
                    "credential": "core-login",
                }
            },
        },
        headers=ADMIN,
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    path = inventory.store().render(str(run_dir))
    rendered = yaml.safe_load(open(path, encoding="utf-8"))
    group = rendered["all"]["children"]["core"]
    assert group["hosts"]["sw1"]["ansible_host"] == "10.1.1.1"
    assert group["vars"]["ansible_user"] == "netops"
    assert group["vars"]["ansible_password"] == "hunter2-horse"


def test_the_rendered_file_is_not_world_readable(client, tmp_path):
    client.put("/inventory", json=SIMPLE, headers=ADMIN)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    path = inventory.store().render(str(run_dir))
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_a_host_in_no_group_is_still_addressable(client, tmp_path):
    """An inventory that validated must be runnable. A host that silently
    vanished at render time would be a selector that matches nothing."""
    client.put(
        "/inventory",
        json={"hosts": {"lonely": {"ansible_host": "10.9.9.9"}}, "groups": {}},
        headers=ADMIN,
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rendered = yaml.safe_load(open(inventory.store().render(str(run_dir)), encoding="utf-8"))
    assert "lonely" in rendered["all"]["children"]["ungrouped"]["hosts"]


def test_rendering_refuses_when_a_named_credential_has_gone(client, tmp_path):
    client.put(
        "/credentials/core-login",
        json={"kind": "password", "secret": "x", "username": ""},
        headers=ADMIN,
    )
    client.put(
        "/inventory",
        json={
            "hosts": {"sw1": {"ansible_host": "10.1.1.1"}},
            "groups": {"core": {"hosts": ["sw1"], "credential": "core-login"}},
        },
        headers=ADMIN,
    )
    client.delete("/credentials/core-login", headers=ADMIN)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(inventory.InventoryError, match="not stored here"):
        inventory.store().render(str(run_dir))


@pytest.mark.parametrize("name", ["all", "ALL", "ungrouped", "localhost"])
def test_a_host_or_group_cannot_take_a_name_ansible_gives_a_meaning(name):
    """A device called `all` made `--limit all` select every device while the card showed one."""
    from executor import inventory

    with pytest.raises(inventory.InventoryError, match="not a usable host name"):
        inventory.validate({"hosts": {name: {"ansible_host": "10.0.0.1"}}})
    with pytest.raises(inventory.InventoryError, match="not a usable group name"):
        inventory.validate({"hosts": {"sw1": {"ansible_host": "10.0.0.1"}}, "groups": {name: {"hosts": ["sw1"]}}})


def test_a_host_cannot_have_the_name_of_a_group():
    """Ansible keeps hosts and groups in one namespace for `--limit`. With a device called `inv_ios` beside the
    group of every IOS device, a change approved for that one device was a change to all of them."""
    from executor import inventory

    with pytest.raises(inventory.InventoryError, match="both a host and a group"):
        inventory.validate({
            "hosts": {"inv_ios": {"ansible_host": "10.0.0.1"}, "r1": {"ansible_host": "10.0.0.2"}},
            "groups": {"inv_ios": {"hosts": ["r1"]}},
        })
