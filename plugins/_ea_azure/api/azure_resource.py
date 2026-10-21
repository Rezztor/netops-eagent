"""One stored resource with both directions of its links, its subnets or other children, its latest changes,
why it might be open to the internet, and what Azure itself reported about it (`signals`: Advisor, policy, health
and its own change records, with the time they were read). Gated on `inventory.view` in `helpers/api_policy.py`.
Serves the stored snapshot whether or not the plugin is enabled."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_azure.helpers import config, exposure, signals, store

_PARTS = ("links_out", "links_in", "children", "changes")


def portal_url(native_id: str) -> str:
    # unverified: the portal's link form, until it is tried against a real tenant
    tenant = str(config.get_config().get("tenant_id") or "").strip()
    return f"https://portal.azure.com/#{'@' + tenant + '/' if tenant else ''}resource{native_id}"


class AzureResource(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        try:
            found = store.get(str(input.get("id") or ""))
        except store.Ambiguous as e:
            return {"ok": False, "error": "Several resources have that name; open one by its id.", "candidates": e.candidates}
        if not found:
            return {"ok": False, "error": "No such resource."}
        return {
            "ok": True, "enabled": config.is_enabled(), "resource": {k: v for k, v in found.items() if k not in _PARTS},
            **{k: found[k] for k in _PARTS}, "exposure": exposure.of(found), "signals": signals.of(found["native_id"]), "portal_url": portal_url(found["native_id"]),
        }
