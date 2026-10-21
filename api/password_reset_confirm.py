"""Spend a reset link and set a new password. Unauthenticated, by definition.

The token is the whole credential: whoever holds it has proved they can read the
mailbox on the account, which is the only thing anybody has to go on when the
password is the thing being replaced.

The flow lives in `helpers/password_reset.complete`, shared with the
server-rendered page the emailed link points at.
"""

from typing import Any

from helpers import password_reset
from helpers.api import ApiHandler, Request, Response


def _audit(event: str, **fields):
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:
        pass


class PasswordResetConfirm(ApiHandler):
    """Set a new password against a token issued by password_reset_request."""

    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        ok, message = password_reset.complete(
            str(input.get("token") or "").strip(),
            str(input.get("new_password") or ""),
        )
        _audit("password_reset.completed" if ok else "password_reset.rejected", via="api")
        return {"ok": ok, "message": message} if ok else {"ok": False, "error": message}
