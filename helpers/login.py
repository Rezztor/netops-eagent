"""Session-backed authentication facade for Rezztor EAgent.

Upstream compared a sha256 of a single AUTH_LOGIN/AUTH_PASSWORD pair and let
every request through when no credentials were configured. This module replaces
that with the account store in helpers.users, and authentication is mandatory:
there is no configuration in which requests are unauthenticated.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from typing import Any

from flask import session

from helpers import rbac, users

SESSION_USER_ID = "user_id"
SESSION_USERNAME = "username"
SESSION_ROLE = "role"
SESSION_PASSWORD_STAMP = "pwd"
SESSION_LOGIN_FORM_TOKEN = "login_form"
SESSION_LAST_SEEN = "seen"

#: A session unused this long ends, inside the cookie's 24-hour lifetime
#: (`PERMANENT_SESSION_LIFETIME` in helpers/ui_server.py).
IDLE_HOURS = float(os.environ.get("REZZTOR_SESSION_IDLE_HOURS") or 8)
#: How stale the last-seen time may get before a request writes it again: the
#: cookie is re-signed and re-sent on a write, so not on every request.
_SEEN_RESOLUTION_SECONDS = 60


def login_form_token() -> str:
    """The token the sign-in form carries back, kept in this browser's session.

    `POST /login` is outside the API dispatcher, so `csrf_protect` never sees
    it. Without a token a page on any site could submit the form with its own
    account's password and sign the visitor in as that account (login CSRF):
    what they then upload or paste lands where the other site reads it.
    """
    token = session.get(SESSION_LOGIN_FORM_TOKEN)
    if not token:
        token = session[SESSION_LOGIN_FORM_TOKEN] = secrets.token_urlsafe(32)
    return token


def login_form_token_valid(given: object) -> bool:
    from helpers.security import same_secret

    return same_secret(given, session.get(SESSION_LOGIN_FORM_TOKEN))


def _password_stamp(user: dict[str, Any] | None) -> str:
    """Changes whenever the stored password does, and says nothing about it.

    The session is a signed cookie with no server state, so there is no list of
    sessions to end. Binding each one to the hash it was issued under ends them
    all at a password change or reset, and to `session_epoch` ends them all at a
    sign-out (`users.end_sessions`). At epoch 0 the stamp is what it was before
    the epoch existed, so adding it signed nobody out.
    """
    stored = str((user or {}).get("password_hash") or "")
    epoch = int((user or {}).get("session_epoch") or 0)
    if epoch:
        stored += f":{epoch}"
    return hashlib.sha256(stored.encode("utf-8")).hexdigest()[:16]


def is_login_required() -> bool:
    """Authentication is always required. Kept as a function because callers
    (e.g. api/csrf_token.py) branch on it."""
    return True


def login_session(user: dict[str, Any]) -> None:
    session.permanent = True
    # Spent: the next sign-in form gets a new one.
    session.pop(SESSION_LOGIN_FORM_TOKEN, None)
    session[SESSION_USER_ID] = user["id"]
    session[SESSION_USERNAME] = user["username"]
    session[SESSION_ROLE] = rbac.normalize_role(user.get("role"))
    session[SESSION_LAST_SEEN] = int(time.time())
    # Re-read: sign-in hands over the public record, which has no hash.
    session[SESSION_PASSWORD_STAMP] = _password_stamp(users.get_user_by_id(user["id"]))


def logout_session() -> None:
    for key in (
        SESSION_USER_ID,
        SESSION_USERNAME,
        SESSION_ROLE,
        SESSION_PASSWORD_STAMP,
        SESSION_LAST_SEEN,
        "authentication",
    ):
        session.pop(key, None)


def current_user() -> dict[str, Any] | None:
    """Resolve the signed-in user from the session, re-reading the store.

    Re-reading matters: it means disabling or deleting an account takes effect
    on that account's very next request instead of when its cookie expires, and
    so does a password change, which ends every session issued before it.
    """
    user_id = session.get(SESSION_USER_ID)
    if not user_id:
        return None
    user = users.get_user_by_id(user_id)
    if user is None or user.get("disabled"):
        return None
    # Issued under a password that has since been changed or reset.
    if session.get(SESSION_PASSWORD_STAMP) != _password_stamp(user):
        return None
    # Unused for longer than the idle limit. A session from before the limit
    # existed has no time and is treated the same: one sign-in, once.
    now = int(time.time())
    seen = session.get(SESSION_LAST_SEEN)
    if not isinstance(seen, int) or now - seen > IDLE_HOURS * 3600:
        return None
    if now - seen > _SEEN_RESOLUTION_SECONDS:
        session[SESSION_LAST_SEEN] = now
    return user


def is_authenticated() -> bool:
    return current_user() is not None


def current_username() -> str | None:
    user = current_user()
    return user["username"] if user else None


def current_role() -> str | None:
    user = current_user()
    return rbac.normalize_role(user.get("role")) if user else None


def current_permissions() -> set[str]:
    return rbac.get_permissions(current_role()) if is_authenticated() else set()


def must_change_password() -> bool:
    """True when the signed-in user has to replace their password before the
    account can be used for anything else."""
    user = current_user()
    return bool(user and user.get("must_change_password"))


def has_permission(permission: str) -> bool:
    user = current_user()
    return bool(user) and rbac.has_permission(user.get("role"), permission)
