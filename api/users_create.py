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


class UsersCreate(ApiHandler):
    """Create an account with a role. Administrators only."""

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_USERS_MANAGE

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        password = input.get("password") or ""
        generated = False
        if not password:
            password = users.generate_password()
            generated = True
        try:
            created = users.create_user(
                username=input.get("username", ""),
                password=password,
                role=input.get("role", rbac.DEFAULT_ROLE),
                display_name=input.get("display_name", ""),
                email=input.get("email", ""),
                created_by=login.current_username() or "unknown",
            )
        except users.UserError as exc:
            return {"ok": False, "error": str(exc)}

        _audit(
            "account.create",
            target=created["username"],
            target_id=created["id"],
            role=created["role"],
            password_generated=generated,
        )

        result: dict[str, Any] = {"ok": True, "user": created}
        if generated:
            # Returned once so the administrator can hand it over; never stored
            # in plaintext and never retrievable again.
            result["generated_password"] = password
        return result
