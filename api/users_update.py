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


class UsersUpdate(ApiHandler):
    """Change an account's role, password, profile, or enabled state.
    Administrators only."""

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_USERS_MANAGE

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        username = input.get("username", "")
        if not username:
            return {"ok": False, "error": "Username is required."}

        actor = login.current_username() or "unknown"
        new_password = input.get("password") or None
        generated = False

        # Password reset: the server generates one and returns it exactly once,
        # matching account creation. Administrators never choose a password that
        # someone else will go on using.
        if not new_password and input.get("generate_password"):
            new_password = users.generate_password()
            generated = True

        resetting_someone_else = bool(new_password) and users.normalize_username(
            username
        ) != users.normalize_username(actor)

        try:
            updated = users.update_user(
                username,
                role=input.get("role"),
                password=new_password,
                # An administrator resetting someone else's password has seen it,
                # so the holder must replace it. A self-reset does not re-arm.
                must_change_password=True if resetting_someone_else else None,
                display_name=input.get("display_name"),
                email=input.get("email"),
                disabled=input.get("disabled"),
                actor=actor,
            )
        except users.UserError as exc:
            return {"ok": False, "error": str(exc)}

        if new_password and not resetting_someone_else:
            # A password change ends every session of the account; keep the one
            # that made it.
            login.login_session(updated)

        _audit(
            "account.update",
            target=updated["username"],
            target_id=updated["id"],
            role=updated["role"],
            disabled=updated["disabled"],
            password_changed=bool(new_password),
            password_generated=generated,
        )

        result: dict[str, Any] = {"ok": True, "user": updated}
        if generated:
            result["generated_password"] = new_password
        return result
