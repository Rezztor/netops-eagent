"""What the stored inventory suggests is wasted, with the rule, the evidence, the inventory date and, where the public
price list can say, a list price. Gated on `inventory.view` in `helpers/api_policy.py`. Serves the stored snapshot
whether or not the plugin is enabled; the only network use is `waste.priced`, a region and a SKU to prices.azure.com.
A pointer to look, not a verdict."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_azure.helpers import store, waste


class AzureWaste(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        filters = {k: str(input[k]) for k in ("subscription", "resource_group") if input.get(k)}
        last = store.as_of()["last_pull"]
        return {"ok": True, "as_of": last["started_at"] if last else None,
                **waste.priced(store.waste(filters))}
