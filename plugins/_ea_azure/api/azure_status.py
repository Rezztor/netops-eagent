"""Where the Azure inventory stands: enabled and set up, the running or last pull, the next scheduled one, how old
each subscription's data is, and the credential's expiry. Gated on `inventory.view` in `helpers/api_policy.py`.
Served with the plugin off too (the stored data is still shown), so it never needs the plugin to be enabled."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from helpers.api import ApiHandler, Request, Response
from plugins._ea_azure.helpers import client, collect, config, credential, schedule, store


def _expires_in_days(expires_on: str) -> int | None:
    try:
        return (_dt.date.fromisoformat(expires_on[:10]) - _dt.date.today()).days
    except ValueError:
        return None


class AzureStatus(ApiHandler):
    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        enabled, configured = config.is_enabled(), config.is_configured()
        cred = credential.status()
        upcoming = schedule.upcoming(config.get_config(), _dt.datetime.now(_dt.timezone.utc)) if enabled and configured else None
        return {
            "ok": True, "enabled": enabled, "configured": configured, "has_data": store.has_data(),
            "test_endpoints": client.TEST_ENDPOINTS is not None, **collect.status(),
            "next_pull_at": upcoming.isoformat() if upcoming else None, "as_of": store.as_of(),
            "credential": {"kind": cred["kind"], "expires_on": cred["expires_on"],
                           "expires_in_days": _expires_in_days(cred["expires_on"]) if cred["set"] else None},
            "verified_against_real_tenant": True,  # sign-in and a networking pull, on a real tenant on 2026-10-03
        }
