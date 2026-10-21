from helpers import rbac, update_spool
from helpers.api import ApiHandler, Request, Response


class UpdateStatus(ApiHandler):
    """The appliance's update service as the administrator's Updates page shows it.

    Read-only: the state file the host tool `rezztor-update` writes, with the packages Rezztor's update service
    listed when the host last asked (ADR 0091), and the daily-check switch. `update_apply.py` is the write side.
    An installation without the host tool answers `{"available": false}`.
    """

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET", "POST"]

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_SETTINGS_MANAGE

    async def process(self, input: dict, request: Request) -> dict | Response:
        answer = update_spool.status()
        if answer["available"]:
            answer["daily_check"] = update_spool.daily_check_enabled()
        return answer
