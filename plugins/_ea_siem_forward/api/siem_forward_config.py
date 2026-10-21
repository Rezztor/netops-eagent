"""Read and write the SIEM forwarding destination.

The generic plugin settings screen doesn't exist in this frontend -- the same
situation `solarwinds_config.py` and `auto_skills_config.py` document -- so the
Settings screen's SIEM Forwarding card talks to this directly.

There is no credential to protect here, unlike the SolarWinds and SMTP handlers:
plain UDP syslog has no authentication, so the whole configuration is readable
and writable as itself.

Gated on plugins.manage: this decides where a copy of this instance's audit
trail is sent, which is not a per-user preference.
"""

from __future__ import annotations

from typing import Any

from helpers import plugins as plugin_helpers
from helpers.api import ApiHandler, Request, Response
from plugins._ea_siem_forward.helpers import config, sender

def _audit(event: str, **fields: Any) -> None:
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:  # noqa: BLE001 - recording must not block the change
        pass


EDITABLE = (
    "enabled",
    "host",
    "port",
    "syslog_format",
    "facility",
    "device_vendor",
    "device_product",
    "min_severity",
    "queue_size",
)


class SiemForwardConfig(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        action = str(input.get("action") or "get").strip().lower()

        try:
            if action == "get":
                return {
                    "ok": True,
                    "plugin_enabled": config.is_enabled(),
                    "editable": list(EDITABLE),
                    "config": config.get_config(),
                    "device_version": config.device_version(),
                    "syslog_formats": list(config.SYSLOG_FORMATS),
                    "stats": sender.stats(),
                }

            if action == "save":
                submitted = input.get("config")
                if not isinstance(submitted, dict):
                    return {"ok": False, "error": "config must be an object"}

                merged = {
                    **config.get_config(),
                    **{key: submitted[key] for key in EDITABLE if key in submitted},
                }

                # Before the save, so the event still goes to the destination
                # it is leaving. Where the audit trail is copied to, and whether
                # it is copied at all, is the one change to this plugin worth a
                # record -- and it used to leave none.
                _audit("siem_forward.config.update", changed_keys=sorted(k for k in EDITABLE if k in submitted))

                plugin_helpers.save_plugin_config(
                    config.PLUGIN_NAME,
                    project_name="",
                    agent_profile="",
                    settings=merged,
                )

                # Startup only wires the sink for a plugin that was already
                # enabled, so saving a destination is the other moment worth
                # registering at -- otherwise turning this on would appear to
                # do nothing until the next restart. Idempotent.
                if config.is_enabled():
                    sender.ensure_registered()

                return {"ok": True, "config": config.get_config(), "stats": sender.stats()}

            if action == "reset_stats":
                _audit("siem_forward.stats.reset", dropped=sender.stats())
                sender.reset_stats()
                return {"ok": True, "stats": sender.stats()}

            return {"ok": False, "error": f"Unknown action: {action}"}
        except Exception as exception:
            return {"ok": False, "error": str(exception)}
