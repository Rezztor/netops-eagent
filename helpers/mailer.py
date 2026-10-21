"""Sending mail to the people who hold accounts here.

Not a mail client. This platform sends to one kind of recipient -- an account on
this instance -- and the address is never supplied by the caller. Callers name a
**user id**; `resolve_recipient` turns that into an address by reading the user
store, and `send_to_user` is the only way out of this module that reaches SMTP.

The reason is the agent. It composes the body of these messages, and it composes
it from whatever ended up in its context: a web page, an uploaded document, a
tool result. If a caller could pass an address, then a sentence buried in a PDF
could address a message to somewhere outside the organisation, and the platform
would send it under its own name. Naming a user id instead means the worst an
injected instruction can do is mail the wrong colleague -- bad, and bounded, and
recorded in the audit log either way.

`_send` takes an address rather than an id, because password reset has to reach
an account that cannot sign in to name itself. It is private for that reason.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from helpers import users
from helpers.print_style import PrintStyle

# Deliberately permissive: one @, no spaces, a dot in the domain. Address syntax
# is far larger than any regex people write for it, and the useful check here is
# "did somebody paste a sentence into this field", not RFC 5321 conformance. The
# real validation is that the address belongs to an account.
_ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")

MAX_SUBJECT = 200
MAX_BODY = 20_000


class MailError(Exception):
    """Something the caller should see: not configured, no address, refused."""


class NotConfigured(MailError):
    pass


@dataclass(slots=True)
class Recipient:
    user_id: str
    username: str
    display_name: str
    address: str


def is_address_shaped(value: str) -> bool:
    return bool(_ADDRESS.match(str(value or "").strip()))


def config() -> dict:
    from helpers import dotenv, settings

    current = settings.get_settings()
    return {
        "enabled": bool(current.get("smtp_enabled")),
        "host": str(current.get("smtp_host") or "").strip(),
        "port": int(current.get("smtp_port") or 0) or 587,
        "security": str(current.get("smtp_security") or "starttls").strip().lower(),
        "username": str(current.get("smtp_username") or "").strip(),
        # Read from the environment, never from settings.json -- the stored copy
        # is a placeholder, the same arrangement the RFC password uses.
        "password": dotenv.get_dotenv_value(dotenv.KEY_SMTP_PASSWORD) or "",
        "from_address": str(current.get("smtp_from_address") or "").strip(),
        "from_name": str(current.get("smtp_from_name") or "Rezztor EAgent").strip(),
        "base_url": str(current.get("smtp_base_url") or "").strip().rstrip("/"),
    }


def is_available() -> tuple[bool, str]:
    """Whether mail can be sent, and if not, what is missing.

    Returns a reason rather than a bare False so the interface can say which
    field an administrator has to fill in, instead of "email is not working".
    """
    settings = config()
    if not settings["enabled"]:
        return (False, "Email is turned off in settings.")
    if not settings["host"]:
        return (False, "No SMTP host is configured.")
    if not settings["from_address"]:
        return (False, "No sender address is configured.")
    if not is_address_shaped(settings["from_address"]):
        return (False, "The configured sender address is not a valid address.")
    return (True, "")


def resolve_recipient(user_id: str) -> Recipient:
    """The account's own address, or an error naming what is wrong with it.

    Every path to SMTP goes through here. An address that is not on an account
    cannot be reached by this module at all.
    """
    record = users.get_user_by_id(str(user_id or "").strip())
    if not record:
        raise MailError("No such account.")
    if record.get("disabled"):
        raise MailError(f"{record.get('username')} is disabled.")

    address = str(record.get("email") or "").strip()
    if not address:
        raise MailError(f"{record.get('username')} has no email address on file.")
    if not is_address_shaped(address):
        raise MailError(f"The address on {record.get('username')} is not a valid address.")

    return Recipient(
        user_id=record["id"],
        username=record["username"],
        display_name=record.get("display_name") or record["username"],
        address=address,
    )


def send_to_user(
    user_id: str,
    *,
    subject: str,
    body: str,
    footer: str = "",
) -> Recipient:
    """Send to one account. The only public route to SMTP that takes a body."""
    subject = str(subject or "").strip()
    body = str(body or "").strip()
    if not subject:
        raise MailError("A message needs a subject.")
    if not body:
        raise MailError("A message needs a body.")
    if len(subject) > MAX_SUBJECT:
        raise MailError(f"Subject is longer than {MAX_SUBJECT} characters.")
    if len(body) > MAX_BODY:
        raise MailError(f"Body is longer than {MAX_BODY} characters.")

    recipient = resolve_recipient(user_id)
    _send(recipient.address, subject=subject, body=body if not footer else f"{body}\n\n{footer}")
    return recipient


def _send(address: str, *, subject: str, body: str) -> None:
    """Hand one message to the SMTP server.

    Private. A caller that can name an arbitrary address is a caller that can
    mail outside the organisation, and the module exists to make that
    impossible from the agent's side.
    """
    available, reason = is_available()
    if not available:
        raise NotConfigured(reason)

    settings = config()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings["from_name"], settings["from_address"]))
    message["To"] = address
    message["Message-ID"] = make_msgid()
    # Automated mail. Without this, an out-of-office reply lands in the mailbox
    # that sent it and, where that mailbox is polled, starts a loop.
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)

    try:
        with _connect(settings) as server:
            if settings["username"]:
                server.login(settings["username"], settings["password"])
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        raise MailError("The mail server rejected the credentials.") from error
    except smtplib.SMTPException as error:
        raise MailError(f"The mail server refused the message: {error}") from error
    except OSError as error:
        raise MailError(f"Could not reach the mail server: {error}") from error

    PrintStyle.info(f"Mail: sent '{subject}' to an account address")


def _connect(settings: dict):
    host, port = settings["host"], settings["port"]
    timeout = 20

    if settings["security"] == "ssl":
        return smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())

    server = smtplib.SMTP(host, port, timeout=timeout)
    if settings["security"] != "none":
        # Default. `none` exists for an internal relay on a trusted network and
        # has to be chosen deliberately, because getting it by accident means
        # credentials cross the network in the clear.
        server.starttls(context=ssl.create_default_context())
    return server
