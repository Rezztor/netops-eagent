"""Serve the OpenAPI document for this instance.

Generated on request from the handler classes rather than checked in, so it
cannot drift from the endpoints actually being served -- a plugin that adds an
endpoint appears here without anyone regenerating a file.

Administrator-only. The document enumerates every endpoint together with the
permission each one requires, which is a map of the instance's privileged
surface. That is not secret from a developer with repository access, but there
is no reason to hand it to an ordinary signed-in user either.
"""

from typing import Any

from helpers import api_schema, rbac
from helpers.api import ApiHandler, Input, Output, Request


class OpenApi(ApiHandler):
    """OpenAPI 3.1 document describing every endpoint this instance serves."""

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET"]

    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_SETTINGS_MANAGE

    async def process(self, input: Input, request: Request) -> Output:
        version = ""
        try:
            from helpers import git

            version = str(git.get_git_info().get("version") or "")
        except Exception:  # noqa: BLE001 - the document is useful without a version
            pass
        document: dict[str, Any] = api_schema.build_openapi(version=version)
        return document
