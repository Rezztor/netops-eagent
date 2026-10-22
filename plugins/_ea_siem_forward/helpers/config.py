"""Resolved settings for the SIEM forwarder.

There is no credential here, and no `usr/.env` indirection like `_ea_mail` and
`_ea_solarwinds` need: plain UDP syslog carries no authentication at all. That
is a simplification, not an oversight. TCP/TLS syslog is the upgrade for anyone
whose security policy needs the transport itself protected; this plugin does not
implement it.
"""

from __future__ import annotations

from typing import Any

from helpers import plugins as plugin_helpers

PLUGIN_NAME = "_ea_siem_forward"

SYSLOG_FORMATS = ("rfc3164", "rfc5424")

# Used only if default_config.yaml is unreadable, so a missing or corrupt file
# degrades to an inert plugin rather than to sending somewhere unintended.
_FALLBACK: dict[str, Any] = {
    "enabled": False,
    "host": "",
    "port": 514,
    "syslog_format": "rfc3164",
    "facility": 16,
    "device_vendor": "Rezztor",
    "device_product": "EAgent",
    "min_severity": 0,
    "queue_size": 1000,
}


def _coerce_int(value: Any, fallback: int, low: int, high: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if low <= parsed <= high else fallback


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    return fallback


def get_config() -> dict[str, Any]:
    """Shipped defaults, overlaid with whatever is actually stored."""
    defaults = plugin_helpers.get_default_plugin_config(PLUGIN_NAME) or {}
    stored = plugin_helpers.get_plugin_config(PLUGIN_NAME) or {}

    merged: dict[str, Any] = {**_FALLBACK, **defaults, **stored}

    merged["enabled"] = _coerce_bool(merged.get("enabled"), _FALLBACK["enabled"])
    merged["host"] = str(merged.get("host") or "").strip()
    merged["port"] = _coerce_int(merged.get("port"), _FALLBACK["port"], 1, 65535)
    merged["facility"] = _coerce_int(merged.get("facility"), _FALLBACK["facility"], 0, 23)
    merged["min_severity"] = _coerce_int(merged.get("min_severity"), _FALLBACK["min_severity"], 0, 10)
    merged["queue_size"] = _coerce_int(merged.get("queue_size"), _FALLBACK["queue_size"], 1, 100_000)

    syslog_format = str(merged.get("syslog_format") or "").strip().lower()
    merged["syslog_format"] = (
        syslog_format if syslog_format in SYSLOG_FORMATS else _FALLBACK["syslog_format"]
    )

    for key in ("device_vendor", "device_product"):
        # CEF caps vendor and product at 63 characters. Truncating here keeps a
        # long value from producing a line the collector rejects wholesale.
        merged[key] = (str(merged.get(key) or "").strip() or _FALLBACK[key])[:63]

    return merged


def is_enabled() -> bool:
    """Whether the plugin itself is toggled on.

    API handlers need this: dispatch resolves a handler by directory and keeps
    serving it after the plugin is disabled, so the handler has to check. The
    sink checks it too, on every event, so toggling the plugin off stops
    forwarding immediately rather than at the next restart.
    """
    try:
        return plugin_helpers.get_toggle_state(PLUGIN_NAME) == "enabled"
    except Exception:
        return False


def is_configured() -> bool:
    """Whether there is somewhere to send to. `enabled` is the operator's own
    switch inside the card, separate from the plugin toggle above."""
    config = get_config()
    return bool(config["enabled"] and config["host"])


def device_version() -> str:
    """Read live rather than stored, so the CEF header cannot claim a version
    this instance is not actually running."""
    try:
        from helpers import git

        return (str(git.get_version() or "").strip() or "unknown")[:31]
    except Exception:
        return "unknown"
