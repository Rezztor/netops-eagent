"""The settings handler's contract for the two executor tokens.

The deployment injects both tokens into the application (ADR 0036). Where it
keeps the administration token back, that one has to be pasted in Settings, and
a save that only wrote the job token left Network → Devices unable to talk to
the configuration plane.
"""

from __future__ import annotations

import asyncio

import pytest

from helpers import dotenv, files
from helpers import plugins as plugin_helpers
from plugins._ea_ansible.api.ansible_config import AnsibleConfig
from plugins._ea_ansible.helpers.config import DEFAULT_ADMIN_TOKEN_KEY, DEFAULT_TOKEN_KEY


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plugins" / "_ea_ansible").mkdir(parents=True, exist_ok=True)
    (tmp_path / "usr" / "plugins" / "_ea_ansible").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(plugin_helpers, "get_toggle_state", lambda name: "enabled")
    yield tmp_path


def _handler():
    return AnsibleConfig.__new__(AnsibleConfig)


def _run(coro):
    return asyncio.run(coro)


def test_get_reports_both_tokens_set_but_never_their_values(instance, monkeypatch):
    def fake_get(key, default=None):
        values = {
            DEFAULT_TOKEN_KEY: "the-job-secret",
            DEFAULT_ADMIN_TOKEN_KEY: "the-admin-secret",
        }
        return values.get(key, default)

    monkeypatch.setattr(dotenv, "get_dotenv_value", fake_get)
    result = _run(_handler().process({"action": "get"}, None))
    assert result["ok"] is True
    assert result["config"]["executor_token_set"] is True
    assert result["config"]["admin_token_set"] is True
    assert "the-job-secret" not in str(result)
    assert "the-admin-secret" not in str(result)


def test_save_with_an_admin_token_writes_it_through_dotenv(instance, monkeypatch):
    """This is the call Network → Devices depends on. Without it the screen
    reports that no administration token is set, even after Settings is saved."""
    calls = []
    monkeypatch.setattr(dotenv, "save_dotenv_value", lambda key, value: calls.append((key, value)))
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "new-admin")

    result = _run(
        _handler().process(
            {"action": "save", "config": {}, "admin_token": "new-admin"}, None
        )
    )
    assert (DEFAULT_ADMIN_TOKEN_KEY, "new-admin") in calls
    assert "new-admin" not in str(result["config"])
    assert result["config"]["admin_token_set"] is True


def test_save_with_a_blank_admin_token_does_not_touch_the_stored_one(instance, monkeypatch):
    calls = []
    monkeypatch.setattr(dotenv, "save_dotenv_value", lambda key, value: calls.append((key, value)))
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "unchanged")

    result = _run(
        _handler().process(
            {
                "action": "save",
                "config": {"executor_url": "http://ansible:8500"},
                "admin_token": "",
            },
            None,
        )
    )
    assert result["ok"] is True
    assert all(key != DEFAULT_ADMIN_TOKEN_KEY for key, _ in calls)


def test_the_reported_origin_distinguishes_the_deployment_from_a_saved_copy(monkeypatch):
    """usr/.env overrides the process environment, so the two sources can
    disagree and the disagreement is invisible from the interface.

    That is the case worth naming: an administration token pasted once keeps
    authenticating after the deployment rotates the Secret, and nothing about a
    field reading "•••••••• stored" says which of the two values is live.
    """
    from plugins._ea_ansible.helpers import config as plugin_config

    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "a-token")

    monkeypatch.setattr(dotenv, "value_origin", lambda key: dotenv.ORIGIN_ENVIRONMENT)
    injected = plugin_config.public_config()
    assert injected["admin_token_origin"] == dotenv.ORIGIN_ENVIRONMENT
    assert injected["executor_token_origin"] == dotenv.ORIGIN_ENVIRONMENT

    monkeypatch.setattr(dotenv, "value_origin", lambda key: dotenv.ORIGIN_FILE)
    assert plugin_config.public_config()["admin_token_origin"] == dotenv.ORIGIN_FILE


def test_the_origin_is_reported_without_the_token(monkeypatch):
    """Adding a field to this payload is the moment a token leaks into it."""
    from plugins._ea_ansible.helpers import config as plugin_config

    # Only the token keys, or the stub also answers for `executor_url` and for
    # the key *names*, and the payload contains the string for reasons that have
    # nothing to do with a leak.
    def only_the_tokens(key, default=None):
        if key in (DEFAULT_TOKEN_KEY, DEFAULT_ADMIN_TOKEN_KEY):
            return "the-admin-secret"
        return default

    monkeypatch.setattr(dotenv, "get_dotenv_value", only_the_tokens)
    monkeypatch.setattr(dotenv, "value_origin", lambda key: dotenv.ORIGIN_ENVIRONMENT)
    payload = plugin_config.public_config()
    assert payload["admin_token_set"] is True, "the stub must actually set one"
    assert "the-admin-secret" not in str(payload)
