"""The second door onto the executor, and what it will not do.

A published port on the one workload that can reach the estate is the riskiest
thing in this design, and ADR 0032 says so in its consequences. These tests are
the narrowings that make it defensible, written so that removing one is a visible
act rather than a quiet one.
"""

from __future__ import annotations

import base64

import pytest

from executor import app as executor_app
from executor import console as break_glass
from executor import store as executor_store

import time

ADMIN_TOKEN = "test-admin-token-value"


def _basic(password: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"x:{password}".encode()).decode()}


@pytest.fixture
def page(monkeypatch, tmp_path):
    monkeypatch.setenv(executor_app.ENV_ADMIN_TOKEN, ADMIN_TOKEN)
    monkeypatch.setenv(executor_app.ENV_TOKEN, "a-different-job-token")
    executor_store.reset_for_tests(str(tmp_path / "jobs.sqlite"))
    break_glass.console.config.update(TESTING=True)
    try:
        yield break_glass.console.test_client()
    finally:
        executor_store.reset_for_tests(None)


# --- off unless asked for ----------------------------------------------------

def test_no_port_configured_means_no_listener(monkeypatch):
    """An executor that published a port because someone forgot to turn it off
    is not a thing that should be possible."""
    monkeypatch.delenv(break_glass.ENV_PUBLIC_PORT, raising=False)
    assert break_glass.public_port() == 0
    assert break_glass.serve_in_background() is None


def test_a_nonsense_port_is_off_rather_than_a_default(monkeypatch):
    monkeypatch.setenv(break_glass.ENV_PUBLIC_PORT, "not-a-port")
    assert break_glass.public_port() == 0


def test_the_default_bind_is_loopback():
    """Reaching this from another host is a separate decision an operator makes
    on purpose, and one that needs TLS: Basic credentials travel in the clear on
    every request."""
    assert break_glass.DEFAULT_BIND == "127.0.0.1"


# --- authentication ----------------------------------------------------------

def test_the_console_needs_the_administration_token(page):
    assert page.get("/").status_code == 401


def test_a_console_password_outside_ascii_is_a_wrong_password_and_not_an_error(page):
    assert page.get("/", headers={"Authorization": "Basic YWRtaW46Y2Fmw6k="}).status_code == 401  # admin:café


def test_the_job_token_does_not_open_the_console(page):
    """The split holds here too. The token the agent carries is not a key to the
    configuration plane, and a console is a window onto it."""
    assert page.get("/", headers=_basic("a-different-job-token")).status_code == 401


def test_with_no_administration_token_there_is_no_console(page, monkeypatch):
    monkeypatch.delenv(executor_app.ENV_ADMIN_TOKEN, raising=False)
    assert page.get("/", headers=_basic(ADMIN_TOKEN)).status_code == 401


def test_the_right_token_gets_the_page(page):
    response = page.get("/", headers=_basic(ADMIN_TOKEN))
    assert response.status_code == 200
    assert b"Ansible executor" in response.data


def test_the_token_is_not_printed_on_the_page(page):
    assert ADMIN_TOKEN.encode() not in page.get("/", headers=_basic(ADMIN_TOKEN)).data


# --- read-only ---------------------------------------------------------------

def test_there_is_no_route_that_changes_anything():
    """The control is the absence of the route, not a check inside one.

    The plan sketched a button here that could run a catalogue read. It is not
    built: browsers attach cached Basic credentials to cross-origin form posts,
    so any page anywhere could have made this executor talk to a device. Reads
    are free by design and the impact would have been small, but "small" is not
    the standard for the one workload that can reach the estate.
    """
    methods = {
        method
        for rule in break_glass.console.url_map.iter_rules()
        for method in rule.methods or ()
    }
    assert not (methods & {"POST", "PUT", "PATCH", "DELETE"}), methods


