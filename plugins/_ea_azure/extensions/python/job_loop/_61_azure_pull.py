"""Start the scheduled Azure pull when the schedule says one is due."""

from typing import Any

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class ScheduledAzurePull(Extension):
    async def execute(self, **kwargs: Any) -> None:
        from plugins._ea_azure.helpers import schedule

        try:
            schedule.start_if_due()
        except Exception as exc:  # noqa: BLE001 - housekeeping must not stop the loop
            PrintStyle.error(f"scheduled Azure pull failed to start: {type(exc).__name__}")
