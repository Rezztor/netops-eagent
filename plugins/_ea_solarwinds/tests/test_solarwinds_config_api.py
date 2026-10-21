"""The handler's own contract: the password never comes back as itself, and an
empty submission leaves whatever is stored untouched.
"""

from __future__ import annotations

import asyncio

import pytest

from helpers import dotenv, files
from helpers import plugins as plugin_helpers
from plugins._ea_solarwinds.api.solarwinds_config import SolarwindsConfig
from plugins._ea_solarwinds.helpers import config


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plugins" / "_ea_solarwinds").mkdir(parents=True, exist_ok=True)
    (tmp_path / "usr" / "plugins" / "_ea_solarwinds").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(plugin_helpers, "get_toggle_state", lambda name: "enabled")
    yield tmp_path


def _handler():
    return SolarwindsConfig.__new__(SolarwindsConfig)


def _run(coro):
    return asyncio.run(coro)


def test_get_reports_password_set_but_never_the_password(instance, monkeypatch):
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "the-real-secret")
    result = _run(_handler().process({"action": "get"}, None))
    assert result["ok"] is True
    assert result["password_set"] is True
    assert "the-real-secret" not in str(result)


def test_get_with_no_password_reports_it_unset(instance, monkeypatch):
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "")
    result = _run(_handler().process({"action": "get"}, None))
    assert result["password_set"] is False


def test_save_with_a_blank_password_does_not_touch_the_stored_one(instance, monkeypatch):
    calls = []
    monkeypatch.setattr(dotenv, "save_dotenv_value", lambda key, value: calls.append((key, value)))
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "unchanged")

    result = _run(_handler().process(
        {"action": "save", "config": {"host": "orion.example.com", "password": ""}}, None
    ))
    assert result["ok"] is True
    assert calls == []
    assert result["config"]["host"] == "orion.example.com"


def test_save_with_a_real_password_writes_it_through_dotenv(instance, monkeypatch):
    calls = []
    monkeypatch.setattr(dotenv, "save_dotenv_value", lambda key, value: calls.append((key, value)))
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "new-secret")

    result = _run(_handler().process(
        {"action": "save", "config": {"host": "orion.example.com", "password": "new-secret"}}, None
    ))
    assert calls == [(dotenv.KEY_SOLARWINDS_PASSWORD, "new-secret")]
    assert "password" not in result["config"]
    assert result["password_set"] is True


def test_save_only_touches_editable_fields(instance, monkeypatch):
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "")
    result = _run(_handler().process(
        {"action": "save", "config": {"host": "orion.example.com", "not_a_real_field": "smuggled"}}, None
    ))
    assert "not_a_real_field" not in result["config"]


def test_save_requires_config_to_be_an_object(instance):
    result = _run(_handler().process({"action": "save", "config": "not an object"}, None))
    assert result["ok"] is False


def test_an_unknown_action_is_refused(instance):
    result = _run(_handler().process({"action": "reformat_the_disk"}, None))
    assert result["ok"] is False
