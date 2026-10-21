"""Where the Ansible executor is, and whether this instance can reach it.

Neither token round-trips through here. `get` reports only whether each is set;
`save` writes a submitted job token or administration token straight to
`usr/.env` through `helpers/dotenv.py`, never into the plugin's own config
file, and an empty submission leaves whatever is stored untouched -- so the
fields can start blank on every load without that meaning "clear it". Same
shape as `_ea_solarwinds/api/solarwinds_config.py`, for the same reason.

The administration token is the one Network → Devices needs. The deployment
supplies it with the job token (ADR 0036), so it is pasted here only where the
deployment keeps it away from the application.

Gated on `plugins.manage`: this decides which executor the agent sends network
jobs to.
"""

from __future__ import annotations

from typing import Any

from helpers import dotenv, plugins as plugin_helpers, rbac
from helpers.api import ApiHandler, Request, Response

from plugins._ea_ansible.helpers import config as plugin_config
from plugins._ea_ansible.helpers import executor_client

EDITABLE = (
    "executor_url",
    "job_timeout_seconds",
    "max_devices_per_job",
    "read_approval",
    "require_local_model",
)


def _audit(event: str, **fields: Any) -> None:
    """Audit without letting a logging failure fail the operation."""
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:
        pass


class AnsibleConfig(ApiHandler):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_PLUGINS_MANAGE

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        action = str(input.get("action") or "get").strip().lower()

        try:
            if action == "get":
                return self._state()

            if action == "save":
                submitted = input.get("config")
                if not isinstance(submitted, dict):
                    return {"ok": False, "error": "config must be an object"}

                settings = {
                    key: submitted[key] for key in EDITABLE if key in submitted
                }
                if settings.get("read_approval", "every_time") not in plugin_config.READ_APPROVAL:
                    return {"ok": False, "error": f"read_approval must be one of {plugin_config.READ_APPROVAL}"}
                if "require_local_model" in settings and not isinstance(settings["require_local_model"], bool):
                    return {"ok": False, "error": "require_local_model must be true or false"}
                if settings:
                    plugin_helpers.save_plugin_config(
                        plugin_config.PLUGIN_NAME,
                        project_name="",
                        agent_profile="",
                        settings={**plugin_config.get_config(), **settings},
                    )

                # Blank means "leave it alone", not "clear it". Clearing is an
                # explicit action so a form that always loads empty cannot wipe
                # a working token by being saved.
                token = str(input.get("executor_token") or "")
                if token.strip():
                    dotenv.save_dotenv_value(
                        plugin_config.get_config()["executor_token_key"], token.strip()
                    )
                admin = str(input.get("admin_token") or "")
                if admin.strip():
                    dotenv.save_dotenv_value(
                        plugin_config.get_config()["admin_token_key"], admin.strip()
                    )

                # Who pointed the instance at an executor or rotated a token.
                # Setting names and whether a token field was submitted — never
                # the token values or the executor URL itself (it can carry a
                # credential in the authority).
                _audit(
                    "ansible.config.save",
                    changed_keys=sorted(settings),
                    executor_token_updated=bool(token.strip()),
                    admin_token_updated=bool(admin.strip()),
                )
                return self._state()

            if action == "clear_token":
                dotenv.save_dotenv_value(
                    plugin_config.get_config()["executor_token_key"], ""
                )
                _audit("ansible.config.clear_token")
                return self._state()

            if action == "test":
                return self._test()

            return {"ok": False, "error": f"Unknown action: {action}"}
        except Exception as exception:
            return {"ok": False, "error": str(exception)}

    def _state(self) -> dict[str, Any]:
        return {
            "ok": True,
            "plugin_enabled": plugin_helpers.get_toggle_state(plugin_config.PLUGIN_NAME)
            == "enabled",
            "config": plugin_config.public_config(),
        }

    def _test(self) -> dict[str, Any]:
        """Ask the executor what it is. Reports only what the answer proves.

        A healthy answer means this instance can reach the executor and which
        catalogue it is pinned to. It does not mean a device is reachable, and
        the card says so rather than implying otherwise.
        """
        state = self._state()
        try:
            health = executor_client.health()
        except executor_client.ExecutorError as error:
            return {**state, "reachable": False, "detail": str(error)}
        return {
            **state,
            "reachable": True,
            "catalogue_commit": str(health.get("catalogue_commit") or ""),
            "catalogue_entries": list(health.get("catalogue_entries") or []),
            "busy": bool(health.get("busy")),
            # Reported, never configured from here. The executor enforces its
            # own rate and concurrency caps, and a screen that let an
            # administrator type a different number would be showing them a
            # number nothing applies.
            "limits": dict(health.get("limits") or {}),
            "queue": dict(health.get("queue") or {}),
            "collections": dict(health.get("collections") or {}),
            "ansible_version": str(health.get("ansible_version") or ""),
        }
