"""Serve one attachment to the person it belongs to.

The owner comes from the session and never from the request. There is no user
id in the payload to forget to check: an id lifted from someone else's
transcript resolves against the caller's own directory, where it does not exist,
and comes back 404. That is the whole isolation story, and it is structural
rather than a check that could be removed.

Open to any signed-in user, deliberately -- an attachment is a file this account
was given in its own conversation. Gating it on plugins.manage would mean an
administrator had to fetch every file for everybody.
"""

from __future__ import annotations

from typing import Any

from helpers import user_scope
from helpers.api import ApiHandler, Request, Response
from plugins._ea_attachments.helpers import store


class AttachmentGet(ApiHandler):
    @classmethod
    def get_methods(cls) -> list[str]:
        # GET so the browser can fetch it as a download; there is nothing to
        # change, so there is nothing for CSRF to protect.
        return ["GET", "POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    async def process(self, input: dict, request: Request) -> dict[str, Any] | Response:
        attachment_id = str(
            (input or {}).get("id") or request.args.get("id") or ""
        ).strip()

        owner = user_scope.get_current_user_id()
        if not user_scope.is_safe_user_id(owner):
            return Response("Not signed in.", status=401, mimetype="text/plain")

        found = store.locate(owner, attachment_id)
        if not found:
            return Response("No such attachment.", status=404, mimetype="text/plain")

        path, record = found
        with open(path, "rb") as handle:
            content = handle.read()

        filename = str(record.get("filename") or "attachment")
        return Response(
            content,
            status=200,
            # A byte stream with an explicit download disposition. Serving a
            # guessed content type would let an uploaded .html render in the
            # user's own origin, which is a stored-XSS shape.
            mimetype="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "Content-Length": str(len(content)),
            },
        )
