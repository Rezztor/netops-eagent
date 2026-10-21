"""The licence page's actions: activate, check in, release, the offline request code and the offline licence file.

The write side of the administrator's licence page; `license_status.py` is the read side. `settings.manage`
only: this claims and releases an installation slot of the customer's licence, which is not a per-user action.

Every action is an administrator's button, which is why it may wait for the licence server at all: a user's
request (a chat send, a page load) never does. The call runs on a worker thread so the wait does not hold the
event loop.

The activation key arrives in `key` and goes to `helpers.license_client` and nowhere else: not into the
answer, a log line or the audit record. The installation token never leaves that module.
"""

import asyncio
from typing import Any

from helpers import license_client, rbac
from helpers.api import ApiHandler, Input, Output, Request
from helpers.license_entitlements import licence_status

MAX_FILE = 16384  # a licence file is about one kilobyte


class LicenseActivate(ApiHandler):
    """activate | checkin | deactivate | request_code | install."""

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_SETTINGS_MANAGE

    async def process(self, input: Input, request: Request) -> Output:
        action = str(input.get("action") or "").strip().lower()

        if action == "request_code":
            # No network call and nothing stored: it restates what the installation knows about itself.
            from rezlic.request_code import RequestCodeError

            try:
                code = license_client.request_code(str(input.get("deployment_name") or "").strip()[:80])
            except RequestCodeError as exc:
                return {"ok": False, "error": exc.reason}
            return {"ok": True, "request_code": code, "licence": licence_status()}

        if action == "activate":
            invoke = lambda: license_client.activate(str(input.get("key") or "")[:128])
        elif action == "checkin":
            invoke = license_client.checkin
        elif action == "deactivate":
            if input.get("confirm") is not True:
                return {"ok": False, "error": "Releasing the installation needs a confirmation."}
            invoke = license_client.deactivate
        elif action == "install":
            text = input.get("licence")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_FILE:
                return {"ok": False, "error": "That is not a licence file."}
            invoke = lambda: license_client.load_licence_file(text)
        else:
            return {"ok": False, "error": "Unknown action."}

        error = None
        previous: str | None = None
        try:
            returned = await asyncio.to_thread(invoke)
            if action == "activate" and isinstance(returned, str):
                previous = returned
        except license_client.LicenceError as exc:
            error = exc.reason
        status = licence_status()
        # A switch that confirmed the previous release names it here. While that release is still pending,
        # `releasing` on the status is the same id, and the token is in neither.
        if previous and not status.get("releasing"):
            status["released_lic"] = previous
        _audit(action, error, status)
        if error:
            return {"ok": False, "error": error, "licence": status}
        return {"ok": True, "licence": status}


def _audit(action: str, error: str | None, status: dict[str, Any]) -> None:
    """Who did what to the licence, and how it ended. The licence id is not secret; the key and token are, and
    are not here."""
    try:
        from plugins._audit.helpers import audit

        audit.emit(
            f"licence.{action}",
            outcome="failed" if error else "ok",
            detail=error or "",
            lic=(status.get("licence") or {}).get("lic", ""),
            state=status["state"],
        )
    except Exception:  # noqa: BLE001 - an audit sink must not fail the action
        pass
