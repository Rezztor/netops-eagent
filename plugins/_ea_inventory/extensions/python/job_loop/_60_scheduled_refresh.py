"""Start the scheduled facts-and-ports refresh when `refresh_interval_hours` says one is due."""

from typing import Any

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class ScheduledRefresh(Extension):
    async def execute(self, **kwargs: Any) -> None:
        from plugins._ea_inventory.helpers import schedule

        try:
            schedule.start_if_due()
        except Exception as exc:  # noqa: BLE001 - housekeeping must not stop the loop
            PrintStyle.error(f"scheduled inventory refresh failed to start: {exc}")
