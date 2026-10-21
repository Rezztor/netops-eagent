"""What the tenant pays, from Azure Cost Management: spend by service, resource group, resource, tag or day, a
forecast, budgets with their cost alerts, and what one resource cost. Answers come from `helpers/cost.py`, which
keeps them four hours (one live question per scope and shape in that time), says the period, the currency and how late
the data can be, and labels a forecast as one. Every string that came from Azure passes the redactor inside `cost.ask`.

Read-only: it signs in with the plugin's read credential and reads cost data only. Gated on `inventory.view` in
`helpers/tool_permissions.py`; `require_local_model` covers it like every `azure_*` tool (ADR 0052).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from helpers.tool import Response, Tool

from plugins._ea_azure.helpers import cost


def answer(a: dict[str, Any]) -> str:
    """The tool's whole answer; a sentence when an argument is refused or Azure cannot say."""
    text = lambda key: str(a.get(key) or "").strip()  # noqa: E731
    try:
        out = cost.ask(text("question") or "spend", scope=text("scope"), resource_group=text("resource_group"),
                       timeframe=text("timeframe"), group_by=text("group_by"), resource=text("resource"))
    except cost.Refused as e:
        return str(e)
    except cost.CostUnavailable as e:
        return f"Azure cost data is not available: {e}"
    return out.pop("headline") + "\n\n" + json.dumps(out, indent=1)


class AzureCost(Tool):
    async def execute(self, **kwargs) -> Response:
        # off the event loop: a first question waits on Azure
        return Response(message=await asyncio.to_thread(answer, self.args or {}), break_loop=False)
