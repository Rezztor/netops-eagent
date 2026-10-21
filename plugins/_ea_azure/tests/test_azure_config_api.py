"""`azure_config` and `azure_test`: what the Settings page can do, and that neither the secret nor the key ever
comes back out. Against the stand-in only."""

from __future__ import annotations

import asyncio

import pytest

from helpers import api_policy, dotenv, rbac
from plugins._audit.helpers import audit
from plugins._ea_azure.api.azure_config import AzureConfig
from plugins._ea_azure.api.azure_test import AzureTest
from plugins._ea_azure.helpers import client, config, credential
from plugins._ea_azure.tests.fake_azure import CLIENT_ID, TENANT_ID, FakeAzure, hand_tenant
from plugins._ea_azure.tests.fixtures.env import install

SECRET = "fake-client-secret"
EVENTS: list = []  # every audit line the test caused


def _call(handler_class, **payload):
    return asyncio.run(handler_class.__new__(handler_class).process(payload, None))


@pytest.fixture
def env(monkeypatch, tmp_path):
    store = install(monkeypatch, tmp_path)
    EVENTS.clear()
    monkeypatch.setattr(audit, "emit", lambda event, **fields: EVENTS.append((event, fields)))
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    client.reset()
    return store


@pytest.fixture
def azure(env, monkeypatch):
    with FakeAzure(hand_tenant()) as fake:
        monkeypatch.setattr(client, "TEST_ENDPOINTS", fake.cloud)
        config.save({"tenant_id": TENANT_ID, "client_id": CLIENT_ID})
        credential.set_secret(SECRET, "")
        EVENTS.clear()
        yield fake
    client.reset()


def _events(env, name):
    return [fields for event, fields in EVENTS if event == name]


# --- azure_config --------------------------------------------------------------------------------------------------

def test_get_reports_the_credential_but_never_its_value(env):
    credential.set_secret("the-real-secret", "2027-05-01")
    result = _call(AzureConfig, action="get")
    assert result["ok"] and result["enabled"] is True
    assert result["credential"] == {"kind": "secret", "set": True, "fingerprint": "", "expires_on": "2027-05-01"}
    assert "the-real-secret" not in str(result)


def test_save_stores_a_secret_through_dotenv_and_audits_without_values(env):
    result = _call(AzureConfig, action="save", secret_expires_on="2027-06-30", secret="typed-once",
                   config={"tenant_id": TENANT_ID, "client_id": CLIENT_ID, "auth": "secret", "subscriptions_mode": "only", "subscription_ids": ["x"]})
    assert env[dotenv.KEY_AZURE_CLIENT_SECRET] == "typed-once"
    assert result["credential"]["kind"] == "secret" and result["credential"]["expires_on"] == "2027-06-30"
    assert result["config"]["subscriptions_mode"] == "only" and "typed-once" not in str(result)
    (saved,) = _events(env, "azure.settings_saved")
    assert set(saved["changed"]) == {"tenant_id", "client_id", "auth", "subscriptions_mode", "subscription_ids", "secret_expires_on"}
    assert TENANT_ID not in str(saved) and "typed-once" not in str(EVENTS)
    (stored,) = _events(env, "azure.credential_set")
    assert stored == {"kind": "secret", "fingerprint": "", "expires_on": "2027-06-30"}


def test_an_empty_secret_on_save_keeps_the_stored_one(env):
    credential.set_secret("stay", "2027-01-01")
    _call(AzureConfig, action="save", secret="", config={"tenant_id": TENANT_ID})
    assert env[dotenv.KEY_AZURE_CLIENT_SECRET] == "stay"
    assert _events(env, "azure.credential_set") == []


def test_save_changes_only_the_editable_fields(env):
    result = _call(AzureConfig, action="save", config={"page_size": 5, "timeout_seconds": 1, "invented": "x", "keep_removed_days": 30})
    assert result["config"]["keep_removed_days"] == 30
    assert result["config"]["page_size"] == 1000 and result["config"]["timeout_seconds"] == 30 and "invented" not in result["config"]
    assert _call(AzureConfig, action="save", config="nope")["ok"] is False


def test_saving_nothing_new_writes_no_audit_line(env):
    _call(AzureConfig, action="save", config={})
    assert _events(env, "azure.settings_saved") == []


