"""Pulls (no arguments), or the changes of one pull or since a date (`run_id` / `since`). Gated on
`inventory.view` in `helpers/api_policy.py`. Serves the stored snapshot whether or not the plugin is enabled."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_azure.api.azure_resources import number
from plugins._ea_azure.helpers import config, store


class AzureHistory(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        run_id, since = (str(input.get(k) or "").strip() for k in ("run_id", "since"))
        limit = max(1, number(input.get("limit"), 100, 200))
        if not (run_id or since):
            return {"ok": True, "enabled": config.is_enabled(), "runs": store.runs(limit)}
        try:
            rows, total = store.changes(run_id or None, since or None, limit, number(input.get("offset"), 0, 10**9))
        except store.BadFilter as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "enabled": config.is_enabled(), "changes": rows, "total": total}
