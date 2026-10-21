"""Slowing down guesses at the local sign-in form.

The only thing standing between an attacker and an unlimited number of password
attempts was `await asyncio.sleep(1)` after each failure. That is a delay, not a
limit: it costs one second of one connection, and nothing stops the next hundred
attempts arriving in parallel. Against a six-character password on a form that
never says no, one second of latency is not a defence.

ADR 0021 says account lockout is deliberately the identity provider's job, and
for an OIDC deployment that is right -- the IdP holds the policy, and duplicating
it here would mean two answers to the same question. But the local form survives
as break-glass, and for a deployment with no IdP configured it is the only way
in. That is the case this covers.

**Keyed by address, and by address-with-username. Never by username alone.**
Locking an account after N failures lets anyone who knows a colleague's username
lock them out of their own account, which turns a login form into a denial of
service against a named person. The address is what an attacker has to spend to
keep guessing, so that is what gets counted.

State is in memory. This deployment is one host by construction -- there is no
shared cache and horizontal scaling is documented as unsupported -- and a
database write per failed attempt would hand an attacker a cheaper way to load
the database than to guess the password. It resets on restart, which is a real
limit: it is a speed bump against online guessing, not an audit record. The
audit record is the `auth.signin` failure event, which is emitted either way.
"""

from __future__ import annotations

import functools
import ipaddress
import os
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

#: Failures from one address before it has to wait.
ADDRESS_ATTEMPTS = 10
#: Failures against one username from one address. Lower, because a caller who
#: knows the username and keeps missing the password is the guess worth stopping
#: soonest.
IDENTITY_ATTEMPTS = 5
#: How long failures are remembered, and how long a block lasts once tripped.
WINDOW_SECONDS = 15 * 60
#: Ceiling on how many keys are tracked, so a flood of forged addresses cannot
#: grow this without bound. Oldest are dropped first.
MAX_TRACKED = 4096


@dataclass
class _Attempts:
    count: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


_lock = threading.Lock()
_attempts: dict[str, _Attempts] = {}


def _now() -> float:
    return time.time()


def _prune(now: float) -> None:
    stale = [key for key, value in _attempts.items() if now - value.last_seen > WINDOW_SECONDS]
    for key in stale:
        del _attempts[key]
    if len(_attempts) > MAX_TRACKED:
        for key in sorted(_attempts, key=lambda k: _attempts[k].last_seen)[
            : len(_attempts) - MAX_TRACKED
        ]:
            del _attempts[key]


@functools.lru_cache(maxsize=1)
def _default_gateway() -> str:
    """This host's default gateway, or "".

    In the container that is the Docker bridge's gateway: what a proxy on the
    Docker host arrives as, and anything reaching a port published on the
    host's loopback. A browser elsewhere on the network keeps its own address
    through the published port.
    """
    try:
        with open("/proc/net/route") as routes:
            for line in routes.read().splitlines()[1:]:
                fields = line.split()
                if fields[1] == "00000000" and int(fields[3], 16) & 0x2:
                    return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except (OSError, ValueError, IndexError):
        pass
    return ""


def _is_unnamed_proxy(address: str) -> bool:
    """Whether `address` may be a proxy every browser arrives through.

    uvicorn believes `X-Forwarded-For` only from `FORWARDED_ALLOW_IPS`. When
    that names nobody, a loopback peer or the Docker gateway (`_default_gateway`)
    is a proxy on this host, the peer is everybody, and ten wrong passwords
    from anyone would lock the form for the whole instance. Any other peer --
    a colleague's 10.x desktop included -- is a browser and is counted.
    """
    if os.environ.get("FORWARDED_ALLOW_IPS", "").strip():
        return False
    try:
        peer = ipaddress.ip_address(address)
    except ValueError:
        return False
    return peer.is_loopback or str(peer) == _default_gateway()


def _keys(address: str | None, username: str | None) -> list[tuple[str, int]]:
    address = (address or "unknown").strip().lower()
    # ponytail: behind an unnamed proxy only the pair counter applies, so
    # spraying many usernames is bounded by the one-second delay alone; naming
    # the proxy in FORWARDED_ALLOW_IPS restores the address counter.
    keys = [] if _is_unnamed_proxy(address) else [(f"addr:{address}", ADDRESS_ATTEMPTS)]
    if username:
        keys.append((f"pair:{address}:{username.strip().lower()[:64]}", IDENTITY_ATTEMPTS))
    return keys


def retry_after(address: str | None, username: str | None = None) -> int:
    """Seconds this caller must wait, or 0 when it may try now."""
    now = _now()
    with _lock:
        _prune(now)
        wait = 0
        for key, allowed in _keys(address, username):
            record = _attempts.get(key)
            if record and record.count >= allowed:
                remaining = int(WINDOW_SECONDS - (now - record.last_seen))
                wait = max(wait, remaining)
        return max(wait, 0)


def record_failure(address: str | None, username: str | None = None) -> None:
    now = _now()
    with _lock:
        for key, _ in _keys(address, username):
            record = _attempts.get(key)
            if record is None or now - record.first_seen > WINDOW_SECONDS:
                _attempts[key] = _Attempts(count=1, first_seen=now, last_seen=now)
            else:
                record.count += 1
                record.last_seen = now
        # After the insert, not before: pruning first leaves room for the new
        # key and the table settles one entry above the ceiling, which is a
        # bound that does not quite hold.
        _prune(now)


def record_success(address: str | None, username: str | None = None) -> None:
    """Forget this caller's failures.

    Clears the address counter too, not only the pair: an attempt that succeeded
    is evidence the address belongs to somebody who knows a password, and a
    shared office address must not accumulate its way to a block because several
    people mistyped during the day.
    """
    with _lock:
        for key, _ in _keys(address, username):
            _attempts.pop(key, None)


def reset() -> None:
    """Forget everything. For tests, and for nothing else."""
    with _lock:
        _attempts.clear()
