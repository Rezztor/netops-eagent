"""The executor's job API, exercised through its own request handling.

`executor/app.py` imports ansible-runner lazily, so the whole request path --
authentication, field refusal, catalogue resolution, variable validation and the
caps -- can be tested without Ansible installed and without anything running.

That is the point: every refusal below happens before a playbook is chosen, and
none of them depends on Ansible behaving.
"""

from __future__ import annotations

import pytest

from executor import app as executor_app
from executor import store as executor_store

from plugins._ea_ansible.tests.conftest import AUTH, TOKEN
GOOD = {
    "playbook_name": "reads/ping_localhost",
    "commit": "unpinned",
    "variables": {},
    "limit": "",
    "check_mode": True,
}


# --- authentication ----------------------------------------------------------

def test_health_needs_no_token_and_reports_the_commit_to_match(client):
    body = client.get("/healthz").get_json()
    assert body["status"] == "ok"
    assert body["catalogue_commit"] == "unpinned"
    assert "reads/ping_localhost" in body["catalogue_entries"]


def test_health_leaks_no_secret(client):
    assert TOKEN not in client.get("/healthz").get_data(as_text=True)


def test_a_job_without_a_token_is_refused(client):
    assert client.post("/jobs", json=GOOD).status_code == 401


def test_a_job_with_the_wrong_token_is_refused(client):
    response = client.post("/jobs", json=GOOD, headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_a_token_with_a_letter_outside_ascii_is_refused_like_any_wrong_token(client):
    """`hmac.compare_digest` raises on a str that is not ASCII, and a header arrives as Latin-1: one such byte from
    anybody who can reach the port was an unhandled error (500), on the job plane and the admin plane."""
    wrong = {"Authorization": "Bearer caf\xe9"}
    assert client.post("/jobs", json=GOOD, headers=wrong).status_code == 401
    assert client.put("/inventory", json={}, headers=wrong).status_code == 401


def test_with_no_token_configured_the_executor_accepts_nothing(monkeypatch):
    """Fails closed. An unconfigured executor is stopped, not open."""
    monkeypatch.delenv(executor_app.ENV_TOKEN, raising=False)
    client = executor_app.app.test_client()
    assert client.post("/jobs", json=GOOD, headers=AUTH).status_code == 401


# --- the fields it refuses by name -------------------------------------------

@pytest.mark.parametrize(
    "field,value",
    [
        ("playbook", "- hosts: all\n  tasks: []"),
        ("content", "anything"),
        ("tasks", [{"shell": "rm -rf /"}]),
        ("command", "show running-config"),
        ("device_ip", "10.1.1.1"),
        ("password", "hunter2"),
        ("vault_password", "hunter2"),
    ],
)
def test_a_job_carrying_content_a_command_or_a_credential_is_refused(client, field, value):
    response = client.post("/jobs", json={**GOOD, field: value}, headers=AUTH)
    assert response.status_code == 400
    assert "refuses" in response.get_json()["error"]


def test_an_unknown_field_is_refused_rather_than_ignored(client):
    response = client.post("/jobs", json={**GOOD, "force": True}, headers=AUTH)
    assert response.status_code == 400
    assert "Unknown" in response.get_json()["error"]


def test_a_body_that_is_not_an_object_is_refused(client):
    assert client.post("/jobs", json=["reads/ping_localhost"], headers=AUTH).status_code == 400


# --- catalogue, commit and variables -----------------------------------------

def test_an_entry_that_is_not_in_the_catalogue_is_a_miss(client):
    response = client.post(
        "/jobs", json={**GOOD, "playbook_name": "changes/whatever"}, headers=AUTH
    )
    assert response.status_code == 404
    assert "not in the playbook catalogue" in response.get_json()["error"]


def test_a_job_drafted_against_another_catalogue_is_refused(client):
    response = client.post("/jobs", json={**GOOD, "commit": "b" * 40}, headers=AUTH)
    assert response.status_code == 409
    assert response.get_json()["executor_catalogue_commit"] == "unpinned"


def test_a_variable_the_entry_does_not_declare_is_refused(client):
    response = client.post(
        "/jobs", json={**GOOD, "variables": {"interface": "Gi0/0"}}, headers=AUTH
    )
    assert response.status_code == 400
    assert "does not take" in response.get_json()["error"]


# --- the caps ----------------------------------------------------------------

def test_a_selector_with_shell_characters_is_refused(client):
    for hostile in ("core-1; rm -rf /", "core-1 && id", "$(whoami)", "core-1|sh"):
        response = client.post("/jobs", json={**GOOD, "limit": hostile}, headers=AUTH)
        assert response.status_code == 400, hostile
        assert "not allowed" in response.get_json()["error"]


def test_more_devices_than_the_cap_is_refused(client, monkeypatch):
    monkeypatch.setenv(executor_app.ENV_MAX_DEVICES, "3")
    response = client.post("/jobs", json={**GOOD, "limit": "a,b,c,d"}, headers=AUTH)
    assert response.status_code == 400
    assert "at most 3 devices" in response.get_json()["error"]


def test_a_read_cannot_be_asked_to_run_for_real(client):
    response = client.post("/jobs", json={**GOOD, "check_mode": False}, headers=AUTH)
    assert response.status_code == 400
    assert "only runs in check mode" in response.get_json()["error"]


def test_check_mode_must_be_a_boolean(client):
    response = client.post("/jobs", json={**GOOD, "check_mode": "yes"}, headers=AUTH)
    assert response.status_code == 400


# --- acceptance, and one job at a time ---------------------------------------

def test_a_well_formed_job_is_accepted_and_reportable(client, monkeypatch):
    monkeypatch.setattr(
        executor_app,
        "_run_playbook",
        lambda *a, **k: {"status": "succeeded", "exit_code": 0, "output": "ok", "events": []},
    )
    response = client.post("/jobs", json=GOOD, headers=AUTH)
    assert response.status_code == 202
    job_id = response.get_json()["job_id"]

    status = client.get(f"/jobs/{job_id}", headers=AUTH)
    assert status.status_code == 200
    assert status.get_json()["playbook_name"] == "reads/ping_localhost"


def test_a_job_is_accepted_before_it_starts_running(client, monkeypatch):
    """202 means recorded and admitted, not finished. Concurrency, queueing and
    the refusals are `test_net_scheduling.py`."""
    monkeypatch.setattr(
        executor_app,
        "_run_playbook",
        lambda *a, **k: {"status": "succeeded", "exit_code": 0, "output": "", "events": []},
    )
    response = client.post("/jobs", json=GOOD, headers=AUTH)
    assert response.status_code == 202
    assert response.get_json()["status"] in {"running", "queued"}


def test_an_unknown_job_id_is_not_found(client):
    assert client.get("/jobs/deadbeef", headers=AUTH).status_code == 404


def test_job_status_needs_a_token(client):
    assert client.get("/jobs/deadbeef").status_code == 401


# --- the run directory -------------------------------------------------------

def test_each_job_gets_its_own_run_directory(tmp_path, monkeypatch):
    """ansible-runner does not create private_data_dir; it fails on a missing one.

    That failure only appears at the first real job -- "private_data_dir path is
    either invalid or does not exist" -- which is exactly the class of bug a
    unit test cannot see and a live run finds in a second.
    """
    monkeypatch.setenv("REZZTOR_ANSIBLE_RUNDIR", str(tmp_path / "runs"))
    first = executor_app._fresh_private_data_dir()
    second = executor_app._fresh_private_data_dir()
    import os

    assert os.path.isdir(first) and os.path.isdir(second)
    assert first != second, "two jobs must not share a run directory"


def test_a_job_against_devices_must_name_them(client):
    """No selector means `hosts: all`: every device in the inventory, past the device cap."""
    for check_mode, name in ((True, "reads/show"), (False, "changes/sonic_community_vlan_add")):
        variables = {"command_1": "show version"} if check_mode else {"vlan_id": 100}
        response = client.post(
            "/jobs", json={**GOOD, "playbook_name": name, "variables": variables, "check_mode": check_mode}, headers=AUTH
        )
        assert response.status_code == 400, name
        assert "must name them" in response.get_json()["error"]


def test_a_selector_must_be_stored_device_names_not_a_pattern_or_a_group(client, monkeypatch):
    """`*` or a group name passed the cap as one device and ran against the whole estate (audit 3, L7)."""
    from executor import inventory as executor_inventory

    executor_inventory.store().save({
        "hosts": {"sw1": {"ansible_host": "192.0.2.1"}, "sw2": {"ansible_host": "192.0.2.2"}},
        "groups": {"inv_ios": {"hosts": ["sw1", "sw2"]}},
    })
    monkeypatch.setattr(
        executor_app, "_run_playbook",
        lambda *a, **k: {"status": "succeeded", "exit_code": 0, "output": "", "events": []},
    )
    job = {**GOOD, "playbook_name": "reads/show", "variables": {"command_1": "show version"}}
    for hostile in ("*", "sw*", "!sw1", "sw[0:1]", "all", "inv_ios", "sw1,inv_ios", "nope"):
        response = client.post("/jobs", json={**job, "limit": hostile}, headers=AUTH)
        assert response.status_code == 400, hostile
    assert client.post("/jobs", json={**job, "limit": "sw1,sw2"}, headers=AUTH).status_code == 202