def test_a_job_page_shows_its_commands(page, monkeypatch):
    executor_store.store().record(
        {
            "job_id": "abc",
            "source": "agent",
            "actor": "asha",
            "playbook": "changes/ios_vlan_name",
            "catalogue_commit": "unpinned",
            "variables": {},
            "limit_pattern": "core-sw-1",
            "check_mode": True,
            "status": "succeeded",
            "exit_code": 0,
            # Now, not epoch: retention prunes anything older than its window, so
            # a fixture dated 1970 is deleted by the write that creates it.
            "started_at": time.time(),
            "finished_at": time.time(),
            "events": [{"commands": ["vlan 40", "name finance"]}],
            "output": "ok",
            "error": "",
            "change_id": "",
        }
    )
    body = page.get("/jobs/abc", headers=_basic(ADMIN_TOKEN)).get_data(as_text=True)
    assert "vlan 40" in body and "name finance" in body
    # The fields the templates name, which are the API's and not the store's.
    # Jinja renders an unknown name as empty, so a mismatch here shows as a
    # blank column and nothing reports it -- which is how it got shipped once.
    assert "changes/ios_vlan_name" in body
    assert "core-sw-1" in body


def test_the_job_table_shows_the_playbook_and_the_targets(page):
    executor_store.store().record(
        {
            "job_id": "abc", "source": "agent", "actor": "asha",
            "playbook": "changes/ios_vlan_name", "catalogue_commit": "unpinned",
            "variables": {}, "limit_pattern": "core-sw-1", "check_mode": True,
            "status": "succeeded", "exit_code": 0,
            "started_at": time.time(), "finished_at": time.time(),
            "events": [], "output": "", "error": "", "change_id": "",
        }
    )
    body = page.get("/", headers=_basic(ADMIN_TOKEN)).get_data(as_text=True)
    assert "changes/ios_vlan_name" in body
    assert "core-sw-1" in body


def test_a_job_that_was_pruned_says_so(page):
    response = page.get("/jobs/nope", headers=_basic(ADMIN_TOKEN))
    assert response.status_code == 404
    assert b"pruned" in response.data


# --- the shell wrapper -------------------------------------------------------
#
# What it refuses is the interesting part. What it cannot refuse is the point of
# the comment at the top of executor/cli.py: a shell in this container reaches
# ansible-playbook directly, and nothing here would know.

def test_the_wrapper_validates_with_the_catalogue_rather_than_its_own_rules(monkeypatch, tmp_path):
    """A shell script that reimplemented variable validation would be a second
    answer to what a variable may contain, and the second answer is always the
    one that is wrong."""
    from executor import cli

    executor_store.reset_for_tests(str(tmp_path / "jobs.sqlite"))
    with pytest.raises(SystemExit):
        cli.run(["changes/ios_vlan_name", "--limit", "core-sw-1", "-e", "vlan_id=99999"])


def test_the_wrapper_refuses_to_run_without_a_limit(tmp_path):
    """A person at a shell who forgot --limit is about to run against the whole
    inventory. The API caps a job's device count; this refuses outright."""
    from executor import cli

    executor_store.reset_for_tests(str(tmp_path / "jobs.sqlite"))
    with pytest.raises(SystemExit):
        cli.run(["reads/device_facts"])


def test_the_wrapper_refuses_an_entry_that_is_not_in_the_catalogue(tmp_path):
    from executor import cli

    executor_store.reset_for_tests(str(tmp_path / "jobs.sqlite"))
    with pytest.raises(SystemExit):
        cli.run(["reads/../../etc/passwd", "--limit", "x"])


def test_a_shell_run_is_recorded_as_one(monkeypatch, tmp_path):
    """`source: cli` is why the console can show a shell run beside an agent one
    -- and why a history with no cli rows is not proof that nobody used a shell."""
    from executor import cli

    executor_store.reset_for_tests(str(tmp_path / "jobs.sqlite"))
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: type("Done", (), {"returncode": 0})()
    )
    with pytest.raises(SystemExit):
        cli.run(["reads/device_facts", "--limit", "core-sw-1"])
    (row,) = executor_store.store().list()
    assert row["source"] == "cli"
    assert row["limit_pattern"] == "core-sw-1"
    assert row["status"] == "succeeded"
