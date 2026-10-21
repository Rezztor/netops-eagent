"""Read and write this plugin's settings.

The generic plugin settings screen does not exist in this frontend --
plugin.yaml's settings_sections is inert and api/plugins.py's get_config and
save_config have no caller -- so the Skills screen talks to this instead.

Gated on plugins.manage: the settings decide whether the agent writes skills at
all, and for whom.
"""

from __future__ import annotations

from typing import Any

from helpers import plugins as plugin_helpers
from helpers.api import ApiHandler, Request, Response
from plugins._ea_auto_skills.helpers import config

# Only these may be set from the interface. The size ceilings and the review
# timeout are deliberately absent: they are safety limits, and an operator who
# needs to move one can edit config.json with the deliberation that implies.
EDITABLE = ("mode", "min_tool_calls", "max_personal_skills", "max_pending_promotions")


class AutoSkillsConfig(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        action = str(input.get("action") or "get").strip().lower()

        try:
            if action == "get":
                return {
                    "ok": True,
                    "enabled": config.is_enabled(),
                    "editable": list(EDITABLE),
                    "config": config.get_config(),
                }

            if action == "save":
                submitted = input.get("config")
                if not isinstance(submitted, dict):
                    return {"ok": False, "error": "config must be an object"}

                merged = {**config.get_config(), **{
                    key: submitted[key] for key in EDITABLE if key in submitted
                }}

                if merged["mode"] not in config.MODES:
                    return {"ok": False, "error": f"mode must be one of {', '.join(config.MODES)}"}

                plugin_helpers.save_plugin_config(
                    config.PLUGIN_NAME,
                    project_name="",
                    agent_profile="",
                    settings=merged,
                )
                # Read back through the same coercion the runtime uses, so the
                # caller sees what will actually take effect.
                return {"ok": True, "config": config.get_config()}

            return {"ok": False, "error": f"Unknown action: {action}"}
        except Exception as exception:
            return {"ok": False, "error": str(exception)}
