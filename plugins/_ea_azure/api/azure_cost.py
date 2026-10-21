"""The Cost section of the Azure screen: the same answer the `azure_cost` tool gives (`cost.ask`), so the screen and
the chat cannot disagree. Gated on `inventory.view` in `helpers/api_policy.py`. A question asked again within four
hours is answered from the database; a first one waits on Azure (off the event loop). Refused while the plugin is off.
"""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_azure.helpers import config, cost


class AzureCost(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if not config.is_enabled():
            return {"ok": False, "error": "Azure is off. Cost data is read live, so it needs the plugin on."}
        text = lambda key: str(input.get(key) or "").strip()  # noqa: E731
        try:
            out = await asyncio.to_thread(
                cost.ask, text("question") or "spend", scope=text("scope"), resource_group=text("resource_group"),
                timeframe=text("timeframe"), group_by=text("group_by"), resource=text("resource"))
        except (cost.Refused, cost.CostUnavailable) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, **out}
