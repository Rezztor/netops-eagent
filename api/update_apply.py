"""The Updates page's actions: upload a file, install it, restart, discard; ask Rezztor's update service what
follows, download the first package or walk to the latest; switch the daily check; a support bundle; and restart
the appliance when Debian's updates of a package need it (`reboot`, ADR 0092).

The write side of the administrator's Updates page; `update_status.py` is the read side. `settings.manage`
only. Nothing here installs or fetches anything: an upload is stored in the inbox the host reads, and every
action is one request file for the host tool `rezztor-update`, which talks to the update service, checks the
file's signature itself and decides (`helpers/update_spool.py`, ADR 0080, 0091).

An upload arrives as a form (`action=upload`, the file as `package`); the other actions as JSON:
`{"action": "download", "version": "..."}`, `{"action": "daily_check", "enabled": true|false}`.
"""

import asyncio

from helpers import login, rbac, settings, update_spool
from helpers.api import ApiHandler, Input, Output, Request


class UpdateApply(ApiHandler):
    """upload | install | restart | discard | bundle | refresh | download | update_all | reboot | daily_check."""

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_SETTINGS_MANAGE

    async def process(self, input: Input, request: Request) -> Output:
        action = str(request.form.get("action") or input.get("action") or "").strip().lower()
        user = login.current_user() or {}
        by = str(user.get("username") or "")
        extra: dict = {}
        try:
            if action == "upload":
                upload = request.files.get("package")
                if upload is None or not upload.filename:
                    return {"ok": False, "error": "No file was sent."}
                # A large file takes a while to write: off the event loop.
                await asyncio.to_thread(update_spool.store, upload.stream)
                ident = update_spool.request("check", by)
            elif action in ("install", "restart", "discard", "bundle", "refresh", "update_all", "reboot"):
                ident = update_spool.request(action, by)
            elif action == "download":
                version = input.get("version")
                extra = {"version": str(version)[:40]}
                ident = update_spool.request(action, by, version=version if isinstance(version, str) else None)
            elif action == "daily_check":
                enabled = input.get("enabled")
                if not isinstance(enabled, bool):
                    return {"ok": False, "error": "Say whether the daily check is on."}
                # An instance setting, not a request to the host: the job loop reads it (update_spool.daily_refresh).
                settings.set_settings_delta({update_spool.DAILY_SETTING: enabled}, apply=False)
                _audit(action, None, enabled=enabled)
                return {"ok": True, "daily_check": enabled}
            else:
                return {"ok": False, "error": "Unknown action."}
        except update_spool.UpdateRefused as refused:
            _audit(action, str(refused), **extra)
            return {"ok": False, "error": str(refused)}
        _audit(action, None, **extra)
        return {"ok": True, "request": ident}


def _audit(action: str, error: str | None, **fields) -> None:
    """Who asked the host for what. How the update ended is in the host's own history and on the page."""
    try:
        from plugins._audit.helpers import audit

        audit.emit(f"update.{action}", outcome="failed" if error else "ok", detail=error or "", **fields)
    except Exception:  # noqa: BLE001 - an audit sink must not fail the action
        pass
