from helpers import rbac
from helpers.api import ApiHandler, Request, Response
from helpers.license_entitlements import licence_status


class GetLicenseStatus(ApiHandler):
    """The installation's licence verdict, for the administrator's licence page.

    Read-only, and makes no network call: the verdict is worked out from the signed documents this installation
    holds. `license_activate.py` is the write side and the only one that talks to the licence server. The
    installation token and the activation key are in no field of the answer.
    """

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_SETTINGS_MANAGE

    async def process(self, input: dict, request: Request) -> dict | Response:
        return {"ok": True, "licence": licence_status()}
