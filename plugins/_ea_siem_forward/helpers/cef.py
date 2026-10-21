"""Turning one audit record into one CEF line inside a syslog envelope.

Format reference: ArcSight's Common Event Format implementation standard.

    CEF:Version|Device Vendor|Device Product|Device Version|Device Event Class ID|Name|Severity|Extension

Two things about this format cause most integration bugs, and both are handled
explicitly below:

1. **Header and extension escape different characters.** In the header, `|` and
   `\\` are escaped and `=` is not. In the extension it is the reverse: `=` and
   `\\` are escaped, `|` is not. Getting this backwards produces a line the
   collector either truncates or splits in the wrong place.
2. **There are two unrelated severities on one line.** The syslog priority byte
   (`<134>`) encodes facility and *syslog* severity, 0-7. CEF's own `Severity`
   header field is 0-10 and is what a SIEM actually alerts on. This module fixes
   the syslog side at a constant and spends its judgement on the CEF side.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

CEF_VERSION = 0

# Syslog severity 6 (informational) for the priority byte. Deliberately fixed:
# the CEF severity field carries the real signal, and deriving two different
# scales from one judgement would make both of them lies.
_SYSLOG_SEVERITY = 6

# CEF header fields have documented length caps; a value over the cap risks the
# collector rejecting the whole line rather than the field.
_MAX_CLASS_ID = 1023
_MAX_NAME = 512

# Audit record keys that carry their own CEF extension key, so a SIEM's built-in
# CEF parser recognises them without per-customer field mapping. Everything else
# is passed through under its own name.
_ACTOR_TO_CEF = {
    "username": "suser",
    "user_id": "suid",
    "role": "spriv",
}

# Keys never worth forwarding: internal bookkeeping that would only add noise.
_SKIP_FIELDS = {"v"}


def escape_header(value: Any) -> str:
    """Escape a CEF *header* field: backslash and pipe, never the equals sign."""
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def escape_extension(value: Any) -> str:
    """Escape a CEF *extension* value: backslash and equals, never the pipe.

    Newlines become the literal two-character sequences the format defines, so
    a multi-line value stays inside its own field instead of being read as the
    start of a new syslog message.
    """
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def severity(event: str, fields: dict[str, Any]) -> int:
    """CEF severity, 0-10, from the event and its own fields.

    A flat per-event table would be wrong the moment an outcome matters: a
    failed sign-in is not the same severity as a successful one, and they share
    an event name. So outcome is checked first and overrides everything.
    """
    if str(fields.get("outcome", "")).lower() in ("failure", "failed", "denied", "refused"):
        return 8

    name = str(event or "")
    if name.startswith("auth."):
        return 6
    if name.startswith(("account.", "users.", "settings.")):
        return 5
    if name.startswith("plugins."):
        return 5
    return 3


def event_name(event: str) -> str:
    """A human-readable Name for the CEF header, derived from the event id.

    `auth.signin` -> `Auth signin`. Deliberately mechanical: a hand-written
    lookup table would go stale the first time an event is added elsewhere in
    the codebase without anyone thinking about this plugin.
    """
    text = str(event or "event").replace(".", " ").replace("_", " ").strip()
    return (text[:1].upper() + text[1:]) if text else "Event"


def _extension_pairs(record: dict[str, Any]) -> list[str]:
    pairs: list[str] = []

    actor = record.get("actor")
    if isinstance(actor, dict):
        for key, cef_key in _ACTOR_TO_CEF.items():
            value = actor.get(key)
            if value:
                pairs.append(f"{cef_key}={escape_extension(value)}")

    # rt is CEF's own receipt-time key, in epoch milliseconds. Derived from the
    # record's own timestamp rather than "now", so a queued event keeps the time
    # it happened rather than the time it was finally sent.
    epoch_ms = _epoch_millis(record.get("ts"))
    if epoch_ms is not None:
        pairs.append(f"rt={epoch_ms}")

    for key, value in record.items():
        if key in ("actor", "ts", "event") or key in _SKIP_FIELDS:
            continue
        if value is None or isinstance(value, (dict, list)):
            # Nested structures have no faithful representation in CEF's flat
            # key=value extension; the audit record already carries a digest
            # and a capped preview for anything large.
            continue
        pairs.append(f"{escape_extension(key)}={escape_extension(value)}")

    return pairs


def _epoch_millis(ts: Any) -> int | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def format_cef(
    record: dict[str, Any],
    *,
    device_vendor: str,
    device_product: str,
    device_version: str,
) -> str:
    """The CEF portion of the line, without the syslog envelope."""
    event = str(record.get("event") or "unknown")
    fields = {k: v for k, v in record.items() if k not in ("event", "actor", "ts")}

    header = "|".join(
        [
            f"CEF:{CEF_VERSION}",
            escape_header(device_vendor),
            escape_header(device_product),
            escape_header(device_version),
            escape_header(event)[:_MAX_CLASS_ID],
            escape_header(event_name(event))[:_MAX_NAME],
            str(severity(event, fields)),
        ]
    )
    return f"{header}|{' '.join(_extension_pairs(record))}".rstrip()


def _syslog_timestamp(ts: Any) -> str:
    """RFC 3164's timestamp: `Mmm dd hh:mm:ss`, day space-padded, no year."""
    try:
        parsed = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        parsed = datetime.now(timezone.utc)
    return f"{parsed.strftime('%b')} {parsed.day:2d} {parsed.strftime('%H:%M:%S')}"


def format_line(
    record: dict[str, Any],
    *,
    hostname: str,
    facility: int,
    syslog_format: str,
    device_vendor: str,
    device_product: str,
    device_version: str,
) -> str:
    """One complete wire line: syslog envelope wrapped around the CEF message."""
    priority = facility * 8 + _SYSLOG_SEVERITY
    cef = format_cef(
        record,
        device_vendor=device_vendor,
        device_product=device_product,
        device_version=device_version,
    )

    if syslog_format == "rfc5424":
        # VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID STRUCTURED-DATA MSG.
        # Nothing here populates structured data -- the CEF payload is the
        # message, and duplicating it into SD-PARAMs would only double the line.
        stamp = str(record.get("ts") or datetime.now(timezone.utc).isoformat())
        return f"<{priority}>1 {stamp} {hostname} {device_product} - - - {cef}"

    return f"<{priority}>{_syslog_timestamp(record.get('ts'))} {hostname} {cef}"
