"""The local sign-in form stops answering after enough wrong guesses.

Before this, a failed password cost `await asyncio.sleep(1)` and nothing else --
a delay on one connection, with no counter and nothing to stop the next hundred
attempts arriving at once. ADR 0021 leaves lockout to the identity provider,
which is right for an OIDC deployment; the local form is break-glass there and
the only way in for a deployment without one.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import login_throttle  # noqa: E402


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    # A named proxy: the addresses below are the browsers' own.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "192.0.2.1")
    login_throttle.reset()
    yield
    login_throttle.reset()


def test_a_fresh_caller_may_try():
    assert login_throttle.retry_after("10.0.0.1", "alice") == 0


def test_guessing_one_account_is_stopped_first():
    for _ in range(login_throttle.IDENTITY_ATTEMPTS):
        login_throttle.record_failure("10.0.0.1", "alice")

    assert login_throttle.retry_after("10.0.0.1", "alice") > 0


def test_spreading_guesses_across_usernames_still_runs_out():
    """The pair counter alone would let one address walk a user list, a few
    guesses each, forever. The address counter is what closes that."""
    for index in range(login_throttle.ADDRESS_ATTEMPTS):
        login_throttle.record_failure("10.0.0.2", f"user{index}")

    assert login_throttle.retry_after("10.0.0.2", "user999") > 0


def test_one_person_cannot_lock_out_another():
    """The reason nothing is keyed by username alone. If it were, anyone who
    knew a colleague's username could lock them out of their own account by
    failing on purpose -- a login form turned into a denial of service against a
    named person."""
    for _ in range(login_throttle.IDENTITY_ATTEMPTS * 4):
        login_throttle.record_failure("10.0.0.3", "victim")

    # The attacker's address is blocked...
    assert login_throttle.retry_after("10.0.0.3", "victim") > 0
    # ...and the victim, signing in from their own machine, is not.
    assert login_throttle.retry_after("10.0.0.99", "victim") == 0


def test_signing_in_clears_the_count():
    for _ in range(login_throttle.IDENTITY_ATTEMPTS - 1):
        login_throttle.record_failure("10.0.0.4", "alice")
    login_throttle.record_success("10.0.0.4", "alice")

    for _ in range(login_throttle.IDENTITY_ATTEMPTS - 1):
        login_throttle.record_failure("10.0.0.4", "alice")
    assert login_throttle.retry_after("10.0.0.4", "alice") == 0


def test_the_window_expires(monkeypatch):
    for _ in range(login_throttle.IDENTITY_ATTEMPTS):
        login_throttle.record_failure("10.0.0.5", "alice")
    assert login_throttle.retry_after("10.0.0.5", "alice") > 0

    later = time.time() + login_throttle.WINDOW_SECONDS + 1
    monkeypatch.setattr(login_throttle, "_now", lambda: later)
    assert login_throttle.retry_after("10.0.0.5", "alice") == 0


def test_tracking_is_bounded():
    """A flood of forged addresses must not grow this without limit; the table
    is in memory and an attacker chooses the keys."""
    for index in range(login_throttle.MAX_TRACKED + 500):
        login_throttle.record_failure(f"10.1.{index // 256}.{index % 256}", None)

    assert len(login_throttle._attempts) <= login_throttle.MAX_TRACKED


def test_a_missing_address_is_still_counted():
    """`request.remote_addr` can be absent. Treating that as "no key" would be a
    hole that needs only a malformed request to find."""
    for _ in range(login_throttle.ADDRESS_ATTEMPTS):
        login_throttle.record_failure(None, "alice")

    assert login_throttle.retry_after(None, "alice") > 0


def test_an_unnamed_proxy_is_not_one_shared_address(monkeypatch):
    """With FORWARDED_ALLOW_IPS empty every browser arrives as the proxy's
    address, so counting that address let ten wrong passwords from anyone lock
    the form for everybody. Only the address-and-username pair is counted."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "")
    monkeypatch.setattr(login_throttle, "_default_gateway", lambda: "172.31.0.1")
    for index in range(login_throttle.ADDRESS_ATTEMPTS * 3):
        login_throttle.record_failure("172.31.0.1", f"guess{index}")

    assert login_throttle.retry_after("172.31.0.1", "admin") == 0

    for _ in range(login_throttle.IDENTITY_ATTEMPTS):
        login_throttle.record_failure("172.31.0.1", "admin")
    assert login_throttle.retry_after("172.31.0.1", "admin") > 0

    # Any other peer is a browser, not a proxy, and keeps its address counter:
    # a public one, and a colleague's desktop on the office LAN.
    for peer in ("93.184.216.34", "10.20.30.40"):
        for index in range(login_throttle.ADDRESS_ATTEMPTS):
            login_throttle.record_failure(peer, f"guess{index}")
        assert login_throttle.retry_after(peer, "someone") > 0
