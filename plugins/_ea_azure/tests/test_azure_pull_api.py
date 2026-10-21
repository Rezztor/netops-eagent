"""`azure_pull` and `azure_status`: who may call them, what they answer, and that no credential comes back."""

from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest

from helpers import api_policy, login, rbac
from plugins._ea_azure.api.azure_pull import AzurePull
from plugins._ea_azure.api.azure_status import AzureStatus
from plugins._ea_azure.helpers import client, collect, config, credential
from plugins._ea_azure.tests.fake_azure import FakeAzure
from plugins._ea_azure.tests.fixtures.env import install
from plugins._ea_azure.tests.fixtures.tenant import CLIENT_ID, TENANT_ID, hand_tenant

SECRET = "fake-client-secret"


def call(handler_class):
    return asyncio.run(handler_class.__new__(handler_class).process({}, None))


@pytest.fixture
def world(database, monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    monkeypatch.setattr(login, "current_username", lambda: "engineer")
    config.save({"tenant_id": TENANT_ID, "client_id": CLIENT_ID})
    credential.set_secret(SECRET, "")
    with FakeAzure(hand_tenant()) as fake:
        monkeypatch.setattr(client, "TEST_ENDPOINTS", fake.cloud)
        client.reset()
        yield fake


def finish():
    deadline = time.monotonic() + 30
    while collect.status()["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
    return collect.status()["last_run"]


def test_permissions():
    for name, perm in (("azure_pull", rbac.PERM_INVENTORY_MANAGE), ("azure_status", rbac.PERM_INVENTORY_VIEW)):
        assert api_policy.required_permission_for(f"plugins/_ea_azure/{name}") == perm
    assert rbac.has_permission(rbac.ROLE_ADMIN, rbac.PERM_INVENTORY_MANAGE)
    assert not rbac.has_permission(rbac.ROLE_USER, rbac.PERM_INVENTORY_MANAGE)
    assert not rbac.has_permission(rbac.ROLE_USER, rbac.PERM_INVENTORY_VIEW)


def test_pull_starts_one_and_refuses_the_next_within_the_gap(world):
    first = call(AzurePull)
    assert first["ok"] and first["run_id"]
    last = finish()
    assert last["id"] == first["run_id"] and last["status"] == "succeeded" and last["requested_by"] == "engineer"
    again = call(AzurePull)
    assert again["ok"] is False and again["reason"] == "too_soon" and "15 minutes" in again["error"]


def test_pull_refuses_when_busy_off_or_not_set_up(world, monkeypatch):
    run = collect.inv.start_run("azure", "someone")
    assert call(AzurePull)["reason"] == "busy"
    collect.inv.finish_run(run, "failed")
    monkeypatch.setattr(config, "is_enabled", lambda: False)
    assert call(AzurePull)["reason"] == "disabled"
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    credential.clear()
    out = call(AzurePull)
    assert out["ok"] is False and out["reason"] == "not_configured"


def test_status_before_and_after_a_pull(world):
    s = call(AzureStatus)
    assert (s["ok"], s["enabled"], s["configured"], s["has_data"], s["test_endpoints"]) == (True, True, True, False, True)
    assert (s["running"], s["last_run"], s["verified_against_real_tenant"]) == (None, None, True)
    assert s["credential"] == {"kind": "secret", "expires_on": "", "expires_in_days": None}
    assert dt.datetime.fromisoformat(s["next_pull_at"]) > dt.datetime.now(dt.timezone.utc)
    assert dt.datetime.fromisoformat(s["next_pull_at"]).weekday() == 5      # Saturday
    call(AzurePull)
    finish()
    s = call(AzureStatus)
    assert s["has_data"] and s["last_run"]["status"] == "succeeded" and s["running"] is None
    assert len(s["as_of"]["subscriptions"]) == 3 and s["as_of"]["last_pull"]["id"] == s["last_run"]["id"]
    assert SECRET not in str(s)


def test_status_serves_stored_data_with_the_plugin_off(world, monkeypatch):
    call(AzurePull)
    finish()
    monkeypatch.setattr(config, "is_enabled", lambda: False)
    s = call(AzureStatus)
    assert s["enabled"] is False and s["has_data"] and s["next_pull_at"] is None and s["as_of"]["last_pull"]


def test_status_counts_the_days_to_a_secrets_expiry(world):
    config.save({"secret_expires_on": (dt.date.today() + dt.timedelta(days=10)).isoformat()})
    assert call(AzureStatus)["credential"]["expires_in_days"] == 10
    config.save({"secret_expires_on": "someday"})
    assert call(AzureStatus)["credential"]["expires_in_days"] is None
