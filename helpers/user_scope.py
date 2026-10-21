"""Which user's data the current operation belongs to.

Rezztor EAgent stores per-user state under usr/users/<user_id>/. Resolving the
owner has to work in three situations:

1. An HTTP or WebSocket request  -> the signed-in account.
2. An agent run on a background thread -> the account that owns the context,
   pushed explicitly with user_scope().
3. Framework startup and maintenance -> no owner; callers must handle None.

The explicit scope wins when set, so a background task keeps operating as its
owner even if it somehow runs inside another request's thread. Request identity
is read live from the session rather than cached in the ContextVar, because a
stale ContextVar left behind on a pooled worker thread would hand one user's
data to the next request on that thread.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from helpers import files

USERS_ROOT = "usr/users"

# Layout of a user's private root. Declared here so that modules needing one of
# these paths do not have to import the module that happens to own the feature,
# which would drag heavy dependencies into light ones.
CHATS_DIR = "chats"
MEMORY_DIR = "memory"
SKILLS_DIR = "skills"
WORKDIR_DIR = "workdir"
UPLOADS_DIR = "uploads"
DOWNLOADS_DIR = "downloads"

_scoped_user_id: ContextVar[str | None] = ContextVar("scoped_user_id", default=None)

# User ids are uuid4().hex, but this is what keeps a crafted id from escaping the
# users root, so it is enforced rather than assumed.
_SAFE_USER_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class UserScopeError(Exception):
    """Raised when an operation needs an owner and none can be resolved."""


def is_safe_user_id(user_id: str | None) -> bool:
    return bool(user_id) and bool(_SAFE_USER_ID.match(user_id or ""))


@contextmanager
def user_scope(user_id: str | None) -> Iterator[None]:
    """Run a block as a specific user. Used for agent runs and scheduled tasks."""
    token = _scoped_user_id.set(user_id)
    try:
        yield
    finally:
        _scoped_user_id.reset(token)


def get_scoped_user_id() -> str | None:
    """The explicitly pushed owner, ignoring any request session."""
    return _scoped_user_id.get()


def get_current_user_id() -> str | None:
    """Owner of the current operation, or None outside both a scope and a request."""
    scoped = _scoped_user_id.get()
    if scoped:
        return scoped

    try:
        from flask import has_request_context

        if not has_request_context():
            return None
        from helpers import login

        user = login.current_user()
        return user["id"] if user else None
    except RecursionError:
        # Never swallowed. This function reads the users table, and reading it
        # opens a transaction that can ask for the scope again; `db` guards that
        # cycle, but if a new one appears it must surface as a crash. Caught
        # here, it presented as a server that had simply stopped answering.
        raise
    except Exception:
        # No Flask context, or the session could not be read. Neither is an
        # error here: it just means there is no request-scoped owner.
        return None


def require_current_user_id() -> str:
    user_id = get_current_user_id()
    if not is_safe_user_id(user_id):
        raise UserScopeError(
            "This operation needs a user context, and none is active."
        )
    return user_id  # type: ignore[return-value]


def get_user_root(user_id: str | None = None) -> str:
    """Absolute path to a user's private data root."""
    user_id = user_id or require_current_user_id()
    if not is_safe_user_id(user_id):
        raise UserScopeError(f"Refusing to build a path for user id {user_id!r}.")
    return files.get_abs_path(USERS_ROOT, user_id)


def get_user_path(*parts: str, user_id: str | None = None) -> str:
    """Absolute path inside a user's private data root."""
    return files.get_abs_path(get_user_root(user_id), *parts)


def list_user_ids() -> list[str]:
    """User ids that have a data directory on disk."""
    try:
        entries = files.list_files(USERS_ROOT, "*")
    except Exception:
        return []
    return [entry for entry in entries if is_safe_user_id(entry)]


def ensure_user_root(user_id: str | None = None) -> str:
    """Create a user's data root if it does not exist, and return it."""
    root = get_user_root(user_id)
    os.makedirs(root, exist_ok=True)
    return root


def ensure_user_dir(*parts: str, user_id: str | None = None) -> str:
    """Create a directory inside a user's root and return it.

    Note this uses os.makedirs rather than files.make_dirs: the latter creates
    os.path.dirname(path), which for a directory argument silently creates only
    its parent.
    """
    path = get_user_path(*parts, user_id=user_id)
    os.makedirs(path, exist_ok=True)
    return path
