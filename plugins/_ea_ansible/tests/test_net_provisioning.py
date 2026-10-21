"""Configuring and enabling the plugin from what the deployment already shipped.

The setup guide called these two switches on purpose -- the container is a
deployment decision, the plugin a runtime one. That holds in one direction: a
plugin enabled with no container gives clear errors instead of a silent no-op.
It does not hold in the other. An operator who ran the executor has already
decided, and a settings form that makes them decide again is a manual step with
no decision in it.

So the deployment's answer is the default, and the administrator's answer, once
given, is never overwritten.
"""

from __future__ import annotations

import pytest

from helpers import cache, files, plugins
from plugins._ea_ansible.helpers import config as plugin_config
from plugins._ea_ansible.helpers import provisioning


@pytest.fixture
def instance(tmp_path, monkeypatch):
    """A instance root with a real plugin directory under it.

    `plugin.yaml` has to exist: `get_toggle_state` reads the manifest first and
    reports "disabled" for a plugin it cannot find, so without it every
    assertion here would pass for the wrong reason. It ships `.toggle-0`, which
    is the default this whole module exists to reconsider.
    """
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr" / "plugins" / plugin_config.PLUGIN_NAME).mkdir(parents=True)

    base = tmp_path / "plugins" / plugin_config.PLUGIN_NAME
    base.mkdir(parents=True)
    (base / "plugin.yaml").write_text(
        f"name: {plugin_config.PLUGIN_NAME}\ntitle: Network Automation\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (base / plugins.DISABLED_FILE_NAME).write_text("", encoding="utf-8")

    monkeypatch.delenv(plugin_config.ENV_EXECUTOR_URL, raising=False)
    monkeypatch.delenv(plugin_config.DEFAULT_TOKEN_KEY, raising=False)
    yield tmp_path
    # The plugin list is cached on first read. Left behind, it names only this
    # tmp instance's plugin and every later test sees no other plugin enabled.
    for area in ("*(plugins)*", "*(extensions)*", "*(api)*"):
        cache.clear(area)


def _deployment_ships_an_executor(monkeypatch):
    monkeypatch.setenv(plugin_config.ENV_EXECUTOR_URL, "http://ansible:8500")
    monkeypatch.setenv(plugin_config.DEFAULT_TOKEN_KEY, "a-job-token")


# --- the address -------------------------------------------------------------


def test_the_executor_address_comes_from_the_deployment_when_none_is_saved(monkeypatch):
    monkeypatch.setenv(plugin_config.ENV_EXECUTOR_URL, "http://ansible:8500")
    assert plugin_config.get_config()["executor_url"] == "http://ansible:8500"


def test_a_saved_address_beats_the_deployment(monkeypatch):
    """The environment is a default, not an override. An administrator who
    pointed this instance at a different executor meant it."""
    monkeypatch.setenv(plugin_config.ENV_EXECUTOR_URL, "http://ansible:8500")
    monkeypatch.setattr(
        plugins, "get_plugin_config", lambda *a, **k: {"executor_url": "http://elsewhere:9000"}
    )
    assert plugin_config.get_config()["executor_url"] == "http://elsewhere:9000"


def test_a_saved_old_default_address_is_ignored(monkeypatch):
    monkeypatch.setenv(plugin_config.ENV_EXECUTOR_URL, "unix:///run/x.sock")
    monkeypatch.setattr(
        plugins, "get_plugin_config", lambda *a, **k: {"executor_url": " http://ansible:8500/ "}
    )
    assert plugin_config.get_config()["executor_url"] == "unix:///run/x.sock"


def test_no_deployment_and_no_saved_address_is_still_unconfigured(monkeypatch):
    monkeypatch.delenv(plugin_config.ENV_EXECUTOR_URL, raising=False)
    assert plugin_config.get_config()["executor_url"] == ""


# --- the switch --------------------------------------------------------------


def test_shipping_an_executor_enables_the_plugin(instance, monkeypatch):
    _deployment_ships_an_executor(monkeypatch)
    assert provisioning.enable_if_the_deployment_ships_an_executor() is True
    assert plugins.get_toggle_state(plugin_config.PLUGIN_NAME) == "enabled"


def test_no_executor_leaves_the_plugin_alone(instance, monkeypatch):
    """The half of the two-switch rule that earns its keep: on with no container
    is a plugin that cannot work."""
    assert provisioning.enable_if_the_deployment_ships_an_executor() is False


def test_an_address_with_no_token_does_not_enable_it(instance, monkeypatch):
    """Enabling here would produce a plugin that is on and cannot authenticate,
    which is worse than off -- the error would point at the executor rather than
    at the missing token."""
    monkeypatch.setenv(plugin_config.ENV_EXECUTOR_URL, "http://ansible:8500")
    monkeypatch.delenv(plugin_config.DEFAULT_TOKEN_KEY, raising=False)
    assert provisioning.enable_if_the_deployment_ships_an_executor() is False


def test_an_administrator_who_turned_it_off_keeps_it_off(instance, monkeypatch):
    """The whole reason this runs only once. A restart must not undo a decision,
    and every boot would otherwise re-enable what somebody deliberately
    disabled."""
    _deployment_ships_an_executor(monkeypatch)
    plugins.toggle_plugin(plugin_config.PLUGIN_NAME, False, project_name="", agent_profile="")

    assert provisioning.enable_if_the_deployment_ships_an_executor() is False
    assert plugins.get_toggle_state(plugin_config.PLUGIN_NAME) == "disabled"


def test_running_twice_is_not_a_second_decision(instance, monkeypatch):
    _deployment_ships_an_executor(monkeypatch)
    assert provisioning.enable_if_the_deployment_ships_an_executor() is True
    assert provisioning.enable_if_the_deployment_ships_an_executor() is False
