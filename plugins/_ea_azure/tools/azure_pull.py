"""Start a pull of the Azure inventory, the same one as the Pull now button, and report its numbers. Never returns
inventory data (the agent follows with `azure_inventory`), has no argument, and starts nothing the screen could
not. Gated on `inventory.manage` in `helpers/tool_permissions.py`; one pull at a time and a minimum gap are
enforced by `collect.start`, and the audit line names who asked.
"""

from __future__ import annotations

import asyncio
import time

from helpers.tool import Response, Tool

from plugins._ea_azure.helpers import collect, config
from plugins._ea_inventory.helpers import proposals

WAIT_SECONDS, POLL_SECONDS = 90, 2


def _numbers(run: dict) -> str:
    c = run.get("counts") or {}
    s = c.get("subscriptions") or {}
    text = (
        f"The Azure pull {run['status']}: {s.get('read', 0)} of {s.get('listed', 0)} subscriptions read "
        f"({s.get('failed', 0)} failed, {s.get('skipped', 0)} skipped), {c.get('resources', 0)} resources found; "
        f"{c.get('added', 0)} added, {c.get('changed', 0)} changed, {c.get('removed', 0)} removed, "
        f"{c.get('returned', 0)} returned since the one before. It took {c.get('duration_s', 0)} seconds."
    )
    return text + (f" {run['error']}" if run.get("error") else "")


class AzurePull(Tool):
    async def execute(self, **kwargs) -> Response:
        say = lambda text: Response(message=text, break_loop=False)  # noqa: E731
        if not config.is_enabled():
            return say("The Azure plugin is turned off.")
        if not config.is_configured():
            return say("Azure is not set up yet: an administrator has to enter the tenant, the application and a credential in Settings.")
        try:
            run_id = collect.start("chat", proposals.current_actor())
        except collect.Busy:
            return say("A pull is already running. Ask again in a minute.")
        except collect.TooSoon:
            return say(f"The last pull finished less than {config.get_config()['min_pull_interval_minutes']} minutes ago; "
                       "use the stored inventory (azure_inventory) or ask again later.")
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            done = collect.status()["last_run"]
            if done and done["id"] == run_id:
                return say(_numbers(done))
            await asyncio.sleep(POLL_SECONDS)
        return say(f"The pull is still running after {WAIT_SECONDS} seconds. Ask again in a minute.")
