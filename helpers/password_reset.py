"""Single-use tokens for a password reset a locked-out user starts themselves.

Deliberately not reachable by the agent. Self-service only: the
"Forgot password?" flow on the sign-in page issues these, and nothing in a
conversation can. An agent that could mint a credential-bearing link would be a
credential-bearing link away from an account takeover every time a web page it
read told it to make one.

Three properties do the work here.

**Only the hash is stored.** A token is 32 random bytes shown once, in one
email. `usr/password_resets.json` holds its SHA-256, so a copy of that file is
not a set of working links -- the same reason password_hash exists two modules
over.

**A token is spent on use.** Consuming one marks it used inside the lock and
before the caller can act on it, so two requests racing the same link cannot both
succeed.

**A request says nothing about who is registered.** The API always reports
success. That belongs in the endpoint, but it constrains this module too: no
call here distinguishes "no such address" from "sent" in anything it returns to
an unauthenticated caller.

**An administrator's switch does not become a fourth way to leak that.**
`password_reset_enabled` in settings can turn the flow off for everyone except
administrators -- who are exempt so the setting can never lock out the person
who controls it. `send_link`'s caller still sees the same message regardless,
so a request for an admin's username silently succeeding while a standard
user's is refused never reaches the page.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from helpers import files

STORE_PATH = "usr/password_resets.json"

# Long enough to walk to another device and read the mail; short enough that a
# link left in a mailbox stops working the same morning.
TOKEN_LIFETIME_SECONDS = 30 * 60

# One address cannot be mailed more often than this. A reset request is an
# unauthenticated way to make the platform send mail, so it is a way to use the
# platform to harass whoever owns the address.
REQUEST_INTERVAL_SECONDS = 60

# Belt and braces against a store that grows forever if nothing ever expires it.
MAX_RECORDS = 500

# One source address may ask this many times per window, whatever it names. The
# interval above protects one mailbox; this stops one caller walking a list of
# names, each a lookup, a token and a mail.
ADDRESS_REQUESTS = 5
ADDRESS_WINDOW_SECONDS = 15 * 60
_MAX_ADDRESSES = 4096
_address_lock = threading.Lock()
_by_address: dict[str, list[float]] = {}



class ResetError(Exception):
    pass


def _now() -> float:
    return time.time()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _connection():
    from helpers import db

    return db.transaction(None)


def _prune(conn: Any) -> None:
    """Drop what is spent or expired, then anything past the cap.

    Belt and braces against a store that grows forever if nothing expires it --
    the same reason MAX_RECORDS existed when this was a file, kept because the
    reason has not changed.
    """
    conn.execute(
        "DELETE FROM password_resets WHERE consumed_at IS NOT NULL OR expires_at <= %s",
        (_now(),),
    )
    conn.execute(
        """
        DELETE FROM password_resets WHERE digest IN (
            SELECT digest FROM password_resets ORDER BY issued_at DESC OFFSET %s
        )
        """,
        (MAX_RECORDS,),
    )


def _address_key(address: str | None, forwarded_for: str | None) -> str | None:
    """Whom to count a request against, or None when nobody can be told apart.

    The peer, unless it is a proxy on this host that uvicorn was not told about
    (`login_throttle._is_unnamed_proxy`); then the last `X-Forwarded-For` hop,
    the one that proxy appended. An IPv6 address counts as its /64, which one
    caller normally holds whole.
    """
    import ipaddress

    from helpers.login_throttle import _is_unnamed_proxy

    key = (address or "unknown").strip().lower()
    if _is_unnamed_proxy(key):
        key = (forwarded_for or "").split(",")[-1].strip().lower()
        try:
            ipaddress.ip_address(key)
        except ValueError:
            return None
    try:
        ip = ipaddress.ip_address(key)
    except ValueError:
        return key
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return key


def address_may_request(address: str | None, forwarded_for: str | None = None) -> bool:
    """Count one request from `address`; False once it has used its allowance.

    In memory, like `helpers.login_throttle` and for its reasons: one host, and a
    database write per request would be a cheaper flood than the one it stops.
    Behind a proxy on this host that uvicorn was not told about
    (`FORWARDED_ALLOW_IPS` empty) every browser shares the proxy's address, so
    the proxy's own `X-Forwarded-For` hop is counted instead, and with none the
    limit is not applied rather than letting one caller close the form for the
    whole instance.
    """
    key = _address_key(address, forwarded_for)
    if key is None:
        return True
    now = _now()
    with _address_lock:
        recent = [t for t in _by_address.get(key, []) if now - t < ADDRESS_WINDOW_SECONDS]
        allowed = len(recent) < ADDRESS_REQUESTS
        if allowed:
            recent.append(now)
        _by_address[key] = recent
        if len(_by_address) > _MAX_ADDRESSES:
            stalest = sorted(_by_address, key=lambda k: max(_by_address[k], default=0.0))
            for stale in stalest[: len(_by_address) - _MAX_ADDRESSES]:
                del _by_address[stale]
    return allowed


def forget_addresses() -> None:
    """For tests."""
    with _address_lock:
        _by_address.clear()


def audit_refused(via: str) -> None:
    try:
        from plugins._audit.helpers import audit

        audit.emit("password_reset.requested", outcome="address_rate_limited", via=via)
    except Exception:
        pass


def recently_requested(user_id: str) -> bool:
    """Whether this account was sent a link a moment ago.

    Checked before sending rather than after, so a flood costs one message and
    not one per request.
    """
    with _connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM password_resets WHERE user_id = %s AND issued_at > %s LIMIT 1",
            (user_id, _now() - REQUEST_INTERVAL_SECONDS),
        ).fetchone()
    return bool(row)


def issue(user_id: str) -> tuple[str, int]:
    """A fresh token for this account, and how long it lives.

    Any token already outstanding for the account is discarded. Asking again
    should replace the link, not leave two working -- a user who requests twice
    because the first mail was slow will use the second, and the first should
    not still be usable an hour later.
    """
    token = secrets.token_urlsafe(32)
    now = _now()
    with _connection() as conn:
        _prune(conn)
        conn.execute("DELETE FROM password_resets WHERE user_id = %s", (user_id,))
        conn.execute(
            """
            INSERT INTO password_resets (digest, user_id, issued_at, expires_at)
            VALUES (%s, %s, %s, %s)
            """,
            (_digest(token), user_id, now, now + TOKEN_LIFETIME_SECONDS),
        )
    return token, TOKEN_LIFETIME_SECONDS


def consume(token: str) -> str:
    """Spend a token and return whose account it is for.

    Marks it used before returning, inside the lock, so two requests carrying the
    same link cannot both come back with a user id.
    """
    candidate = str(token or "").strip()
    if not candidate:
        raise ResetError("This reset link is not valid.")
    wanted = _digest(candidate)

    with _connection() as conn:
        now = _now()
        # Looked up by digest, which is the primary key -- the file version had
        # to scan, and compared in constant time so the scan did not leak which
        # prefix was right. An index lookup on a hash has nothing to leak: every
        # digest is the same length and the input is already hashed.
        #
        # Locked, so two requests carrying the same link cannot both come back
        # with a user id. The file version held a thread lock, which bound one
        # process; this binds the row.
        row = conn.execute(
            "SELECT user_id, expires_at, consumed_at FROM password_resets "
            "WHERE digest = %s FOR UPDATE",
            (wanted,),
        ).fetchone()
        if row is None:
            raise ResetError("This reset link is not valid.")
        user_id, expires_at, consumed_at = row
        if consumed_at is not None:
            raise ResetError("This reset link has already been used.")
        if float(expires_at) <= now:
            raise ResetError("This reset link has expired.")
        conn.execute(
            "UPDATE password_resets SET consumed_at = %s WHERE digest = %s", (now, wanted)
        )
        return str(user_id or "")


def revoke_for_user(user_id: str) -> int:
    """Drop every outstanding link for an account.

    Called when the password changes by any other route. A link issued before a
    successful sign-in and password change should not still work afterwards.
    """
    with _connection() as conn:
        _prune(conn)
        return int(
            conn.execute(
                "DELETE FROM password_resets WHERE user_id = %s", (user_id,)
            ).rowcount
        )


# --- the flow itself --------------------------------------------------------
#
# Here rather than in the API handler because there are two front doors: the
# JSON endpoints, and the server-rendered pages the sign-in screen links to. A
# security flow implemented twice is a security flow with two behaviours, and the
# one that gets reviewed is whichever the reader happened to open.


def send_link(identifier: str, base_url: str) -> str:
    """Mail a reset link for whoever `identifier` names. Never raises.

    Returns an outcome for the audit log. The **caller must not vary its reply**
    on it: answering differently for a registered address turns this into a way
    to discover who holds an account here.
    """
    from helpers import mailer, rbac, settings, users
    from helpers.print_style import PrintStyle

    wanted = str(identifier or "").strip()
    if not wanted:
        return "empty"

    record = users.get_user(wanted) or users.get_user_by_email(wanted)
    if record is None or record.get("disabled"):
        return "no_such_account"

    # An administrator's own switch. Checked after the account is found, not
    # before, so the answer to the caller stays the constant "if that account
    # exists..." message regardless -- turning this off must not become a second
    # way to learn who holds an account.
    #
    # Administrators are exempt on purpose: this is the deployment's one
    # self-service recovery path, and a setting an administrator can use to lock
    # themselves out of their own account is a setting that turns a
    # misconfiguration into an outage with no way back in through the UI. The
    # same reasoning already guards the last administrator in
    # helpers/users.py:update_user.
    if not settings.get_settings().get("password_reset_enabled", True) and not rbac.is_admin(
        record.get("role")
    ):
        return "disabled"

    if recently_requested(record["id"]):
        return "rate_limited"

    try:
        recipient = mailer.resolve_recipient(record["id"])
    except mailer.MailError:
        return "no_address"

    if not base_url:
        # A link needs an origin this process cannot infer safely: the Host
        # header is attacker-controlled, and a reset link built from it points at
        # somebody else's server. An administrator sets it or this does not run.
        PrintStyle.error("Password reset: smtp_base_url is not set, so no link can be built.")
        return "no_base_url"

    token, lifetime = issue(record["id"])
    minutes = max(1, lifetime // 60)
    link = f"{base_url.rstrip('/')}/reset-password?token={token}"

    try:
        mailer.send_to_user(
            record["id"],
            subject="Reset your Rezztor EAgent password",
            body=(
                f"Hello {recipient.display_name},\n\n"
                "Someone asked to reset the password for your Rezztor EAgent "
                f"account ({recipient.username}). To choose a new one, open:\n\n"
                f"{link}\n\n"
                f"The link works once and expires in {minutes} minutes.\n\n"
                "If this was not you, you can ignore this message. Your password "
                "has not changed, and the link is useless without this mailbox."
            ),
        )
    except mailer.MailError as error:
        PrintStyle.error(f"Password reset: could not send to an account address: {error}")
        return "send_failed"

    return "sent"


# Two workers: requests queue rather than each holding a thread, so a flood
# costs memory for the queue and never more than two database connections.
_sender = ThreadPoolExecutor(max_workers=2, thread_name_prefix="password-reset")


def send_link_later(identifier: str, base_url: str, via: str) -> None:
    """`send_link` off the request, with its audit event; returns at once.

    The reply was constant but its timing was not: an unknown identifier
    answered after a lookup, a registered one after a token insert and an SMTP
    delivery, and that difference told a stranger who holds an account here.
    Both front doors answer before any of that work starts.
    """

    def run() -> None:
        outcome = send_link(identifier, base_url)
        try:
            from plugins._audit.helpers import audit

            audit.emit("password_reset.requested", outcome=outcome, via=via)
        except Exception:
            pass

    _sender.submit(run)


def complete(token: str, new_password: str) -> tuple[bool, str]:
    """Spend a token and set the password. Returns (ok, message to show)."""
    from helpers import users

    try:
        # Checked before the token is spent: failing this is the user's own
        # typing, and it should not cost them the link. Every rule, not only
        # the shortest length: one too long for the hash passed, spent the
        # link and then failed.
        users.check_password(new_password)
    except users.UserError as error:
        return (False, str(error).replace("Password must", "The new password must"))

    try:
        user_id = consume(token)
    except ResetError as error:
        return (False, str(error))

    record = users.get_user_by_id(user_id)
    if record is None or record.get("disabled"):
        # The account went away between the mail and the click.
        return (False, "This reset link is not valid.")

    users.update_user(
        record["username"],
        password=new_password,
        # The password is now one only its holder has chosen and seen, so there
        # is nothing left to force a change of at next sign-in.
        must_change_password=False,
        actor="password-reset",
    )
    revoke_for_user(user_id)
    return (True, "Your password has been changed. You can sign in now.")
