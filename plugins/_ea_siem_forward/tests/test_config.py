"""Configuration coercion.

Every value here ends up either in a packet header or in the address it is sent
to, so a bad stored value must degrade to the shipped default rather than
produce a malformed line or a send to somewhere unintended.
"""

from __future__ import annotations

import pytest

from plugins._ea_siem_forward.helpers import config as plugin_config


@pytest.fixture
def stored(monkeypatch):
    def _set(**values):
        monkeypatch.setattr(
            plugin_config.plugin_helpers, "get_default_plugin_config", lambda name: {}
        )
        monkeypatch.setattr(
            plugin_config.plugin_helpers, "get_plugin_config", lambda name: dict(values)
        )
    return _set


def test_defaults_are_inert(stored):
    stored()
    config = plugin_config.get_config()
    assert config["enabled"] is False
    assert config["host"] == ""
    assert config["port"] == 514


@pytest.mark.parametrize("bad", ["not a port", -1, 0, 70000, None, True])
def test_a_bad_port_falls_back_to_514(stored, bad):
    stored(port=bad)
    assert plugin_config.get_config()["port"] == 514


@pytest.mark.parametrize("bad", [-1, 24, "local0", None])
def test_a_bad_facility_falls_back_to_local0(stored, bad):
    stored(facility=bad)
    assert plugin_config.get_config()["facility"] == 16


def test_an_unknown_syslog_format_falls_back_rather_than_being_sent_as_is(stored):
    stored(syslog_format="rfc9999")
    assert plugin_config.get_config()["syslog_format"] == "rfc3164"


def test_a_known_syslog_format_is_accepted_case_insensitively(stored):
    stored(syslog_format="RFC5424")
    assert plugin_config.get_config()["syslog_format"] == "rfc5424"


def test_min_severity_stays_inside_cefs_own_scale(stored):
    stored(min_severity=99)
    assert plugin_config.get_config()["min_severity"] == 0


def test_vendor_and_product_are_truncated_to_cefs_documented_cap(stored):
    stored(device_vendor="x" * 200, device_product="y" * 200)
    config = plugin_config.get_config()
    assert len(config["device_vendor"]) == 63
    assert len(config["device_product"]) == 63


def test_an_empty_vendor_falls_back_rather_than_emitting_an_empty_header_field(stored):
    stored(device_vendor="   ")
    assert plugin_config.get_config()["device_vendor"] == "Rezztor"


def test_host_is_stripped(stored):
    stored(host="  10.0.0.1  ")
    assert plugin_config.get_config()["host"] == "10.0.0.1"


def test_is_configured_needs_both_the_switch_and_a_destination(stored):
    stored(enabled=True, host="")
    assert plugin_config.is_configured() is False

    stored(enabled=False, host="10.0.0.1")
    assert plugin_config.is_configured() is False

    stored(enabled=True, host="10.0.0.1")
    assert plugin_config.is_configured() is True


def test_device_version_is_read_live_and_never_empty(monkeypatch):
    from helpers import git

    monkeypatch.setattr(git, "get_version", lambda: "")
    assert plugin_config.device_version() == "unknown"

    monkeypatch.setattr(git, "get_version", lambda: "v1.2.3")
    assert plugin_config.device_version() == "v1.2.3"

    def boom():
        raise RuntimeError("no git")

    monkeypatch.setattr(git, "get_version", boom)
    assert plugin_config.device_version() == "unknown"
