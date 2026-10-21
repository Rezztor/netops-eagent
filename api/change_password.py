from typing import Any

from helpers import login, users
from helpers.api import ApiHandler, Request, Response


def _audit(event: str, **fields):
    """Audit without letting a logging failure fail the operation."""
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:
        pass


class ChangePassword(ApiHandler):
    """Let the signed-in user change their own password. Any authenticated role."""

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        user = login.current_user()
        if user is None:
            return {"ok": False, "error": "Not authenticated."}

        current_password = input.get("current_password") or ""
        new_password = input.get("new_password") or ""

        # Re-verify the current password: a hijacked session must not be enough
        # to lock the real owner out of their account.
        if not users.verify_password(current_password, user.get("password_hash", "")):
            return {"ok": False, "error": "Current password is incorrect."}
        try:
            updated = users.update_user(
                user["username"],
                password=new_password,
                # The holder has now chosen a password nobody else has seen.
                must_change_password=False,
                actor=user["username"],
            )
        except users.UserError as exc:
            return {"ok": False, "error": str(exc)}
        # The change ended every session of this account, this one included.
        # Re-issue this one; the others stay ended.
        login.login_session(updated)
        _audit("account.password_changed", target=user["username"], self_service=True)
        return {"ok": True}
