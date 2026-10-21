from typing import Any

from helpers import rbac, users
from helpers.api import ApiHandler, Request, Response


class UsersList(ApiHandler):
    """List all accounts and the available roles. Administrators only."""

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_USERS_MANAGE

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        return {
            "ok": True,
            "users": users.list_users(),
            "roles": rbac.list_roles(),
        }
