"""Reclaim runner pods nobody is using.

Runners are per user and sessions are per conversation, so a runner cannot be
released when a single conversation ends -- another may still be using it. This
sweep is what actually bounds their lifetime, and it is also the only thing that
collects pods orphaned when the application restarts mid-session.

Runs on the existing job loop rather than a scheduled job of its own so it needs no
extra deployment object and no second credential.
"""

from __future__ import annotations

import asyncio
import time

from helpers.extension import Extension
from helpers.print_style import PrintStyle

# The sweep itself is cheap -- one list call -- but there is no reason to make
# it every loop iteration.
INTERVAL_SECONDS = 300

_last_run = 0.0


class ReclaimIdleRunners(Extension):
    async def execute(self, **kwargs):
        global _last_run

        from plugins._code_execution.helpers import runner_pool

        pool = runner_pool.get_pool()
        if pool is None:
            return

        now = time.monotonic()
        if now - _last_run < INTERVAL_SECONDS:
            return
        _last_run = now

        try:
            # Blocking I/O -- an API call for pods, a query and a file write
            # for slots. Kept off the event loop either way.
            removed = await asyncio.to_thread(pool.sweep)
        except Exception as exc:  # noqa: BLE001 - housekeeping must not stop the loop
            PrintStyle.error(f"runner sweep failed: {exc}")
            return

        if removed:
            PrintStyle.standard(f"reclaimed {len(removed)} idle runner(s): {', '.join(removed)}")
