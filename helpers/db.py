"""The database connection, and the two roles that make isolation structural.

ADR 0003 isolated users by directory: ownership is derived from *where a file
lives*, so a path-handling mistake is contained by the filesystem rather than by
a filter being remembered at every call site. Moving to SQL gives that up unless
something replaces it, and ADR 0002 said what: row-level security rather than
discipline.

That is not belt and braces. ADR 0003 records a cross-user leak this product
already had — the FAISS cache was keyed by a subdirectory *name* that is `main`
for every user, so the first user to load it populated the cache for everyone.
The path was per-user; the key was not. A `WHERE owner_id = ...` that someone
forgets is the same bug with a different shape, and there will be more call sites
than there were cache keys.

So there are two roles:

**The owner role** creates tables and runs migrations. It is the one in
`REZZTOR_DB_URL`. The web process holds that URL for its whole life, and uses it
outside startup too, through `unscoped_transaction()`, for the operations that are
about the whole instance: backup and restore, deleting an account's rows, the
runner pool, the chat index, loading the scheduler, renaming a model preset. Each
such call is a place where row-level security does not apply; keep the list short.

**The application role** serves every other query and is created by the bootstrap
below. It cannot bypass row-level security: the role is not a superuser and does
not have `BYPASSRLS`, and the per-user tables are declared `FORCE ROW LEVEL
SECURITY`, which subjects the table owner too. Those tables are `conversations`,
`memories`, `kvp` and `change_approval`; on them a query that reaches the database
without a user scope set sees nothing at all, because the policy compares against
a setting that is then NULL, and `owner_id = NULL` is never true.

Every other table has no policy. Most hold nothing that is one person's (the
inventory, the cloud tables, settings), but `scheduled_tasks`, `password_resets`,
`runner_slots`, `inventory_proposal` and `inventory_source_run` carry an owner or
a user and are kept apart by the `WHERE` of the code that reads them, which is
the discipline the first paragraph warns about. A query on one of those must
name its owner.

Failing closed is the point. The filesystem version of this mistake returned
another user's data; this version returns none.

**The cloud role** is a third login role, `eagent_cloud`, held only by the cloud
executor (ADR 0061, 0064). It is created when `REZZTOR_DB_CLOUD_PASSWORD` is set
and given exactly the grants in `_CLOUD_GRANTS`: OpenTofu's state table, the
write-ahead record of every apply, and the price cache. The application role has
the opposite arrangement -- migration 0021 takes `states` and
`cloud_state_version` away from it -- so the application can never read the
state it asks the executor to change.
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Iterator

ENV_URL = "REZZTOR_DB_URL"
ENV_APP_PASSWORD = "REZZTOR_DB_APP_PASSWORD"
ENV_CLOUD_PASSWORD = "REZZTOR_DB_CLOUD_PASSWORD"

#: The role the application serves requests as. Not the role that owns the
#: tables, and deliberately not configurable: a deployment that pointed this at
#: the owner would silently lose row-level security, and nothing would look
#: wrong.
APP_ROLE = "eagent_app"

#: The role the cloud executor connects as. Like `APP_ROLE`, not configurable.
CLOUD_ROLE = "eagent_cloud"

#: Everything `CLOUD_ROLE` may touch, and nothing else. The state may
#: be deleted from because OpenTofu's backend deletes a workspace by name; the
#: product itself never asks it to. The versions are append-only.
_CLOUD_GRANTS = (
    "GRANT SELECT, INSERT, UPDATE, DELETE ON states TO {role}",
    "GRANT USAGE ON SEQUENCE states_id_seq TO {role}",
    "GRANT SELECT, INSERT ON cloud_state_version TO {role}",
    "GRANT SELECT, INSERT, UPDATE ON cloud_price TO {role}",
)

#: The setting a policy compares against. `SET LOCAL` scopes it to the
#: transaction, so it cannot leak onto the next request that borrows the same
#: pooled connection.
SCOPE_SETTING = "app.user_id"

# `transaction(None)` means "resolve the ambient scope"; this means "there is no
# scope and none should be looked for". They had the same spelling, and the
# account lookup -- which cannot know who the user is, because it is how we find
# out -- used the one that goes looking.
NO_SCOPE = ""

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")

#: A migration is `NNNN_some_name.sql`. The number orders them and is what is
#: recorded as applied; the name is for people.
MIGRATION_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class DatabaseError(Exception):
    """Refused, or unreachable. The message is written to be shown to an operator."""


class NotConfigured(DatabaseError):
    """No database URL. Storage is required, so this is fatal rather than a fallback."""


_pool: Any = None
_pool_lock = threading.Lock()


def owner_url() -> str:
    url = (os.getenv(ENV_URL) or "").strip()
    if not url:
        raise NotConfigured(
            f"{ENV_URL} is not set. This build stores everything in PostgreSQL and "
            "has no file fallback, so there is nothing to fall back to."
        )
    return url


def app_url() -> str:
    """The owner URL with the user and password swapped for the app role."""
    password = (os.getenv(ENV_APP_PASSWORD) or "").strip()
    if not password:
        raise NotConfigured(
            f"{ENV_APP_PASSWORD} is not set, so the application has no role to "
            "connect as. It must not fall back to the owner role: that role is "
            "not subject to row-level security."
        )
    from urllib.parse import quote, urlsplit, urlunsplit

    parts = urlsplit(owner_url())
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    # Quoted: `@`, `/`, `#` and `?` in a password otherwise end the userinfo
    # early, and the rest of the password becomes the host.
    netloc = f"{APP_ROLE}:{quote(password, safe='')}@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


# --- bootstrap ---------------------------------------------------------------

def bootstrap() -> list[str]:
    """Create the application role and bring the schema up to date.

    Returns the migrations it applied, so startup can say which -- an operator
    watching a slow first boot should be able to tell schema work from a hang.

    Runs as the owner, at startup, before anything serves a request. Idempotent:
    a role that exists has its password reset to what the environment says, so
    rotating the secret is a restart rather than a procedure.
    """
    import psycopg

    password = (os.getenv(ENV_APP_PASSWORD) or "").strip()
    if not password:
        raise NotConfigured(f"{ENV_APP_PASSWORD} is not set.")

    with psycopg.connect(owner_url(), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)
        ).fetchone()
        # The password is a literal here because PostgreSQL will not take a
        # parameter in CREATE ROLE. It is quoted by the driver rather than
        # formatted in, which is the part that matters.
        from psycopg import sql

        if exists:
            conn.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                    sql.Identifier(APP_ROLE), sql.Literal(password)
                )
            )
        else:
            conn.execute(
                sql.SQL("CREATE ROLE {} WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                    sql.Identifier(APP_ROLE), sql.Literal(password)
                )
            )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(conn.info.dbname), sql.Identifier(APP_ROLE)
            )
        )
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(APP_ROLE))
        )

    # Any pool built before this point was built before the role existed, so its
    # connections failed to authenticate and it is cached in that state. Thrown
    # away rather than reused: a first boot would otherwise serve every request
    # through a pool that cannot connect.
    reset_for_tests()

    applied = migrate()
    _bootstrap_cloud_role()
    return applied


def _bootstrap_cloud_role() -> None:
    """Create or refresh the cloud role and give it its grants, once the tables exist.

    A deployment without the cloud executor sets no password and gets no role. Runs
    after `migrate()` because the grants name tables that migration 0021 creates.
    """
    password = (os.getenv(ENV_CLOUD_PASSWORD) or "").strip()
    if not password:
        return
    import psycopg
    from psycopg import sql

    with psycopg.connect(owner_url(), autocommit=True) as conn:
        role = sql.Identifier(CLOUD_ROLE)
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (CLOUD_ROLE,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL(verb + " ROLE {} WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                role, sql.Literal(password)
            )
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(conn.info.dbname), role)
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        for grant in _CLOUD_GRANTS:
            conn.execute(sql.SQL(grant).format(role=role))


# --- migrations --------------------------------------------------------------

def migrations() -> list[tuple[int, str, str]]:
    """(number, name, path), in order. Refuses a duplicate number.

    Two migrations sharing a number is not a conflict a resolver should guess at:
    whichever ran first would be recorded and the other silently skipped forever.
    """
    found: dict[int, tuple[int, str, str]] = {}
    for entry in sorted(os.listdir(SCHEMA_DIR)) if os.path.isdir(SCHEMA_DIR) else []:
        match = MIGRATION_PATTERN.match(entry)
        if not match:
            continue
        number = int(match.group(1))
        if number in found:
            raise DatabaseError(
                f"Two migrations are numbered {number:04d}: "
                f"{found[number][1]} and {match.group(2)}."
            )
        found[number] = (number, match.group(2), os.path.join(SCHEMA_DIR, entry))
    return [found[key] for key in sorted(found)]


def _refuse_a_schema_this_build_does_not_know(
    applied: set[int], available: list[tuple[int, str, str]]
) -> None:
    """Refuse to start against a schema newer than this image.

    Migrations only move forward, and `migrate()` applies what is missing --
    which is exactly right going up and says nothing going down. Roll
    REZZTOR_VERSION back after an upgrade and the older application starts
    cleanly against the newer schema, then misbehaves at runtime against columns
    and constraints it does not know about.

    ADR 0037 named this hazard -- "a helm rollback after a failed start left the
    old application on the new schema" -- and the appliance engine that answered
    it, by restoring a dump before rolling back, was retired with k3s on
    2026-09-18. On Compose nothing was left to notice, so this does.

    It is a refusal rather than a warning for the same reason `prepare_database`
    raises: an instance that came up in this state reads as "the application is
    broken" and sends whoever is debugging it somewhere else entirely.
    """
    if not applied:
        return
    known = {number for number, _, _ in available}
    unknown = sorted(applied - known)
    if not unknown:
        return
    newest_known = max(known) if known else 0
    raise DatabaseError(
        f"This database is at schema {max(unknown):04d} and this build only ships "
        f"migrations up to {newest_known:04d} (unrecognised: "
        f"{', '.join(f'{n:04d}' for n in unknown)}). It was migrated by a newer "
        "release. Migrations only move forward, so starting here would run an "
        "older application against a schema it does not know. Deploy the newer "
        "release again, or restore the backup taken before the upgrade."
    )


def migrate() -> list[str]:
    """Apply what has not been applied. Returns the names of what ran."""
    import psycopg

    applied: list[str] = []
    with psycopg.connect(owner_url()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                number      INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.commit()

        done = {
            row[0] for row in conn.execute("SELECT number FROM schema_migrations").fetchall()
        }

        available = migrations()
        _refuse_a_schema_this_build_does_not_know(done, available)

        for number, name, path in available:
            if number in done:
                continue
            with open(path, encoding="utf-8") as handle:
                statements = handle.read()
            # One transaction per migration, so a failure leaves the schema at a
            # version that exists rather than half-way between two.
            with conn.transaction():
                conn.execute(statements)  # type: ignore[arg-type]
                conn.execute(
                    "INSERT INTO schema_migrations (number, name) VALUES (%s, %s)",
                    (number, name),
                )
            applied.append(f"{number:04d}_{name}")
    return applied


# --- serving requests --------------------------------------------------------

def pool() -> Any:
    global _pool
    with _pool_lock:
        if _pool is None:
            from psycopg_pool import ConnectionPool

            _pool = ConnectionPool(
                app_url(),
                min_size=1,
                max_size=int(os.getenv("REZZTOR_DB_POOL_MAX") or 10),
                open=True,
            )
        return _pool


# Resolving the ambient scope reads the users table, and reading any table opens
# a transaction -- which, with no scope passed, asks for the ambient scope again.
# That cycle ran a thousand frames deep on every request until
# `get_current_user_id()`'s `except Exception` swallowed the RecursionError, so
# a running instance did not crash; it just stopped answering.
#
# The guard is per-thread because that is the granularity a request runs at. A
# transaction opened *while* the scope is being resolved gets no scope, which is
# correct: the account lookup is how we find out who the user is, so it cannot
# already know.
_resolving = threading.local()


def _resolve_ambient_scope() -> str | None:
    """The owner of the current request, via Flask. Replaced in tests."""
    from helpers import user_scope

    return user_scope.get_current_user_id()


def _current_scope() -> str | None:
    if getattr(_resolving, "active", False):
        return None
    _resolving.active = True
    try:
        return _resolve_ambient_scope()
    finally:
        _resolving.active = False


@contextmanager
def transaction(user_id: str | None = None) -> Iterator[Any]:
    """A transaction scoped to one user, as the application role.

    `user_id` defaults to the active scope. Passing it explicitly is for the few
    places that act on behalf of a user who is not the caller.

    The scope is set with `SET LOCAL`, so it ends with the transaction and cannot
    leak onto whatever borrows this pooled connection next. A transaction opened
    without a scope is not an error and not a wildcard: the policy compares
    against a NULL and no row matches.
    """
    if user_id is None:
        user_id = _current_scope()

    # No explicit `conn.transaction()` here. `ConnectionPool.connection()`
    # already wraps the block in one and commits on a clean exit -- and when it
    # returns the connection to the pool it calls rollback() to reset it, which
    # psycopg forbids while an explicit Transaction context is open. The result
    # was every pooled request poisoning its own connection on the way back,
    # with "Explicit rollback() forbidden within a Transaction context" and, once
    # a connection was reused, an assertion inside psycopg's savepoint
    # bookkeeping.
    #
    # It passed every test, because a test opens a pool, uses it, and throws it
    # away. It took running the real thing to see it.
    #
    # `SET LOCAL` is still scoped correctly: the pool's transaction is the one it
    # is local to.
    with pool().connection() as conn:
        conn.execute("SELECT set_config(%s, %s, true)", (SCOPE_SETTING, user_id or ""))
        yield conn


@contextmanager
def unscoped_transaction() -> Iterator[Any]:
    """A transaction as the *owner*, seeing every row regardless of owner.

    For the handful of things that are legitimately instance-wide: creating the
    first administrator, counting seats for the licence, framework maintenance.

    Named so it cannot be reached for absent-mindedly. Every use is a place where
    the isolation boundary is deliberately not applied, and should read that way
    in a diff.
    """
    import psycopg

    with psycopg.connect(owner_url()) as conn:
        with conn.transaction():
            yield conn


# --- backup ------------------------------------------------------------------

#: Not backed up. It records which migrations ran, and a restore applies
#: migrations first -- carrying the old list across would tell a newer schema it
#: was already up to date.
UNDUMPED_TABLES = frozenset({
    "schema_migrations",
    # Seeded by its own migration and describing this runner image, not this
    # instance's data. Dumping it would carry one host's slot assignments into
    # another's restore, where the accounts behind them may not exist; and
    # because a restore truncates first, including it empties the pool and every
    # claim then reads as "the pool is full".
    "runner_slots",
})


def data_tables() -> list[str]:
    with unscoped_transaction() as conn:
        return sorted(
            name
            for (name,) in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
            if name not in UNDUMPED_TABLES
        )


def dump_data() -> dict[str, Any]:
    """Every row of every table, as plain JSON-able values.

    Deliberately not `pg_dump`. That would put postgresql-client in the image and
    couple its version to the server's -- pg_dump refuses to dump a server newer
    than itself, so a Postgres upgrade would silently break backups until someone
    also bumped the client.

    It also dumps the wrong thing. Schema comes from migrations, which is what
    lets a backup taken on an older version restore onto a newer one: the
    migrations run first and the data lands in the schema they built. A pg_dump
    carries a schema of its own, and restoring it would put the database back to
    the shape it had when the backup was taken.

    Read as the owner, because a backup is instance-wide by definition and a
    scoped connection would silently produce one user's data.
    """
    payload: dict[str, Any] = {"tables": {}}
    with unscoped_transaction() as conn:
        for table in data_tables():
            cursor = conn.execute(f'SELECT * FROM "{table}"')
            columns = [column.name for column in cursor.description or ()]
            payload["tables"][table] = {
                "columns": columns,
                "rows": [[_jsonable(value) for value in row] for row in cursor.fetchall()],
            }
    return payload


def _jsonable(value: Any) -> Any:
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        import base64

        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    return value


def load_data(payload: dict[str, Any]) -> dict[str, int]:
    """Replace every table's contents with what the backup holds.

    One transaction: a restore that fails half way through would leave accounts
    from the backup beside conversations from before it, which is a state no
    version of this instance has ever been in.

    Tables the backup does not mention are left alone rather than emptied. A
    backup taken before a table existed should not delete it.
    """
    from psycopg.types.json import Jsonb

    tables = payload.get("tables") or {}
    restored: dict[str, int] = {}
    present = set(data_tables())

    with unscoped_transaction() as conn:
        # Deferred so the order tables are restored in does not have to match
        # whatever foreign keys exist between them.
        conn.execute("SET CONSTRAINTS ALL DEFERRED")
        for table in tables:
            if table in present:
                conn.execute(f'TRUNCATE "{table}" CASCADE')

        for table, content in tables.items():
            if table not in present:
                continue
            columns = content.get("columns") or []
            rows = content.get("rows") or []
            if not columns or not rows:
                restored[table] = 0
                continue
            types = _column_types(conn, table)
            # Only the columns this schema has. A backup from before a column
            # was dropped names one that is gone, and inserting it failed the
            # whole restore the docstring above promises. It also means every
            # name in the statement is one the catalogue returned: the file's
            # own were written between quotes as they came, on this connection.
            kept = [index for index, column in enumerate(columns) if column in types]
            if not kept:
                restored[table] = 0
                continue
            placeholders = ", ".join(["%s"] * len(kept))
            quoted = ", ".join(f'"{columns[index]}"' for index in kept)
            for row in rows:
                values = [
                    Jsonb(row[index]) if types[columns[index]] in ("json", "jsonb") else row[index]
                    for index in kept
                ]
                conn.execute(
                    f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', values
                )
            restored[table] = len(rows)
            if types.get("id") in ("integer", "bigint"):
                # Rows went in with their own ids, so a sequence behind them would hand
                # the next insert an id that is taken (OpenTofu's `states`, 0021).
                conn.execute(
                    f'SELECT setval(pg_get_serial_sequence(\'public."{table}"\', \'id\'), '
                    f'max(id)) FROM "{table}"'
                )
    return restored


def _column_types(conn: Any, table: str) -> dict[str, str]:
    return {
        name: kind
        for name, kind in conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
    }


def reset_for_tests() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:  # noqa: BLE001
                pass
        _pool = None
