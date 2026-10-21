"""Resolved settings for the Azure plugin.

The credential is not part of this. It lives in `usr/.env` (see `credential.py`) and is read only by
`client.token()`, never merged into the dict returned here, so nothing that only calls `get_config()` can reach it.
"""

from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo

from helpers import plugins as plugin_helpers

PLUGIN_NAME = "_ea_azure"

# Used only if default_config.yaml is unreadable, so a missing file degrades to an unconfigured plugin.
_FALLBACK: dict[str, Any] = {
    "cloud": "public",
    "tenant_id": "",
    "client_id": "",
    "auth": "certificate",
    "secret_expires_on": "",
    "subscriptions_mode": "all",
    "subscription_ids": [],
    "schedule": {"frequency": "weekly", "weekday": "sat", "time": "02:00", "timezone": "UTC"},
    "keep_removed_days": 90,
    "keep_history_days": 365,
    "min_pull_interval_minutes": 15,
    "timeout_seconds": 30,
    "page_size": 1000,
    "max_resources": 100000,
}

_INT_KEYS = (
    "keep_removed_days", "keep_history_days", "min_pull_interval_minutes", "timeout_seconds", "page_size", "max_resources",
)
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _schedule(raw: Any) -> dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    base = _FALLBACK["schedule"]
    frequency = str(raw.get("frequency") or "").strip().lower()
    weekday = str(raw.get("weekday") or "").strip().lower()[:3]
    time = str(raw.get("time") or "").strip()
    zone = str(raw.get("timezone") or "").strip()
    try:
        ZoneInfo(zone)
    except Exception:
        zone = base["timezone"]
    return {
        "frequency": frequency if frequency in ("off", "daily", "weekly") else base["frequency"],
        "weekday": weekday if weekday in _WEEKDAYS else base["weekday"],
        "time": time if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time) else base["time"],
        "timezone": zone,
    }


def _normalise(merged: dict[str, Any]) -> dict[str, Any]:
    from plugins._ea_azure.helpers import client  # client imports this module

    out = dict(merged)
    for key in _INT_KEYS:
        out[key] = _int(out.get(key), _FALLBACK[key])
    for key in ("tenant_id", "client_id", "secret_expires_on"):
        out[key] = str(out.get(key) or "").strip()
    if out.get("cloud") not in client.CLOUDS:
        out["cloud"] = "public"
    if out.get("auth") not in ("certificate", "secret"):
        out["auth"] = "certificate"
    if out.get("subscriptions_mode") not in ("all", "only", "except"):
        out["subscriptions_mode"] = "all"
    ids = out.get("subscription_ids")
    out["subscription_ids"] = list(dict.fromkeys(
        str(i).strip() for i in (ids if isinstance(ids, list) else []) if str(i).strip()
    ))
    out["schedule"] = _schedule(out.get("schedule"))
    return out


def get_config() -> dict[str, Any]:
    """Shipped defaults, overlaid with whatever is stored. No credential."""
    defaults = plugin_helpers.get_default_plugin_config(PLUGIN_NAME) or {}
    stored = plugin_helpers.get_plugin_config(PLUGIN_NAME) or {}
    return _normalise({**_FALLBACK, **defaults, **stored})


def save(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into the stored settings, normalised. Returns the result."""
    merged = _normalise({**get_config(), **updates})
    plugin_helpers.save_plugin_config(PLUGIN_NAME, project_name="", agent_profile="", settings=merged)
    return get_config()


def is_enabled() -> bool:
    """Whether the plugin is toggled on. Dispatch keeps serving a handler after its plugin is disabled, so the
    handlers that refuse when it is off must ask."""
    try:
        return plugin_helpers.get_toggle_state(PLUGIN_NAME) == "enabled"
    except Exception:
        return False


def is_configured() -> bool:
    """Whether there is enough here to attempt a sign-in: both IDs and a credential."""
    from plugins._ea_azure.helpers import credential

    config = get_config()
    return bool(config["tenant_id"] and config["client_id"] and credential.status()["set"])
