"""Send one synthetic CEF event to the configured destination, now.

An administrator otherwise finds out whether the destination is right during an
incident, which is the worst possible time. This is the same gap the backlog
already names for SMTP ("a test-send button in Settings -> Email").

What it can and cannot prove is worth being precise about, because UDP: a
success here means the packet left this host. It does not mean a collector
received it, parsed it, or accepted the CEF. That is a property of UDP, not of
this handler, and the Settings card says so rather than implying more.

Gated on plugins.manage, like the configuration it exercises.
"""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response
from plugins._ea_siem_forward.helpers import config, sender


class SiemForwardTest(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        try:
            if not config.is_enabled():
                return {"ok": False, "error": "The SIEM Forwarding plugin is disabled."}

            result = sender.send_test_event()
            # Echo the exact line that went out. An operator debugging a
            # collector's parser needs to see what it was given, not a summary.
            return result
        except Exception as exception:
            return {"ok": False, "error": str(exception)}
