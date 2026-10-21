"""Settings resolution: defaults, coercion of whatever was stored, and what counts as configured."""

from __future__ import annotations

import pytest

from plugins._ea_azure.helpers import config, credential
from plugins._ea_azure.tests.fixtures.env import install


@pytest.fixture
def env(monkeypatch, tmp_path):
    return install(monkeypatch, tmp_path)


def test_defaults_are_the_documented_ones(env):
    cfg = config.get_config()
    assert (cfg["cloud"], cfg["auth"], cfg["subscriptions_mode"], cfg["page_size"], cfg["keep_removed_days"]) == ("public", "certificate", "all", 1000, 90)
    assert cfg["schedule"] == {"frequency": "weekly", "weekday": "sat", "time": "02:00", "timezone": "UTC"}


def test_bad_stored_values_fall_back_instead_of_raising(env):
    config.save({
        "cloud": "gov", "auth": "password", "subscriptions_mode": "some", "page_size": "lots", "keep_removed_days": -5,
        "subscription_ids": ["a", " a ", "", "b", 7],
        "schedule": {"frequency": "hourly", "weekday": "someday", "time": "25:99", "timezone": "Mars/Base"},
    })
    cfg = config.get_config()
    assert (cfg["cloud"], cfg["auth"], cfg["subscriptions_mode"], cfg["page_size"], cfg["keep_removed_days"]) == ("public", "certificate", "all", 1000, 90)
    assert cfg["subscription_ids"] == ["a", "b", "7"]
    assert cfg["schedule"] == {"frequency": "weekly", "weekday": "sat", "time": "02:00", "timezone": "UTC"}


def test_a_good_schedule_survives(env):
    config.save({"schedule": {"frequency": "daily", "weekday": "Monday", "time": "04:30", "timezone": "Asia/Dubai"}})
    assert config.get_config()["schedule"] == {"frequency": "daily", "weekday": "mon", "time": "04:30", "timezone": "Asia/Dubai"}


def test_configured_needs_both_ids_and_a_credential(env):
    assert not config.is_configured()
    config.save({"tenant_id": "t", "client_id": "c"})
    assert not config.is_configured()
    credential.set_secret("s3cret", "")
    assert config.is_configured()
    config.save({"client_id": ""})
    assert not config.is_configured()


def test_the_config_never_carries_the_credential(env):
    credential.set_secret("s3cret-value", "2027-01-01")
    assert "s3cret-value" not in str(config.get_config())


def test_enabled_follows_the_toggle(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, enabled=False)
    assert config.is_enabled() is False