def test_generate_certificate_replaces_a_secret_and_returns_only_the_public_half(env):
    config.save({"tenant_id": TENANT_ID})
    credential.set_secret("old-secret", "")
    result = _call(AzureConfig, action="generate_certificate")
    assert result["ok"] and result["credential"]["kind"] == "certificate"
    assert result["public_certificate"].startswith("-----BEGIN CERTIFICATE-----") and "PRIVATE" not in result["public_certificate"]
    assert not env[dotenv.KEY_AZURE_CLIENT_SECRET]
    assert env[dotenv.KEY_AZURE_CLIENT_CERT] not in str(result) and "old-secret" not in str(result)
    (stored,) = _events(env, "azure.credential_set")
    assert stored["kind"] == "certificate" and stored["fingerprint"] == result["credential"]["fingerprint"]
    assert _call(AzureConfig, action="public_certificate")["public_certificate"] == result["public_certificate"]


def test_there_is_no_public_file_before_a_certificate_exists(env):
    result = _call(AzureConfig, action="public_certificate")
    assert result["ok"] is False and "certificate" in result["error"]


def test_clear_credential_and_unknown_actions(env):
    credential.set_secret("gone", "")
    assert _call(AzureConfig, action="clear_credential")["credential"]["set"] is False
    assert _events(env, "azure.credential_cleared") == [{}]
    assert _call(AzureConfig, action="explode")["ok"] is False


def test_no_action_ever_returns_the_secret_or_the_key(env):
    config.save({"tenant_id": TENANT_ID})
    secret = "sh0wn-never-0123"
    seen = []
    for payload in ({"action": "save", "secret": secret, "config": {}}, {"action": "get"}, {"action": "generate_certificate"},
                    {"action": "get"}, {"action": "public_certificate"}, {"action": "clear_credential"}, {"action": "save", "config": {}}):
        seen.append(str(_call(AzureConfig, **payload)))
    blob = env.get(dotenv.KEY_AZURE_CLIENT_CERT) or ""
    assert not any(secret in text or "PRIVATE KEY" in text for text in seen) and not any(blob and blob in text for text in seen)


def test_both_endpoints_need_the_manage_plugins_permission():
    for name in ("azure_config", "azure_test"):
        permission = api_policy.required_permission_for(f"plugins/_ea_azure/{name}")
        assert permission == rbac.PERM_PLUGINS_MANAGE
        assert rbac.has_permission(rbac.ROLE_ADMIN, permission) and not rbac.has_permission(rbac.ROLE_USER, permission)


# --- azure_test ----------------------------------------------------------------------------------------------------

def test_a_good_connection_lists_the_subscriptions_and_counts_resources(azure, env):
    before = config.get_config()
    result = _call(AzureTest)
    assert result["ok"] and result["signed_in"] is True and "reason" not in result
    assert [s["name"] for s in result["subscriptions"]] == ["Production", "Non-production", "Shared services"]
    assert set(result["subscriptions"][0]) == {"id", "name", "state"}
    assert result["resources"] == len(azure.tenant["resources"]) and str(result["resources"]) in result["message"]
    assert config.get_config() == before  # it stores nothing
    assert _events(env, "azure.connection_tested") == [{"result": "ok", "signed_in": True, "subscriptions": 3}]


def test_it_signs_in_afresh_every_time(azure):
    _call(AzureTest)
    _call(AzureTest)
    assert azure._token_n == 2


@pytest.mark.parametrize("switch", ["tenant", "application", "secret", "expired", "certificate"])
def test_each_failure_is_a_plain_sentence_with_its_reason(azure, switch):
    azure.sign_in_error = switch
    result = _call(AzureTest)
    assert result["ok"] and result["signed_in"] is False and result["reason"] == switch and result["subscriptions"] == []
    assert "AADSTS" in result["message"] and "simulated" not in result["message"] and SECRET not in str(result)


def test_microsoft_not_reachable(azure, monkeypatch):
    monkeypatch.setattr(client, "TEST_ENDPOINTS", client.Cloud("http://127.0.0.1:9", "http://127.0.0.1:9"))
    result = _call(AzureTest)
    assert result["signed_in"] is False and result["reason"] == "network"


def test_a_role_that_was_never_assigned_is_said_so(azure):
    azure.hide_subscription = {s["subscriptionId"] for s in azure.tenant["subscriptions"]}
    result = _call(AzureTest)
    assert result["signed_in"] is True and result["reason"] == "no_role" and "Reader" in result["message"]


def test_a_failed_count_still_returns_the_subscriptions(azure):
    azure.throttle_after, azure.throttle_count = 0, 99
    result = _call(AzureTest)
    assert result["signed_in"] is True and result["reason"] == "read" and len(result["subscriptions"]) == 3 and result["resources"] == 0


def test_not_set_up_and_switched_off_never_touch_the_network(azure, monkeypatch, tmp_path):
    credential.clear()
    assert _call(AzureTest)["reason"] == "not_configured"
    monkeypatch.setattr(config, "is_enabled", lambda: False)
    result = _call(AzureTest)
    assert result["signed_in"] is False and result["reason"] == "disabled"
    assert azure.received == []
