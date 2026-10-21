"""Once a day, ask the appliance's host to ask Rezztor's update service which packages follow (ADR 0091).

The application calls no update server: `helpers.update_spool.daily_refresh` writes one `refresh` request into
the spool, and the host tool does the asking. Nothing happens without a spool (a Compose install, a developer's
machine), with the administrator's daily-check switch off, or while the host is busy or has a request pending.
"""

from typing import Any

from helpers.extension import Extension
from helpers.print_style import PrintStyle


class UpdateCheck(Extension):
    def execute(self, data: dict[str, Any] | None = None, **kwargs):
        from helpers import update_spool

        try:
            update_spool.daily_refresh()
        except Exception as exc:  # noqa: BLE001 - a job must not kill the loop
            PrintStyle.error(f"Update check error: {type(exc).__name__}")
