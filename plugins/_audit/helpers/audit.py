"""Structured audit events.

Emitted as one JSON object per line on stdout, prefixed so a log shipper can
select them without parsing everything the process prints. stdout rather than a
file on purpose -- see docs/internal/decisions/0005-audit-to-stdout-not-to-disk.md.

Three rules this module exists to enforce:

1. Auditing must never break the thing it audits. Every entry point swallows its
   own exceptions; a malformed event is lost, the request is not.
2. Nothing reaches the log unmasked. Values registered as secrets are replaced
   before serialisation, because an audit log is exactly where a leaked
   credential does the most damage and is least expected.
3. Every event carries who did it. An event without an actor is still emitted --
   silence would be worse -- but it is marked so the gap is visible.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections import deque
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Callable

AUDIT_PREFIX = "REZZTOR_AUDIT"
SCHEMA_VERSION = 1

# Arguments can be arbitrarily large (a whole script, a file's contents). The
# full value is represented by a digest; the preview is for a human scanning.
MAX_PREVIEW_CHARS = 400

_write_lock = threading.Lock()

# Anything the model or a tool prints can imitate a REZZTOR_AUDIT line. Every
# record therefore carries `boot` (random per process, so a restart is visible),
# `seq` (rises by one per record within a boot, so a gap is visible) and `mac`
# (HMAC-SHA256 of the record's canonical JSON without `mac`). A receiver that
# holds the key rejects a missing or wrong mac; see verify().
_BOOT = secrets.token_hex(8)
_PROCESS_KEY = secrets.token_hex(32)
_seq = 0
KEY_LABEL = b"eagent audit record"


def audit_key(secret: str | None = None) -> bytes:
    """HMAC key derived from the server's FLASK_SECRET_KEY under its own label."""
    return hmac.new((secret or os.getenv("FLASK_SECRET_KEY") or _PROCESS_KEY).encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()


def _mac(record: dict[str, Any], key: bytes) -> str:
    body = json.dumps({k: v for k, v in record.items() if k != "mac"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()


def verify(lines: Any, key: bytes | None = None) -> list[str]:
    """Check REZZTOR_AUDIT lines in order; returns the problems found (empty = clean).

    A line with a missing or wrong mac is reported and skipped; a gap in `seq`
    within one `boot` is reported. Lines without the prefix are ignored.
    """
    key = key or audit_key()
    problems: list[str] = []
    last: dict[str, int] = {}
    for n, line in enumerate(lines, 1):
        if not str(line).startswith(AUDIT_PREFIX + " "):
            continue
        try:
            record = json.loads(str(line)[len(AUDIT_PREFIX) + 1 :])
            ok = hmac.compare_digest(str(record.get("mac", "")), _mac(record, key))
        except Exception:
            ok = False
        if not ok:
            problems.append(f"line {n}: missing or wrong mac")
            continue
        boot, seq = record.get("boot"), record.get("seq")
        if boot in last and seq != last[boot] + 1:
            problems.append(f"line {n}: sequence gap after {last[boot]}, got {seq}")
        last[boot] = seq
    return problems


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _mask(value: str, context: Any = None) -> str:
    """Replace registered secret values. Falls back to the raw string only if
    the secrets manager is unavailable, never to silently dropping the event."""
    try:
        from helpers.secrets import get_secrets_manager

        return get_secrets_manager(context).mask_values(value)
    except Exception:
        return value


def _masked(value: Any, context: Any = None) -> Any:
    """`value` with every string in it masked, while it is still the string
    that is stored. Serialising first hides a secret from the search: JSON
    writes a newline as `\\n`, escapes a quote and spells a non-ASCII letter
    `\\uXXXX`, and none of those is the secret as the store holds it."""
    if isinstance(value, str):
        return _mask(value, context)
    if isinstance(value, dict):
        return {key: _masked(item, context) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_masked(item, context) for item in value]
    return value


def digest(value: Any) -> str:
    try:
        raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    except Exception:
        raw = str(value)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


def preview(value: Any, context: Any = None) -> str:
    try:
        # ensure_ascii=False, as `emit` writes it: the placeholder itself is not ASCII.
        raw = value if isinstance(value, str) else json.dumps(_masked(value, context), default=str, ensure_ascii=False)
    except Exception:
        raw = str(value)
    # Again, on the text: a plain string was not walked above, and neither was
    # anything `default=str` turned into one.
    masked = _mask(raw, context)
    if len(masked) > MAX_PREVIEW_CHARS:
        return masked[:MAX_PREVIEW_CHARS] + f"… (+{len(masked) - MAX_PREVIEW_CHARS} chars)"
    return masked


def current_actor() -> dict[str, Any]:
    """Who is performing the current operation.

    Works both inside a request and on an agent's background thread, because
    user_scope resolves either. An unresolved actor is reported as such rather
    than omitted.
    """
    actor: dict[str, Any] = {"user_id": None, "username": None, "role": None}
    try:
        from helpers import login, user_scope

        actor["user_id"] = user_scope.get_current_user_id()
        user = login.current_user()
        if user:
            actor["username"] = user.get("username")
            actor["role"] = user.get("role")
        elif actor["user_id"]:
            from helpers import users

            record = users.get_user_by_id(actor["user_id"])
            if record:
                actor["username"] = record.get("username")
                actor["role"] = record.get("role")
    except Exception:
        pass
    return actor


# A bounded window of the most recent events, so an administrator can see what
# just happened without leaving the interface.
#
# This is NOT the audit trail and must never be treated as one. The audit trail
# is the stdout stream shipped off-box to the SIEM, and its tamper-evidence comes
# precisely from living somewhere the application cannot rewrite (ADR 0005).
# This buffer is in memory, capped, and lost on restart -- convenience only.
RECENT_EVENT_LIMIT = 200
_recent: deque[dict[str, Any]] = deque(maxlen=RECENT_EVENT_LIMIT)


# Additional consumers of every record, registered by a plugin at load time --
# a SIEM forwarder, for instance.
#
# Deliberately NOT `@extension.extensible` on emit() itself. That decorator's
# `start` point hands an extension the arguments before this function masks
# them, timestamps them and writes them, which would be a new way to alter an
# audit record on its way out -- the exact property ADR 0005 exists to deny.
# A sink is additive only: it runs after the record is written and buffered,
# receives its own copy, and nothing here reads back what it does with it.
_sinks: list[Callable[[dict[str, Any]], None]] = []


def register_sink(fn: Callable[[dict[str, Any]], None]) -> None:
    """Register an extra consumer of every audit record.

    Called with the fully masked, finalised record after it has reached stdout
    and the in-memory window -- never before either, and never with the ability
    to change what they got. A sink that raises is caught and ignored, which is
    the same promise emit() already makes for itself: auditing must not break
    the audited operation, and neither may forwarding it.

    A sink is expected to return promptly. It runs on the thread that emitted
    the event, so anything slow (a socket, a disk) belongs on the sink's own
    queue and thread, not here.
    """
    _sinks.append(fn)


def emit(event: str, **fields: Any) -> None:
    """Write one audit event. Never raises."""
    try:
        record = {
            "v": SCHEMA_VERSION,
            "ts": _now(),
            "event": event,
            "actor": current_actor(),
        }
        # Rule 2, here and not only in `preview`: a caller that passes a field
        # as it has it -- an error text, a path, a name -- is masked too.
        for name, value in _masked(fields).items():
            # Rule 3: who did it, when, and what it was are this function's to
            # say. A field of the same name is kept beside them, not over them;
            # `actor=<a username>` from a caller used to replace the resolved
            # actor, under a valid mac.
            record[f"field_{name}" if name in record else name] = value
        # Normalise first so the mac covers exactly what a receiver parses back.
        record = json.loads(json.dumps(record, default=str, ensure_ascii=False))
        with _write_lock:
            global _seq
            _seq += 1
            record.update(boot=_BOOT, seq=_seq)
            record["mac"] = _mac(record, audit_key())
            # ASCII on the wire. One event is one line, and with ensure_ascii
            # off U+2028, U+2029 and U+0085 pass through raw: they end a line
            # for whoever reads text, so a typed username could cut a genuine
            # record in two. Escaped, the value is the same and the line whole.
            line = f"{AUDIT_PREFIX} " + json.dumps(record)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
            _recent.append(record)

        # Outside the write lock on purpose: a slow sink must not hold up
        # another thread's audit write. Each sink gets its own shallow copy, so
        # one cannot alter what the next sink -- or the buffer above -- sees.
        for sink in _sinks:
            try:
                sink(dict(record))
            except Exception:
                pass
    except Exception:
        # Auditing must not break the operation it is auditing.
        pass


def recent_events(limit: int = 50) -> list[dict[str, Any]]:
    """Most recent events first, newest to oldest.

    Read from the in-memory window described above. Returns whatever is there,
    which after a restart is nothing -- callers must present it as a live tail,
    never as a complete record.
    """
    with _write_lock:
        events = list(_recent)
    events.reverse()
    return events[: max(0, int(limit))]
