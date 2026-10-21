"""Email somebody who holds an account here.

The tool takes a **username**, never an address. `helpers/mailer.py` turns that
into an address by reading the user store, so there is no argument to this tool
that can name a mailbox outside the organisation -- not a mistyped one, and not
one an instruction hidden in a document asked for.

Every message carries a footer naming the account it was sent on behalf of. The
agent cannot suppress it. A recipient who gets something odd should be able to
see at a glance that a person asked for it and which person, rather than having
to work out whether the platform decided to write to them on its own.
"""

from __future__ import annotations

from helpers import mailer, user_scope, users
from helpers.parallel_tools import user_facing_context
from helpers.tool import Response, Tool

from plugins._ea_mail.helpers import policy


def _audit(event: str, **fields):
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:
        pass


class SendUserEmail(Tool):
    async def execute(self, to: str = "", subject: str = "", body: str = "", **kwargs) -> Response:
        to = str(to or self.args.get("to") or "").strip()
        subject = str(subject or self.args.get("subject") or "").strip()
        body = str(body or self.args.get("body") or "").strip()

        available, reason = mailer.is_available()
        if not available:
            return Response(
                message=(
                    f"Cannot send email: {reason} An administrator configures this "
                    "under Settings."
                ),
                break_loop=False,
            )

        sender_id = user_scope.get_current_user_id()
        if not user_scope.is_safe_user_id(sender_id):
            return Response(
                message="Cannot send email outside a signed-in conversation.",
                break_loop=False,
            )

        try:
            target = policy.resolve_target(to, sender_id)  # type: ignore[arg-type]
            policy.check_and_record(sender_id)  # type: ignore[arg-type]
        except policy.Refused as refusal:
            return Response(message=str(refusal), break_loop=False)

        sender = users.get_user_by_id(sender_id) or {}  # type: ignore[arg-type]
        sender_name = sender.get("display_name") or sender.get("username") or "a colleague"

        try:
            recipient = mailer.send_to_user(
                target["id"],
                subject=subject,
                body=body,
                footer=(
                    "—\n"
                    f"Sent by {sender_name} ({sender.get('username')}) through "
                    "Rezztor EAgent. Reply to this person, not to this address."
                ),
            )
        except mailer.MailError as error:
            _audit(
                "mail.failed",
                sender=sender.get("username"),
                recipient=target.get("username"),
                detail=str(error),
            )
            return Response(message=f"Could not send: {error}", break_loop=False)

        # The address is deliberately not in the audit record or the reply: the
        # agent named an account, and telling it the mailbox would hand back a
        # colleague's address it had no way to ask for.
        _audit(
            "mail.sent",
            sender=sender.get("username"),
            recipient=recipient.username,
            subject=subject,
        )

        # Reported to the agent by username, for the same reason.
        surface = user_facing_context(self.agent)
        surface.log.log(
            type="info",
            heading=f"Emailed {recipient.username}",
            content=f"“{subject}” was sent to {recipient.display_name}.",
        )

        return Response(
            message=(
                f"Sent “{subject}” to {recipient.username} at the address on their "
                "account."
            ),
            break_loop=False,
        )
