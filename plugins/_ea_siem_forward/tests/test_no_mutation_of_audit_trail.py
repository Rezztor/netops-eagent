"""This plugin, wired into the real audit stream, still cannot alter it.

`tests/test_audit_logging.py` proves the sink mechanism is additive-only in
general. This proves the specific thing registered here honours that when it is
actually attached to `audit.emit()` -- the integration, not the contract.
"""

from __future__ import annotations

import json

import pytest

from plugins._audit.helpers import audit
from plugins._ea_siem_forward.helpers import config as plugin_config
from plugins._ea_siem_forward.helpers import sender


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    saved = list(audit._sinks)
    audit._sinks.clear()
    monkeypatch.setattr(sender, "_queue", None)
    sender.reset_stats()
    yield
    audit._sinks[:] = saved
    monkeypatch.setattr(sender, "_queue", None)
    sender.reset_stats()


def _configure(monkeypatch, **overrides):
    config = {**plugin_config._FALLBACK, "enabled": True, "host": "10.0.0.1", **overrides}
    monkeypatch.setattr(plugin_config, "get_config", lambda: config)
    monkeypatch.setattr(plugin_config, "is_enabled", lambda: True)
    monkeypatch.setattr(plugin_config, "is_configured", lambda: True)
    monkeypatch.setattr(plugin_config, "device_version", lambda: "1.0")


def _logged(capsys) -> list[dict]:
    out = capsys.readouterr().out
    return [
        json.loads(line[len(audit.AUDIT_PREFIX) + 1 :])
        for line in out.splitlines()
        if line.startswith(audit.AUDIT_PREFIX)
    ]


def test_the_forwarder_sees_events_without_changing_the_record(monkeypatch, capsys):
    _configure(monkeypatch)
    audit.register_sink(sender.handle_record)

    audit.emit("auth.signin", outcome="failure", username="admin")

    logged = _logged(capsys)
    assert len(logged) == 1
    assert logged[0]["event"] == "auth.signin"
    assert logged[0]["outcome"] == "failure"

    queued = sender._q().get_nowait()
    assert "CEF:0|Rezztor|EAgent|1.0|auth.signin|" in queued
    assert "outcome=failure" in queued


def test_a_broken_forwarder_does_not_stop_the_audit_write(monkeypatch, capsys):
    """The failure that matters most: if this plugin is broken, the trail that
    ADR 0005 actually relies on must be unaffected."""
    _configure(monkeypatch)
    monkeypatch.setattr(sender, "format_record", lambda record: 1 / 0)
    audit.register_sink(sender.handle_record)

    audit.emit("account.delete", target="alice")

    assert _logged(capsys)[0]["event"] == "account.delete"


def test_a_disabled_plugin_forwards_nothing_but_audits_everything(monkeypatch, capsys):
    _configure(monkeypatch)
    monkeypatch.setattr(plugin_config, "is_enabled", lambda: False)
    audit.register_sink(sender.handle_record)

    audit.emit("auth.signin", outcome="success")

    assert _logged(capsys)[0]["event"] == "auth.signin", "the trail is unconditional"
    assert sender._q().qsize() == 0, "the forwarding is not"


def test_the_secrets_masking_has_already_happened_by_the_time_we_see_it(monkeypatch, capsys):
    """The sink runs after emit() has masked. A forwarder that re-derived the
    record instead would have to repeat the masking, and could get it wrong --
    which is the reason it is handed the finished record."""
    _configure(monkeypatch)
    audit.register_sink(sender.handle_record)

    audit.emit("settings.update", preview=audit.preview("token=SUPERSECRET"))

    queued = sender._q().get_nowait()
    logged = _logged(capsys)[0]
    # Whatever masking did or did not do to it, the forwarded copy says exactly
    # what the audit line says -- never more.
    assert logged["preview"].replace("=", "\\=") in queued
