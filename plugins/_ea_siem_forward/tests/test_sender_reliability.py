"""The sink must never become the audited operation's problem.

Inherited straight from ADR 0005's first constraint -- "auditing never breaks
the audited operation" -- extended to forwarding it. These tests are about what
happens when the destination is wrong, gone, or slower than the events arrive.
"""

from __future__ import annotations

import queue

import pytest

from plugins._ea_siem_forward.helpers import config as plugin_config
from plugins._ea_siem_forward.helpers import sender


@pytest.fixture(autouse=True)
def _isolated_sender(monkeypatch):
    """A fresh queue and counters per test; never touch the real one."""
    monkeypatch.setattr(sender, "_queue", None)
    sender.reset_stats()
    yield
    monkeypatch.setattr(sender, "_queue", None)
    sender.reset_stats()


def _configure(monkeypatch, **overrides):
    config = {**plugin_config._FALLBACK, "enabled": True, "host": "10.0.0.1", **overrides}
    monkeypatch.setattr(plugin_config, "get_config", lambda: config)
    monkeypatch.setattr(plugin_config, "is_enabled", lambda: True)
    monkeypatch.setattr(plugin_config, "is_configured", lambda: bool(config["enabled"] and config["host"]))
    monkeypatch.setattr(plugin_config, "device_version", lambda: "1.0")
    return config


def _record(event="tool.start", **fields):
    base = {
        "v": 1,
        "ts": "2026-09-05T08:26:10.123+00:00",
        "event": event,
        "actor": {"user_id": "u1", "username": "admin", "role": "admin"},
    }
    base.update(fields)
    return base


def test_the_sink_enqueues_without_touching_a_socket(monkeypatch):
    """Whatever the destination is doing, the emitting thread only enqueues."""
    _configure(monkeypatch)

    def explode(*args, **kwargs):
        raise AssertionError("the sink must not open a socket on the caller's thread")

    monkeypatch.setattr(sender.socket, "socket", explode)

    sender.handle_record(_record())

    assert sender._q().qsize() == 1


def test_a_full_queue_drops_the_oldest_and_counts_it(monkeypatch):
    _configure(monkeypatch, queue_size=2)

    for index in range(4):
        sender.handle_record(_record(msg=f"event-{index}"))

    assert sender._q().qsize() == 2
    assert sender.stats()["dropped_queue_full"] == 2

    remaining = [sender._q().get_nowait() for _ in range(2)]
    assert "event-2" in remaining[0] and "event-3" in remaining[1], (
        "the newest events are the ones an incident responder needs"
    )


def test_the_sink_never_raises_even_when_formatting_fails(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(sender, "format_record", lambda record: 1 / 0)
    sender.handle_record(_record())  # must not raise


def test_nothing_is_queued_while_the_plugin_is_disabled(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(plugin_config, "is_enabled", lambda: False)

    sender.handle_record(_record())

    assert sender._q().qsize() == 0, "disabling the plugin stops forwarding immediately"


def test_nothing_is_queued_while_no_destination_is_configured(monkeypatch):
    _configure(monkeypatch, host="")
    sender.handle_record(_record())
    assert sender._q().qsize() == 0


def test_min_severity_filters_before_the_queue(monkeypatch):
    _configure(monkeypatch, min_severity=5)

    sender.handle_record(_record("tool.start"))               # severity 3
    assert sender._q().qsize() == 0

    sender.handle_record(_record("auth.signin", outcome="failure"))  # severity 8
    assert sender._q().qsize() == 1


def test_a_send_failure_is_counted_not_raised(monkeypatch):
    """A refused port or an unresolvable name must not kill the sender thread --
    the destination may come back, and the next event should still try."""
    _configure(monkeypatch)

    class _BadSocket:
        def __init__(self, *args, **kwargs):
            pass

        def sendto(self, *args, **kwargs):
            raise OSError("network unreachable")

        def close(self):
            pass

    monkeypatch.setattr(sender.socket, "socket", _BadSocket)

    sender._enqueue("a line")
    sender._q().put_nowait(sender._STOP)
    sender._worker()  # runs to the stop sentinel on this thread

    assert sender.stats()["send_errors"] == 1
    assert sender.stats()["sent"] == 0


def test_a_successful_send_is_counted(monkeypatch):
    _configure(monkeypatch)
    sent: list[tuple] = []

    class _Socket:
        def __init__(self, *args, **kwargs):
            pass

        def sendto(self, payload, address):
            sent.append((payload, address))

        def close(self):
            pass

    monkeypatch.setattr(sender.socket, "socket", _Socket)

    sender._enqueue("<134>Sep  5 08:26:10 h CEF:0|Rezztor|EAgent|1.0|x|X|3|")
    sender._q().put_nowait(sender._STOP)
    sender._worker()

    assert sender.stats()["sent"] == 1
    payload, address = sent[0]
    assert address == ("10.0.0.1", 514)
    assert payload.startswith(b"<134>")


def test_the_test_event_reports_the_error_rather_than_claiming_success(monkeypatch):
    _configure(monkeypatch)

    class _BadSocket:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def sendto(self, *a, **k):
            raise OSError("no route to host")

    monkeypatch.setattr(sender.socket, "socket", _BadSocket)

    result = sender.send_test_event()
    assert result["ok"] is False
    assert "no route to host" in result["error"]


def test_the_test_event_refuses_without_a_destination(monkeypatch):
    _configure(monkeypatch, host="")
    result = sender.send_test_event()
    assert result["ok"] is False
    assert "host" in result["error"].lower()
