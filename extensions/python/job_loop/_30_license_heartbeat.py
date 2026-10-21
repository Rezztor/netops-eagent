"""The background half of the licence: once a minute, let the licence client do what is due.

`helpers.license_client.tick` keeps the clock mark, repeats a request that has had no verified answer, and
makes the daily check-in, waiting 1, 5 and 30 minutes and then an hour after failures. On a Community build it
returns without reading a row or calling the server. The schedule is in the
database, so a restart neither forgets a pending request nor sends an extra check-in.

This is the only scheduled call to the licence server. A failure is quiet by design: the client records it for
the licence page and what the installation does next is decided locally from the signed documents it holds.
The call runs on a worker thread, so a slow licence server never holds the job loop.
"""

import asyncio
from typing import Any

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class LicenseHeartbeat(Extension):
    async def execute(self, data: dict[str, Any] | None = None, **kwargs):
        from helpers import license_client

        try:
            await asyncio.to_thread(license_client.tick)
        except Exception as exc:  # noqa: BLE001 - a job must not kill the loop
            PrintStyle.error(f"Licence check error: {type(exc).__name__}")
