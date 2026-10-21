from typing import Any

from helpers import login, rbac
from helpers.api import ApiHandler, Request, Response


class WhoAmI(ApiHandler):
    """Identity and permissions of the signed-in user, for the WebUI to gate
    controls it should not render."""

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        user = login.current_user()
        if user is None:
            return {"ok": False, "error": "Not authenticated."}
        role = rbac.normalize_role(user.get("role"))
        return {
            "ok": True,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "display_name": user.get("display_name") or user["username"],
                "email": user.get("email") or "",
                "role": role,
                "role_title": rbac.ROLES[role]["title"],
                # Drives the forced-change interstitial in the WebUI.
                "must_change_password": bool(user.get("must_change_password")),
            },
            "permissions": sorted(rbac.get_permissions(role)),
        }
