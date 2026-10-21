"""What to check before building something in Azure: naming, Azure's name rules, a free address block, tags, hub,
region, zones, SKUs in use and their list price, duplicates. Findings with their evidence, computed by
`helpers/advise.py` from the stored inventory; the answer starts with the age of the pull, like `azure_inventory`.

Read-only and never contacts Azure. The one outside call is the public list price (`helpers/prices.py`, no token,
`prices.azure.com` only) and the advice says the price is unknown when it cannot be had. Every string that came from
Azure passes `redact.redact_text`. Gated on `inventory.view` in `helpers/tool_permissions.py`.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from helpers.tool import Response, Tool

from plugins._ea_azure.helpers import advise, store
from plugins._ea_azure.tools.azure_inventory import _clean, _header

MAX_NEEDS = 8


def _args(a: dict[str, Any]) -> dict[str, Any]:
    """Checked arguments, or `advise.Refused` with the sentence to give back."""
    purpose, env = str(a.get("purpose") or "").strip(), str(a.get("env") or "").strip()
    if not advise.slug(purpose) or len(purpose) > 80:
        raise advise.Refused("purpose is needed: a few words such as `payments web app`.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 -]{1,19}", env):
        raise advise.Refused("env is needed: a short word such as prod, test or dev.")
    region = re.sub(r"\s+", "", str(a.get("region") or "")).lower()
    if region and not re.fullmatch(r"[a-z][a-z0-9]{2,31}", region):
        raise advise.Refused("region must be an Azure region name such as uaenorth or westeurope.")
    raw = a.get("needs") or []
    asked = [n.strip().lower() for n in (raw.split(",") if isinstance(raw, str) else map(str, raw)) if n.strip()]
    if len(asked) > MAX_NEEDS or any(n not in advise.NEEDS for n in asked):
        raise advise.Refused(f"needs is at most {MAX_NEEDS} of: {', '.join(advise.NEEDS)}.")
    size = str(a.get("address_size") or "").strip().lstrip("/")
    if size and not (size.isdigit() and 8 <= int(size) <= 29):
        raise advise.Refused("address_size must be a prefix length from 8 to 29, such as 24 or /24.")
    return {"purpose": purpose, "env": env, "region": region, "size": int(size) if size else None,
            "needs": list(dict.fromkeys(advise.NEEDS[n] for n in asked)), "subscription": str(a.get("subscription") or "").strip()}


def answer(a: dict[str, Any]) -> str:
    """The tool's whole answer; a sentence when an argument is refused or nothing has been pulled."""
    try:
        args = _args(a)
        asof = store.as_of()
        if not asof["last_pull"]:
            return "No Azure inventory has been pulled yet."
        body = advise.run(**args)
    except advise.Refused as e:
        return str(e)
    return _header(asof) + "\n\n" + json.dumps(_clean(body), indent=1)


class AzureAdvise(Tool):
    async def execute(self, **kwargs) -> Response:
        # off the event loop: the list price may wait on the network
        return Response(message=await asyncio.to_thread(answer, self.args or {}), break_loop=False)
