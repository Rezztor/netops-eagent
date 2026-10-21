from typing import Any

from helpers import login, rbac, users
from helpers.api import ApiHandler, Request, Response


def _audit(event: str, **fields):
    """Audit without letting a logging failure fail the operation."""
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:
        pass


class UsersDelete(ApiHandler):
    """Delete an account. Administrators only."""

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_USERS_MANAGE

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        username = input.get("username", "")
        if not username:
            return {"ok": False, "error": "Username is required."}
        try:
            users.delete_user(username, actor=login.current_username() or "unknown")
        except users.UserError as exc:
            return {"ok": False, "error": str(exc)}
        _audit("account.delete", target=users.normalize_username(username))
        return {"ok": True, "deleted": users.normalize_username(username)}
