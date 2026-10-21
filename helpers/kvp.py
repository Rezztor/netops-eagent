import fnmatch
import json
import os
import threading
from typing import Any

_runtime_lock = threading.RLock()
_runtime_store: dict[str, Any] = {}


def _validate_key(key: str) -> None:
    if not key:
        raise ValueError("key must not be empty")
    if "\x00" in key:
        raise ValueError("key contains NUL")
    if "/" in key or os.path.sep in key or (os.path.altsep and os.path.altsep in key):
        raise ValueError("key must not contain path separators")


def get_runtime(key: str, default: Any = None) -> Any:
    with _runtime_lock:
        return _runtime_store.get(key, default)


def set_runtime(key: str, value: Any) -> None:
    _validate_key(key)
    with _runtime_lock:
        _runtime_store[key] = value


def remove_runtime(key: str) -> None:
    with _runtime_lock:
        _runtime_store.pop(key, None)


def find_runtime(pattern: str) -> list[str]:
    if not pattern:
        return []
    with _runtime_lock:
        return sorted([k for k in _runtime_store.keys() if fnmatch.fnmatch(k, pattern)])


def _connection():
    from helpers import db

    return db.transaction(None)


def get_persistent(key: str, default: Any = None) -> Any:
    """An instance-wide value: one per key, the same for everyone.

    For anything belonging to one account use `get_user_persistent`. These two
    used to be the same function, which is how every account's pinned chats came
    to live in a single row that any of them could read or delete.
    """
    _validate_key(key)
    with _connection() as conn:
        row = conn.execute(
            "SELECT value FROM kvp WHERE key = %s AND scope = 'instance'", (key,)
        ).fetchone()
    return default if row is None else row[0]


def set_persistent(key: str, value: Any) -> None:
    from psycopg.types.json import Jsonb

    _validate_key(key)
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO kvp (key, value, scope, owner_id, updated_at)
            VALUES (%s, %s, 'instance', NULL, now())
            ON CONFLICT (key, owner_id)
            DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (key, Jsonb(value)),
        )


def remove_persistent(key: str) -> None:
    _validate_key(key)
    with _connection() as conn:
        conn.execute(
            "DELETE FROM kvp WHERE key = %s AND scope = 'instance'", (key,)
        )


def get_user_persistent(key: str, default: Any = None, user_id: str | None = None) -> Any:
    """A value belonging to one account, and readable only by that account.

    The owner is resolved from the ambient scope unless one is passed, and the
    row-level policy on `kvp` is what enforces it -- not the WHERE clause here.
    With no owner to resolve there is nothing to read, which is the same answer
    the policy gives.
    """
    _validate_key(key)
    owner = user_id or _current_owner()
    if not owner:
        return default
    with _connection() as conn:
        row = conn.execute(
            "SELECT value FROM kvp WHERE key = %s AND scope = 'user' AND owner_id = %s",
            (key, owner),
        ).fetchone()
    return default if row is None else row[0]


def set_user_persistent(key: str, value: Any, user_id: str | None = None) -> None:
    from psycopg.types.json import Jsonb

    _validate_key(key)
    owner = user_id or _current_owner()
    if not owner:
        # Refusing rather than writing an unowned row: the CHECK would reject it
        # anyway, and a row nobody owns is the shape this migration removed.
        raise ValueError(f"{key!r} is per-user and there is no user in scope")
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO kvp (key, value, scope, owner_id, updated_at)
            VALUES (%s, %s, 'user', %s, now())
            ON CONFLICT (key, owner_id)
            DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (key, Jsonb(value), owner),
        )


def remove_user_persistent(key: str, user_id: str | None = None) -> None:
    _validate_key(key)
    owner = user_id or _current_owner()
    if not owner:
        return
    with _connection() as conn:
        conn.execute(
            "DELETE FROM kvp WHERE key = %s AND scope = 'user' AND owner_id = %s",
            (key, owner),
        )


def _current_owner() -> str | None:
    from helpers import user_scope

    return user_scope.get_current_user_id()


def find_persistent(pattern: str) -> list[str]:
    """Keys matching a shell glob.

    Translated to LIKE rather than fetching every key and matching in Python:
    the file version globbed a directory, which read every filename anyway, and
    this does the same work where the data is. `*` and `?` are the only
    wildcards the callers use; LIKE's own wildcards are escaped so a key
    containing a percent sign cannot widen the search.
    """
    if not pattern:
        return []

    _validate_key(pattern.replace("*", "x").replace("?", "x"))
    like = (
        pattern.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("*", "%")
        .replace("?", "_")
    )
    with _connection() as conn:
        rows = conn.execute(
            "SELECT key FROM kvp WHERE key LIKE %s ESCAPE '\\' "
            "AND scope = 'instance' ORDER BY key",
            (like,),
        ).fetchall()
    return [row[0] for row in rows]
