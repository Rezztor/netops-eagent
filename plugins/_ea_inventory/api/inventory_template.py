"""A blank CSV for an import. Header only. Gated on `inventory.view`."""

from __future__ import annotations

import csv
import io
from typing import Any

from helpers.api import ApiHandler, Request, Response

from plugins._ea_inventory.helpers.normalise import FIELDS


def content() -> str:
    """One header row: every proposal field but `custom`, then the place by name, level by level, as the alternative to `place_id`."""
    names = [name for name in FIELDS if name != "custom"] + ["site", "location", "building", "floor", "room", "rack"]
    out = io.StringIO()
    csv.writer(out).writerow(names)
    return out.getvalue()


class InventoryTemplate(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        return {
            "ok": True, "filename": "inventory-template.csv",
            "content_type": "text/csv", "content": content(),
        }
