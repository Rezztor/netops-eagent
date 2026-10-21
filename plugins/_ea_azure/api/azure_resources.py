"""Stored resources, filtered, with the values the filters offer. Gated on `inventory.view` in
`helpers/api_policy.py`. Serves the stored snapshot whether or not the plugin is enabled."""

from __future__ import annotations

from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_azure.helpers import categories, config, store

FILTERS = ("q", "subscription", "resource_group", "category", "type", "region", "state", "tag", "without",
           "changed_since", "include_removed", "attention")
#: what the filter drop-downs are offered from: where you are, not what you already picked in them
_SCOPE = ("subscription", "resource_group", "category", "without", "changed_since", "include_removed", "attention")
_TAG_SCAN, _PAGE = 2000, 500


def number(value: Any, default: int, high: int) -> int:
    try:
        return max(0, min(int(value), high))
    except (TypeError, ValueError):
        return default


def _tag_names(scope: dict[str, Any]) -> list[str]:
    """ponytail: read from the first 2,000 rows in scope (the store has no tag listing); add one if tenants outgrow it."""
    names: set[str] = set()
    for offset in range(0, _TAG_SCAN, _PAGE):
        rows, total = store.search(scope, _PAGE, offset)
        names.update(name for r in rows for name in r["tags"])
        if offset + _PAGE >= total:
            break
    return sorted(names, key=str.lower)


class AzureResources(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        filters = {k: input[k] for k in FILTERS if input.get(k) not in (None, "")}
        scope = {k: v for k, v in filters.items() if k in _SCOPE}
        try:
            rows, total = store.search(filters, max(1, number(input.get("limit"), 100, 200)), number(input.get("offset"), 0, 10**9))
            groups = {by: store.counts(scope, by)[0] for by in ("type", "region", "state")}
        except store.BadFilter as e:
            return {"ok": False, "error": str(e)}
        return {
            "ok": True, "enabled": config.is_enabled(), "resources": rows, "total": total,
            "facets": {
                "types": [{"type": g["key"], "label": categories.label_of(g["key"]), "count": g["count"]} for g in groups["type"]],
                "regions": [g for g in groups["region"] if g["key"]],
                "states": groups["state"],
                "tag_names": _tag_names(scope),
            },
        }
