"""Settle Azure changes whose apply was interrupted (the application restarted while an apply job ran)."""

import asyncio
from typing import Any

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class SettleInterruptedAzureChanges(Extension):
    async def execute(self, **kwargs: Any) -> None:
        from plugins._ea_azure_change.helpers import settling

        try:
            await asyncio.to_thread(settling.settle_open)
        except Exception as exc:  # noqa: BLE001 - housekeeping must not stop the loop
            PrintStyle.error(f"settling interrupted Azure changes failed: {type(exc).__name__}")
