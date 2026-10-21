"""Test connection: sign in, list the subscriptions the application can read, count the resources visible to it.
Stores nothing. Gated on `plugins.manage` in `helpers/api_policy.py`. Refused while the plugin is off.

Every outcome the endpoint handled is `ok: true`; the answer says what happened in `signed_in` and `reason`:
the client's `tenant | application | certificate | secret | expired | network | other`, and this endpoint's own
`disabled | not_configured | no_role | read`. `message` is the plugin's own sentence, never Azure's text.
"""

from __future__ import annotations

import asyncio
from typing import Any

from helpers.api import ApiHandler, Request, Response
from plugins._audit.helpers import audit
from plugins._ea_azure.helpers import client, config, queries


def _answer(signed_in: bool, message: str, reason: str | None = None, subs: list | None = None, resources: int = 0) -> dict:
    out = {"ok": True, "signed_in": signed_in, "message": message, "subscriptions": subs or [], "resources": resources}
    return {**out, "reason": reason} if reason else out


def _probe() -> dict:
    client.reset()  # a test that reused yesterday's token would prove nothing
    try:
        client.token()
    except client.NotConfigured as e:
        return _answer(False, str(e), "not_configured")
    except client.SignInFailed as e:
        return _answer(False, str(e), e.reason)
    try:
        listed = client.subscriptions()
    except client.AzureError as e:
        return _answer(True, f"Signed in, but the subscription list could not be read. {e}", "read")
    subs = [{"id": s["id"], "name": s["name"], "state": s["state"]} for s in listed]
    if not subs:
        return _answer(True, "Signed in, but this application can read no subscription. Give it the Reader role.", "no_role")
    try:
        total = sum(int(row.get("total") or 0) for page in client.graph(queries.COUNT, [s["id"] for s in subs]) for row in page)
    except client.AzureError as e:
        return _answer(True, f"Signed in and listed the subscriptions, but counting resources failed. {e}", "read", subs)
    return _answer(True, f"Signed in. {len(subs)} subscription(s) readable, {total} resources visible.", None, subs, total)


class AzureTest(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        if not config.is_enabled():
            result = _answer(False, "Azure is off. Turn it on in Plugins first.", "disabled")
        else:
            result = await asyncio.to_thread(_probe)
        audit.emit(
            "azure.connection_tested", result=result.get("reason") or "ok", signed_in=result["signed_in"],
            subscriptions=len(result["subscriptions"]),
        )
        return result
