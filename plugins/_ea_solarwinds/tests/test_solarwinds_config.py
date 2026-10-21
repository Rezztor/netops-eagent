import pytest

from plugins._ea_solarwinds.helpers import config


def test_the_shipped_default_has_no_host_so_it_is_unconfigured(monkeypatch):
    shipped = {}
    monkeypatch.setattr("helpers.plugins.get_default_plugin_config", lambda *a, **k: shipped)
    monkeypatch.setattr("helpers.plugins.get_plugin_config", lambda *a, **k: {})
    resolved = config.get_config()
    assert resolved["host"] == ""
    assert resolved["port"] == config._FALLBACK["port"]


@pytest.mark.parametrize("key", config._INT_KEYS)
def test_a_nonsense_number_falls_back_rather_than_producing_zero(key, monkeypatch):
    monkeypatch.setattr("helpers.plugins.get_plugin_config", lambda *a, **k: {key: "not a number"})
    assert config.get_config()[key] == config._FALLBACK[key]


@pytest.mark.parametrize("key", config._INT_KEYS)
def test_a_zero_or_negative_value_falls_back(key, monkeypatch):
    """A zero row cap, timeout or rate limit would not mean 'none' -- it would
    mean every call fails or every call is refused. Falling back is the safe
    reading, the same choice test_solarwinds_capabilities.py's row-cap test
    depends on."""
    monkeypatch.setattr("helpers.plugins.get_plugin_config", lambda *a, **k: {key: 0})
    assert config.get_config()[key] == config._FALLBACK[key]


def test_verify_tls_survives_yaml_reading_it_as_a_real_boolean(monkeypatch):
    monkeypatch.setattr("helpers.plugins.get_plugin_config", lambda *a, **k: {"verify_tls": False})
    assert config.get_config()["verify_tls"] is False


def test_verify_tls_defaults_on_so_a_missing_value_is_the_safe_direction(monkeypatch):
    monkeypatch.setattr("helpers.plugins.get_plugin_config", lambda *a, **k: {})
    monkeypatch.setattr("helpers.plugins.get_default_plugin_config", lambda *a, **k: {})
    assert config.get_config()["verify_tls"] is True


def test_host_and_username_are_trimmed(monkeypatch):
    monkeypatch.setattr(
        "helpers.plugins.get_plugin_config",
        lambda *a, **k: {"host": "  orion.example.com  ", "username": " svc "},
    )
    resolved = config.get_config()
    assert resolved["host"] == "orion.example.com"
    assert resolved["username"] == "svc"


def test_is_configured_requires_host_username_and_a_stored_password(monkeypatch):
    from helpers import dotenv

    monkeypatch.setattr(
        config, "get_config",
        lambda: {"host": "orion.example.com", "username": "svc"},
    )
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "")
    assert config.is_configured() is False

    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "secret")
    assert config.is_configured() is True


def test_is_configured_is_false_with_no_host_even_if_a_password_is_set(monkeypatch):
    from helpers import dotenv

    monkeypatch.setattr(config, "get_config", lambda: {"host": "", "username": "svc"})
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda *a, **k: "secret")
    assert config.is_configured() is False


def test_is_enabled_reflects_the_plugin_toggle(monkeypatch):
    monkeypatch.setattr("helpers.plugins.get_toggle_state", lambda name: "enabled")
    assert config.is_enabled() is True
    monkeypatch.setattr("helpers.plugins.get_toggle_state", lambda name: "disabled")
    assert config.is_enabled() is False


def test_is_enabled_fails_closed_if_the_toggle_check_itself_errors(monkeypatch):
    def raise_error(name):
        raise RuntimeError("plugin metadata unreadable")

    monkeypatch.setattr("helpers.plugins.get_toggle_state", raise_error)
    assert config.is_enabled() is False
