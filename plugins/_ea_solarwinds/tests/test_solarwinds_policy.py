"""The per-conversation rate cap. Nothing else here restricts who may call this
plugin's tool -- the query templates in capabilities.py are the access-control
boundary -- so this is purely a guard against a loop hammering the instance.
"""

from __future__ import annotations

import time

import pytest

from helpers import files
from plugins._ea_solarwinds.helpers import config as plugin_config
from plugins._ea_solarwinds.helpers import policy


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr").mkdir(parents=True, exist_ok=True)
    yield tmp_path


@pytest.fixture
def limit(monkeypatch):
    def set_limit(n):
        monkeypatch.setattr(
            plugin_config, "get_config",
            lambda: {"max_calls_per_minute": n},
        )
    return set_limit


def test_calls_under_the_limit_are_not_refused(instance, limit):
    limit(3)
    for _ in range(3):
        policy.check_and_record("ctx-1")  # must not raise


def test_the_call_past_the_limit_is_refused(instance, limit):
    limit(2)
    policy.check_and_record("ctx-1")
    policy.check_and_record("ctx-1")
    with pytest.raises(policy.RateLimited, match="limit"):
        policy.check_and_record("ctx-1")


def test_the_limit_is_per_conversation_not_global(instance, limit):
    limit(1)
    policy.check_and_record("ctx-1")
    policy.check_and_record("ctx-2")  # must not raise -- different conversation


def test_the_window_lets_go_after_a_minute(instance, limit, monkeypatch):
    limit(1)
    policy.check_and_record("ctx-1")
    later = time.time() + 61
    monkeypatch.setattr(policy.time, "time", lambda: later)
    policy.check_and_record("ctx-1")  # must not raise


def test_a_refused_call_was_still_counted(instance, limit):
    """Recorded before the call runs, not after -- a query that hangs and
    times out must still count against the cap."""
    limit(1)
    policy.check_and_record("ctx-1")
    with pytest.raises(policy.RateLimited):
        policy.check_and_record("ctx-1")
    with pytest.raises(policy.RateLimited):
        policy.check_and_record("ctx-1")


def test_other_conversations_old_stamps_are_dropped(instance, limit, monkeypatch):
    """The ledger kept one key per conversation for ever, and was rewritten whole on every call."""
    limit(5)
    monkeypatch.setattr(time, "time", lambda: 1000.0)
    policy.check_and_record("old-chat")
    monkeypatch.setattr(time, "time", lambda: 2000.0)
    policy.check_and_record("new-chat")
    assert set(policy._read_ledger()) == {"new-chat"}
