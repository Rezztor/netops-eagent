"""The Cloud view's tree, its four attention tiles and the "as of" line. Gated on `inventory.view` in
`helpers/api_policy.py`. Serves the stored snapshot whether or not the plugin is enabled."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_azure.helpers import config, store


class AzureTree(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        return {
            "ok": True, "enabled": config.is_enabled(), "tree": store.tree(),
            "attention": {key: store.counts({"attention": key}, "state")[1] for key in store.ATTENTION},
            "as_of": store.as_of(),
        }
