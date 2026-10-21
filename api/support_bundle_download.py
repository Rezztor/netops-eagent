"""Download one support bundle the host made (Settings, Support bundle). Administrators only.

The bundle is the machine's logs and settings, cleaned of secrets on the host (`rezztor-update`, "the support
bundle"). It is the customer's: the administrator downloads it, reads it if they like, and sends it to Rezztor.
Each download is audited.
"""

from helpers import login, rbac, update_spool
from helpers.api import ApiHandler, Request, Response, send_file


class SupportBundleDownload(ApiHandler):
    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET"]

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_SETTINGS_MANAGE

    async def process(self, input: dict, request: Request) -> dict | Response:
        name = str(request.args.get("file") or input.get("file") or "")
        try:
            path = update_spool.bundle_path(name)
        except update_spool.UpdateRefused as refused:
            return Response(str(refused), status=404, mimetype="text/plain")
        _audit(name)
        return send_file(path, as_attachment=True, download_name=name, mimetype="application/gzip")


def _audit(name: str) -> None:
    try:
        from plugins._audit.helpers import audit

        user = login.current_user() or {}
        audit.emit("support_bundle.download", file=name, by=str(user.get("username") or ""))
    except Exception:  # noqa: BLE001 - auditing never fails the download
        pass
