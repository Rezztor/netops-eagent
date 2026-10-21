"""Read and write this plugin's connection settings.

The generic plugin settings screen doesn't exist in this frontend -- the same
situation `auto_skills_config.py` documents -- so the Settings screen's
SolarWinds card talks to this directly.

The password never round-trips through here as itself. `get` reports only
whether one is set (`password_set: true/false`) and never includes the value
in `config`. `save` writes a submitted password straight to `usr/.env` via
`helpers/dotenv.py`, never into the plugin's own config file; an empty
submission leaves whatever is stored untouched, so the field can start blank on
every load without that meaning "clear it."

Gated on plugins.manage: this decides which instance the agent reads live
infrastructure data from.
"""

from __future__ import annotations

from typing import Any

from helpers import dotenv
from helpers import plugins as plugin_helpers
from helpers.api import ApiHandler, Request, Response
from plugins._ea_solarwinds.helpers import config

EDITABLE = (
    "host",
    "port",
    "verify_tls",
    "username",
    "timeout_seconds",
    "max_rows",
    "max_calls_per_minute",
)


class SolarwindsConfig(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        action = str(input.get("action") or "get").strip().lower()

        try:
            if action == "get":
                current = config.get_config()
                return {
                    "ok": True,
                    "enabled": config.is_enabled(),
                    "editable": list(EDITABLE),
                    "config": current,
                    "password_set": bool(dotenv.get_dotenv_value(dotenv.KEY_SOLARWINDS_PASSWORD)),
                }

            if action == "save":
                submitted = input.get("config")
                if not isinstance(submitted, dict):
                    return {"ok": False, "error": "config must be an object"}

                merged = {
                    **config.get_config(),
                    **{key: submitted[key] for key in EDITABLE if key in submitted},
                }

                plugin_helpers.save_plugin_config(
                    config.PLUGIN_NAME,
                    project_name="",
                    agent_profile="",
                    settings=merged,
                )

                # The password is never sent back to the client (see `get`
                # below), so there is nothing for a client to round-trip
                # unchanged. An empty field means "leave it as it is"; the
                # only way to change it is to type a new one.
                password = submitted.get("password")
                if isinstance(password, str) and password:
                    dotenv.save_dotenv_value(dotenv.KEY_SOLARWINDS_PASSWORD, password)

                return {
                    "ok": True,
                    "config": config.get_config(),
                    "password_set": bool(dotenv.get_dotenv_value(dotenv.KEY_SOLARWINDS_PASSWORD)),
                }

            return {"ok": False, "error": f"Unknown action: {action}"}
        except Exception as exception:
            return {"ok": False, "error": str(exception)}
