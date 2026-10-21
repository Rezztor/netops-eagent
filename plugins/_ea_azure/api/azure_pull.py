"""Pull now: start one Azure pull in the background and return its run id. Needs `inventory.manage` (see
`helpers/api_policy.py`); no approval card, because a pull is one fixed read-only request and returns no data.
One at a time, and refused within `min_pull_interval_minutes` of the last one. Audited by the pull itself."""

from __future__ import annotations

from typing import Any

from helpers import login
from helpers.api import ApiHandler, Request, Response
from plugins._ea_azure.helpers import client, collect, config


def _no(reason: str, error: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "reason": reason}


class AzurePull(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if not config.is_enabled():
            return _no("disabled", "Azure is turned off. Turn it on in Plugins first.")
        try:
            run_id = collect.start("screen", login.current_username() or "unknown")
        except collect.Busy as e:
            return _no("busy", str(e))
        except collect.TooSoon as e:
            return _no("too_soon", str(e))
        except client.NotConfigured as e:
            return _no("not_configured", str(e))
        return {"ok": True, "run_id": run_id}
