"""Start a password reset. Unauthenticated, by definition.

The caller is somebody locked out of their account, so there is no session to
read and nothing to check them against. That makes this the platform's one
unauthenticated way to cause an email to be sent, and the rule below follows from
that rather than from any general principle.

**It always reports success.** Answering differently for a registered address
turns this into a membership oracle: anyone could learn which of a list of people
hold accounts here. The response is identical whether an account was found, the
account has no address on file, mail is switched off, or the send failed. What
actually happened goes to the audit log, where an administrator can see it and a
stranger cannot.

**It answers before doing anything.** The lookup and the send run on a worker
(`password_reset.send_link_later`), so a registered identifier does not take an
SMTP round trip longer to answer than an unknown one.

The flow lives in `helpers/password_reset.send_link`, shared with the
server-rendered page the sign-in screen links to.
"""

import json
from typing import Any

from helpers import mailer, password_reset
from helpers.api import ApiHandler, Request, Response


# One reply, whatever happened.
_ANSWER = {
    "ok": True,
    "message": (
        "If that account exists and has an email address on file, "
        "a reset link is on its way."
    ),
}


_TOO_MANY = "Too many reset requests from this address. Try again in a few minutes."


class PasswordResetRequest(ApiHandler):
    """Email a reset link to an account's own registered address."""

    @classmethod
    def requires_auth(cls) -> bool:
        # The caller cannot sign in. That is the entire situation.
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        # Follows requires_auth by default; stated because turning it on would
        # need a token the sign-in page has no session to carry.
        return False

    async def process(self, input: dict[Any, Any], request: Request) -> dict[Any, Any] | Response:
        # Per source address, before anything is looked up. Says nothing about
        # any account: it is the same answer whatever was named.
        if not password_reset.address_may_request(
            getattr(request, "remote_addr", None),
            getattr(request, "headers", {}).get("X-Forwarded-For"),
        ):
            password_reset.audit_refused("api")
            return Response(
                response=json.dumps({"ok": False, "error": _TOO_MANY}),
                status=429,
                mimetype="application/json",
            )
        identifier = str(input.get("identifier") or "").strip()
        password_reset.send_link_later(identifier, mailer.config()["base_url"], via="api")
        return _ANSWER
