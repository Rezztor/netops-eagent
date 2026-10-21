"""Resolved settings for the Auto Skills plugin.

get_plugin_config returns a stored config.json wholesale rather than merging it
over the shipped defaults, so a partial file would leave keys missing. Every
read goes through here, which overlays the two and coerces the result, so no
caller has to guess whether a key is present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from helpers import plugins as plugin_helpers

if TYPE_CHECKING:
    from agent import Agent


PLUGIN_NAME = "_ea_auto_skills"

MODE_PERSONAL = "personal"
MODE_REVIEW = "review"
MODE_OFF = "off"
MODES = (MODE_PERSONAL, MODE_REVIEW, MODE_OFF)

# Used only if default_config.yaml is unreadable, so that a missing or corrupt
# file degrades to doing nothing rather than to unbounded behaviour.
_FALLBACK: dict[str, Any] = {
    "mode": MODE_OFF,
    "min_tool_calls": 2,
    "max_personal_skills": 25,
    "max_pending_promotions": 50,
    "max_skill_md_chars": 10000,
    "max_skill_bytes": 40000,
    "review_timeout_seconds": 120,
}

_INT_KEYS = (
    "min_tool_calls",
    "max_personal_skills",
    "max_pending_promotions",
    "max_skill_md_chars",
    "max_skill_bytes",
    "review_timeout_seconds",
)


def _coerce_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def get_config(agent: "Agent | None" = None) -> dict[str, Any]:
    """Shipped defaults, overlaid with whatever scope resolution found."""
    defaults = plugin_helpers.get_default_plugin_config(PLUGIN_NAME) or {}
    stored = plugin_helpers.get_plugin_config(PLUGIN_NAME, agent=agent) or {}

    merged: dict[str, Any] = {**_FALLBACK, **defaults, **stored}

    # YAML reads a bare off, on, no and yes as booleans, so a config file can
    # hand us False where a mode name was meant. Anything unrecognised becomes
    # off, which is the safe direction: a typo silently stops the plugin rather
    # than silently starting it.
    raw_mode = merged.get("mode")
    if isinstance(raw_mode, bool):
        raw_mode = MODE_OFF if raw_mode is False else ""
    mode = str(raw_mode or "").strip().lower()
    merged["mode"] = mode if mode in MODES else MODE_OFF

    for key in _INT_KEYS:
        merged[key] = _coerce_int(merged.get(key), _FALLBACK[key])

    return merged


def is_enabled() -> bool:
    """Whether the plugin is toggled on. Extensions do not need this -- they are
    only loaded for enabled plugins -- but API handlers do: dispatch resolves a
    handler by directory and keeps serving it after the plugin is disabled."""
    try:
        return plugin_helpers.get_toggle_state(PLUGIN_NAME) == "enabled"
    except Exception:
        return False
