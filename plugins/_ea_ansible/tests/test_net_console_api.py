"""The endpoints the console reads and writes through.

Two permissions, deliberately different. Reading what ran is `network.view`: an
engineer who needs to see what was pushed to a switch last Tuesday should not
need the permission that lets them point the agent at a different executor.
Editing the inventory and the credentials is `plugins.manage`, alongside
configuring the executor, because both decide what the agent can reach.

The other property worth a test is that the secret only travels one way. The
executor has no endpoint that gives a credential back, so neither does this, and
the test asserts that rather than trusting the layer below to keep being careful.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from helpers import api_policy, rbac

from plugins._ea_ansible.api.network_inventory import NetworkInventory
from plugins._ea_ansible.api.network_jobs import NetworkJobs
from plugins._ea_ansible.helpers import admin_client, executor_client


def call(handler_class: Any, payload: dict) -> dict:
    handler = handler_class(app=None, thread_lock=None)  # type: ignore[arg-type]
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        handler.process(payload, None)
    )


# --- the permissions ---------------------------------------------------------

def test_reading_what_ran_does_not_require_administering_the_plugin():
    required = api_policy.required_permission_for("plugins/_ea_ansible/network_jobs")
    assert required == rbac.PERM_NETWORK_VIEW
    assert required != rbac.PERM_PLUGINS_MANAGE


def test_the_permission_is_declared_in_one_place_only():
    """A handler's own required_permission() wins over the table. Declaring it
    in both would mean two answers to one question, and a table that has quietly
    stopped being the place to look."""
    assert NetworkJobs.required_permission() is None


def test_editing_the_inventory_requires_administering_the_plugin():
    """Not on the handler, but in the central table -- which is where a reader
    checking "what can this account reach" actually looks."""
    assert (
        api_policy.required_permission_for("plugins/_ea_ansible/network_inventory")
        == rbac.PERM_PLUGINS_MANAGE
    )


def test_a_power_user_can_read_history_and_cannot_edit_the_inventory():
    permissions = rbac.ROLES[rbac.ROLE_POWER]["permissions"]
    assert rbac.PERM_NETWORK_VIEW in permissions
    assert rbac.PERM_PLUGINS_MANAGE not in permissions


def test_a_power_user_can_approve_and_a_standard_user_cannot():
    """ADR 0039: the requesting engineer approves their own change."""
    assert rbac.PERM_NETWORK_APPROVE in rbac.ROLES[rbac.ROLE_POWER]["permissions"]
    assert rbac.PERM_NETWORK_APPROVE not in rbac.ROLES[rbac.ROLE_USER]["permissions"]


def test_a_standard_user_sees_none_of_it():
    permissions = rbac.ROLES[rbac.ROLE_USER]["permissions"]
    assert rbac.PERM_NETWORK_VIEW not in permissions
    assert rbac.PERM_TOOL_NETWORK not in permissions


# --- the jobs endpoint -------------------------------------------------------

def test_the_listing_passes_its_filters_through(monkeypatch):
    seen: dict = {}

    def fake_jobs(agent=None, **kwargs):
        seen.update(kwargs)
        return {"jobs": [], "total": 0}

    monkeypatch.setattr(executor_client, "jobs", fake_jobs)
    result = call(NetworkJobs, {"status": "failed", "limit": 10})
    assert result["ok"] is True
    assert seen["status"] == "failed" and seen["limit"] == 10


def test_asking_for_one_job_asks_the_executor_for_that_job(monkeypatch):
    monkeypatch.setattr(executor_client, "poll", lambda agent, job_id: {"job_id": job_id})
    assert call(NetworkJobs, {"job_id": "abc"})["job"]["job_id"] == "abc"


def test_an_unreachable_executor_is_reported_not_raised(monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise executor_client.ExecutorUnreachable("nothing answered")

    monkeypatch.setattr(executor_client, "jobs", unreachable)
    result = call(NetworkJobs, {})
    assert result["ok"] is False
    assert "nothing answered" in result["error"]


# --- the inventory endpoint --------------------------------------------------

def test_reading_returns_names_and_fingerprints_and_no_secret(monkeypatch):
    monkeypatch.setattr(
        admin_client, "get_inventory", lambda *a, **k: {"inventory": {}, "unresolved_credentials": []}
    )
    monkeypatch.setattr(
        admin_client,
        "list_credentials",
        lambda *a, **k: [{"name": "core", "kind": "password", "fingerprint": "abc123"}],
    )
    result = call(NetworkInventory, {"action": "get"})
    assert result["credentials"][0]["fingerprint"] == "abc123"
    assert "secret" not in str(result)


def test_a_blank_secret_changes_nothing_rather_than_clearing(monkeypatch):
    """The field starts empty on every load, so blank has to mean "leave it
    alone". Clearing a credential is deleting it, which is a different action."""
    called = []
    monkeypatch.setattr(
        admin_client, "put_credential", lambda *a, **k: called.append(k) or {}
    )
    result = call(NetworkInventory, {"action": "save_credential", "name": "core", "secret": ""})
    assert result["ok"] is False
    assert not called


def test_saving_a_credential_does_not_echo_it_back(monkeypatch):
    monkeypatch.setattr(
        admin_client,
        "put_credential",
        lambda *a, **k: {"name": "core", "fingerprint": "abc123", "kind": "password"},
    )
    result = call(
        NetworkInventory,
        {"action": "save_credential", "name": "core", "secret": "hunter2-horse", "kind": "password"},
    )
    assert "hunter2-horse" not in str(result)


def test_an_inventory_that_is_not_an_object_is_refused_here_too():
    result = call(NetworkInventory, {"action": "save_inventory", "inventory": "core-switches"})
    assert result["ok"] is False


def test_an_unknown_action_is_refused_rather_than_defaulting():
    assert call(NetworkInventory, {"action": "drop_everything"})["ok"] is False


def test_the_executors_own_refusal_is_what_the_screen_shows(monkeypatch):
    """The executor's messages are written to be read by a person. Replacing
    them with a generic one here would throw away the useful half."""
    def refused(*_args, **_kwargs):
        raise admin_client.ExecutorRefused(
            "group core names credential 'core-login', which is not stored here."
        )

    monkeypatch.setattr(admin_client, "put_inventory", refused)
    result = call(NetworkInventory, {"action": "save_inventory", "inventory": {}})
    assert "not stored here" in result["error"]
