"""Resolved settings for the SolarWinds plugin.

The password is not part of this. It lives in `usr/.env` through
`helpers/dotenv.py`, the same indirection the SMTP password uses, and is read
separately by `helpers/client.py` at the point of connecting -- never merged
into the dict this module returns, so there is no path from "read the config"
to "get the password" for anything that only calls `get_config()`.
"""

from __future__ import annotations

from typing import Any

from helpers import plugins as plugin_helpers

PLUGIN_NAME = "_ea_solarwinds"

# Used only if default_config.yaml is unreadable, so a missing or corrupt file
# degrades to an unconfigured plugin rather than to unbounded behaviour.
_FALLBACK: dict[str, Any] = {
    "host": "",
    "port": 17778,
    "verify_tls": True,
    "username": "",
    "timeout_seconds": 15,
    "max_rows": 50,
    "max_calls_per_minute": 20,
}

_INT_KEYS = ("port", "timeout_seconds", "max_rows", "max_calls_per_minute")


def _coerce_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


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
    """Shipped defaults, overlaid with whatever is actually stored. No password."""
    defaults = plugin_helpers.get_default_plugin_config(PLUGIN_NAME) or {}
    stored = plugin_helpers.get_plugin_config(PLUGIN_NAME) or {}

    merged: dict[str, Any] = {**_FALLBACK, **defaults, **stored}

    for key in _INT_KEYS:
        merged[key] = _coerce_int(merged.get(key), _FALLBACK[key])
    merged["verify_tls"] = _coerce_bool(merged.get("verify_tls"), _FALLBACK["verify_tls"])
    merged["host"] = str(merged.get("host") or "").strip()
    merged["username"] = str(merged.get("username") or "").strip()

    return merged


def is_enabled() -> bool:
    """Whether the plugin is toggled on. API handlers need this -- dispatch
    resolves a handler by directory and keeps serving it after the plugin is
    disabled, so the handler itself must check."""
    try:
        return plugin_helpers.get_toggle_state(PLUGIN_NAME) == "enabled"
    except Exception:
        return False


def is_configured() -> bool:
    """Whether there is enough here to attempt a connection."""
    from helpers import dotenv

    config = get_config()
    return bool(
        config["host"]
        and config["username"]
        and dotenv.get_dotenv_value(dotenv.KEY_SOLARWINDS_PASSWORD)
    )
