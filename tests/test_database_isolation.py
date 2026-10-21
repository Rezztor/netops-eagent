"""Row-level security, written before the tables that will depend on it.

ADR 0003 isolates users by directory: ownership comes from *where a file lives*,
so a path-handling mistake is contained by the filesystem instead of by a filter
being remembered at every call site. SQL gives that up. ADR 0002 said what has to
replace it — row-level security rather than discipline — and these are the tests
that make that claim checkable.

They matter more than most tests here because of what ADR 0003 records under
"what this did not prevent": this product has already leaked one user's data to
another, when the FAISS cache was keyed by a subdirectory name that is `main` for
everybody. The path was per-user; the key was not. A forgotten `WHERE owner_id`
is that same bug with a different shape, and there will be more query sites than
there were cache keys.

The case to read first is `test_a_query_with_no_scope_sees_nothing`. Failing
closed is the whole design: the filesystem version of this mistake returned
somebody else's data, and this version returns none.

These need a running PostgreSQL. They skip without one rather than failing, on
the same terms as the runner-sandbox tests, and the skip message says how to get
one.
"""

from __future__ import annotations

import os

import pytest

from pathlib import Path

from helpers import db

REPO_ROOT = Path(__file__).resolve().parent.parent

DEV_URL = "postgresql://eagent:devpassword@127.0.0.1:55432/eagent"
DEV_APP_PASSWORD = "apppassword"

START_IT = (
    "needs PostgreSQL: docker run -d --name eagent-pg-dev "
    "-e POSTGRES_PASSWORD=devpassword -e POSTGRES_USER=eagent -e POSTGRES_DB=eagent "
    "-p 127.0.0.1:55432:5432 pgvector/pgvector:pg17"
)


def _reachable() -> bool:
    url = os.getenv("REZZTOR_TEST_DB_URL") or DEV_URL
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


needs_postgres = pytest.mark.skipif(not _reachable(), reason=START_IT)


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setenv(db.ENV_URL, os.getenv("REZZTOR_TEST_DB_URL") or DEV_URL)
    monkeypatch.setenv(db.ENV_APP_PASSWORD, DEV_APP_PASSWORD)
    db.reset_for_tests()
    db.bootstrap()

    # A table shaped exactly like every user-owned table will be. Built here
    # rather than in a migration so these tests describe the pattern rather than
    # any one table's use of it.
    with db.unscoped_transaction() as conn:
        conn.execute("DROP TABLE IF EXISTS rls_probe")
        conn.execute(
            """
            CREATE TABLE rls_probe (
                id       TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                body     TEXT
            );
            ALTER TABLE rls_probe ENABLE ROW LEVEL SECURITY;
            ALTER TABLE rls_probe FORCE ROW LEVEL SECURITY;
            CREATE POLICY rls_probe_owner ON rls_probe
                USING (owner_id = app_user()) WITH CHECK (owner_id = app_user());
            GRANT SELECT, INSERT, UPDATE, DELETE ON rls_probe TO eagent_app;
            """
        )
        conn.execute(
            "INSERT INTO rls_probe VALUES "
            "('a', 'asha', 'asha row'), ('b', 'bruno', 'bruno row')"
        )
    try:
        yield
    finally:
        with db.unscoped_transaction() as conn:
            conn.execute("DROP TABLE IF EXISTS rls_probe")
        db.reset_for_tests()


# --- the property everything else rests on -----------------------------------

@needs_postgres
def test_a_query_with_no_scope_sees_nothing(database):
    """Not everything. The policy compares owner_id against a setting that is
    NULL when nothing set it, and `owner_id = NULL` is never true.

    If this ever returns rows, every endpoint that forgot to scope has been
    returning the whole instance, and nothing else here would notice.
    """
    with db.transaction(None) as conn:
        assert conn.execute("SELECT count(*) FROM rls_probe").fetchone()[0] == 0


@needs_postgres
def test_an_empty_user_id_is_not_a_wildcard(database):
    with db.transaction("") as conn:
        assert conn.execute("SELECT count(*) FROM rls_probe").fetchone()[0] == 0


# --- ordinary scoping ---------------------------------------------------------

@needs_postgres
def test_a_scoped_query_sees_only_that_users_rows(database):
    with db.transaction("asha") as conn:
        rows = conn.execute("SELECT id, body FROM rls_probe").fetchall()
    assert rows == [("a", "asha row")]


@needs_postgres
def test_asking_for_another_users_rows_by_name_returns_nothing(database):
    """The interesting case is not a query that forgot to filter -- it is one
    that filters *for someone else*. The policy is an AND, not a default."""
    with db.transaction("asha") as conn:
        assert conn.execute(
            "SELECT id FROM rls_probe WHERE owner_id = 'bruno'"
        ).fetchall() == []


@needs_postgres
def test_the_scope_does_not_leak_onto_the_next_transaction(database):
    """Connections are pooled, so a scope set with SET rather than SET LOCAL
    would be inherited by whoever borrows the connection next -- which is the
    same shape as the cache-key leak ADR 0003 records."""
    with db.transaction("asha") as conn:
        conn.execute("SELECT 1 FROM rls_probe").fetchall()
    with db.transaction(None) as conn:
        assert conn.execute("SELECT count(*) FROM rls_probe").fetchone()[0] == 0


# --- writes -------------------------------------------------------------------

@needs_postgres
def test_a_user_cannot_write_a_row_owned_by_someone_else(database):
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction("asha") as conn:
            conn.execute("INSERT INTO rls_probe VALUES ('c', 'bruno', 'forged')")


@needs_postgres
def test_a_user_cannot_reassign_a_row_to_themselves(database):
    """UPDATE is checked twice -- by USING on the row as it is, and by WITH CHECK
    on the row as it would become. Only the second stops this."""
    with db.transaction("asha") as conn:
        changed = conn.execute(
            "UPDATE rls_probe SET owner_id = 'asha' WHERE id = 'b'"
        ).rowcount
    assert changed == 0


@needs_postgres
def test_a_user_cannot_delete_someone_elses_row(database):
    with db.transaction("asha") as conn:
        assert conn.execute("DELETE FROM rls_probe WHERE id = 'b'").rowcount == 0
    with db.unscoped_transaction() as conn:
        assert conn.execute("SELECT count(*) FROM rls_probe").fetchone()[0] == 2


# --- the role itself ----------------------------------------------------------

@needs_postgres
def test_the_application_role_cannot_bypass_the_policy(database):
    """FORCE ROW LEVEL SECURITY subjects the table owner too, but the role the
    application connects as must not be able to sidestep it another way."""
    with db.unscoped_transaction() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
            (db.APP_ROLE,),
        ).fetchone()
    assert row == (False, False)


@needs_postgres
def test_the_owner_role_sees_everything_and_is_named_so_it_is_obvious(database):
    """Instance-wide work is legitimate -- seat counting, the first
    administrator -- and it goes through a call that reads as an exception in a
    diff rather than through the same function as everything else."""
    with db.unscoped_transaction() as conn:
        assert conn.execute("SELECT count(*) FROM rls_probe").fetchone()[0] == 2


# --- configuration ------------------------------------------------------------

def test_without_a_url_it_refuses_rather_than_falling_back(monkeypatch):
    """There is no file fallback in this build. A missing URL is fatal, and the
    message says so rather than leaving an operator looking for the old store."""
    monkeypatch.delenv(db.ENV_URL, raising=False)
    with pytest.raises(db.NotConfigured, match="no file fallback"):
        db.owner_url()


def test_the_application_never_falls_back_to_the_owner_role(monkeypatch):
    """The owner is not subject to row-level security. Connecting as it because
    a password was missing would disable isolation and look like it worked."""
    monkeypatch.setenv(db.ENV_URL, DEV_URL)
    monkeypatch.delenv(db.ENV_APP_PASSWORD, raising=False)
    with pytest.raises(db.NotConfigured, match="row-level security"):
        db.app_url()


def test_the_application_url_swaps_the_role_and_keeps_the_target(monkeypatch):
    monkeypatch.setenv(db.ENV_URL, "postgresql://owner:secret@dbhost:5433/eagent")
    monkeypatch.setenv(db.ENV_APP_PASSWORD, "apppw")
    url = db.app_url()
    assert url.startswith(f"postgresql://{db.APP_ROLE}:apppw@dbhost:5433/eagent")
    assert "owner:secret" not in url


def test_two_migrations_cannot_share_a_number(tmp_path, monkeypatch):
    """Whichever ran first would be recorded and the other skipped forever."""
    (tmp_path / "0002_one.sql").write_text("SELECT 1;")
    (tmp_path / "0002_two.sql").write_text("SELECT 1;")
    monkeypatch.setattr(db, "SCHEMA_DIR", str(tmp_path))
    with pytest.raises(db.DatabaseError, match="numbered 0002"):
        db.migrations()


def test_migrations_are_ordered_by_number_not_by_name(tmp_path, monkeypatch):
    for name in ("0010_zebra.sql", "0002_apple.sql", "0001_foundations.sql"):
        (tmp_path / name).write_text("SELECT 1;")
    monkeypatch.setattr(db, "SCHEMA_DIR", str(tmp_path))
    assert [number for number, _, _ in db.migrations()] == [1, 2, 10]


def test_something_that_is_not_a_migration_is_ignored(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("notes")
    (tmp_path / "0001_ok.sql").write_text("SELECT 1;")
    (tmp_path / "draft.sql").write_text("SELECT 1;")
    monkeypatch.setattr(db, "SCHEMA_DIR", str(tmp_path))
    assert [name for _, name, _ in db.migrations()] == ["ok"]


# --- startup ------------------------------------------------------------------

def test_the_application_prepares_the_database_before_it_serves():
    """bootstrap() creates the application role and runs the migrations, and for
    a while nothing but the tests called it.

    An instance that came up without it would serve a sign-in page it cannot
    authenticate against, because every request would fail on a role that does
    not exist -- which reads as "the application is broken" rather than "the
    database is not ready", and sends whoever is debugging it somewhere else.
    """
    import run_ui

    source = (REPO_ROOT / "run_ui.py").read_text(encoding="utf-8")
    body = source[source.index("def run():"):source.index("def prepare_database")]
    assert "prepare_database()" in body

    # Before the account check, which reads the users table, and before anything
    # that serves.
    assert body.index("prepare_database()") < body.index("ensure_admin_account()")
    assert body.index("prepare_database()") < body.index("start_web_server")
    assert callable(run_ui.prepare_database)


def test_a_database_that_cannot_be_prepared_stops_startup():
    """It raises rather than warning. Coming up half-working is the outcome that
    wastes the most of somebody's evening."""
    import run_ui

    source = (REPO_ROOT / "run_ui.py").read_text(encoding="utf-8")
    body = source[source.index("def prepare_database"):source.index("def run_migration_checks")]
    assert body.count("raise") >= 2, "prepare_database must not swallow a failure"
    assert "return" not in body.split("raise")[0].split("try:")[-1]


@needs_postgres
def test_a_pooled_connection_survives_being_reused(database):
    """The bug this exists for passed every other test in this file.

    `ConnectionPool.connection()` already wraps its block in a transaction and
    calls rollback() to reset the connection when it goes back to the pool --
    which psycopg forbids while an explicit Transaction context is open. Opening
    one inside it poisoned every connection on the way back, and the failure only
    surfaced once a poisoned connection was handed out again.

    Every test here opened a pool, used it once and threw it away, so none of
    them ever reused a connection. It took running the real thing to see it, and
    this is the test that would have.
    """
    for _ in range(30):
        with db.transaction("asha") as conn:
            assert conn.execute("SELECT 1").fetchone() == (1,)


@needs_postgres
def test_the_scope_still_does_not_leak_across_reuses(database):
    """The reason the explicit transaction was there in the first place was to
    scope `SET LOCAL`. Removing it must not have removed that."""
    with db.transaction("asha") as conn:
        conn.execute("SELECT app_user()").fetchone()
    for _ in range(10):
        with db.transaction(None) as conn:
            assert conn.execute("SELECT app_user()").fetchone() == (None,)


@needs_postgres
def test_bootstrap_discards_a_pool_built_before_the_role_existed(database, monkeypatch):
    """On a first boot something may reach the database before the application
    role has been created -- and psycopg caches the pool it failed with. Reusing
    it would mean serving every request through a pool that cannot connect."""
    db.reset_for_tests()
    first = db.pool()
    db.bootstrap()
    assert db.pool() is not first


def test_importing_models_does_not_reach_the_database():
    """Module import happens long before `bootstrap()` has built a database.

    `models.py` used to call `configure_litellm()` at import, which reads
    settings, which now means a query. On a first boot that fired eleven times
    against a role that did not exist yet -- and lost, because
    `get_litellm_global_kwargs()` swallows the failure and falls back to
    defaults, so an operator's configured `litellm_global_kwargs` silently never
    reached litellm. Every call path calls `configure_litellm()` itself, so the
    import-time call bought nothing and cost that.

    No `@needs_postgres`: the point is that this passes with no database at all.
    """
    import subprocess
    import sys

    # `helpers.db.pool`, not `psycopg_pool.ConnectionPool`: with no
    # DATABASE_URL set the call raises NotConfigured before a pool is ever
    # built, so a probe on the pool constructor passes whether or not the read
    # happens. `pool()` is the layer the read actually crosses either way.
    probe = (
        "import sys\n"
        "from helpers import db\n"
        "def boom(*a, **k):\n"
        "    sys.exit('models reached the database at import time')\n"
        "db.pool = boom\n"
        "import models\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- resolving the scope must not need the scope -------------------------------

@needs_postgres
def test_resolving_the_ambient_scope_does_not_re_enter_the_scope_resolver(database, monkeypatch):
    """The cycle that wedged a running instance.

    `transaction(None)` means "use the ambient scope", and resolving that scope
    reads the users table -- which opens a transaction, which asks for the
    ambient scope again. Each request burned a thousand stack frames before
    `get_current_user_id()`'s `except Exception` swallowed the RecursionError
    and let it continue, so the symptom was not a crash but a server that
    stopped answering.

    It needed two conditions that no test had together: an empty scope
    ContextVar and a live Flask request context. In the worker thread the
    request actually runs on, that is the normal case.
    """
    depth = {"max": 0, "now": 0}
    real = db._current_scope

    def counting():
        depth["now"] += 1
        depth["max"] = max(depth["max"], depth["now"])
        try:
            return real()
        finally:
            depth["now"] -= 1

    monkeypatch.setattr(db, "_current_scope", counting)

    def resolver():
        # Stands in for login.current_user(): reading the users table needs a
        # transaction of its own, exactly as the real one does.
        with db.transaction() as conn:
            conn.execute("SELECT 1").fetchone()
        return "asha"

    monkeypatch.setattr(db, "_resolve_ambient_scope", resolver)

    with db.transaction() as conn:
        conn.execute("SELECT 1").fetchone()

    assert depth["max"] <= 2, (
        f"scope resolution recursed {depth['max']} deep; the guard is not holding"
    )


def test_a_password_with_url_characters_does_not_move_the_connection(monkeypatch):
    """The password is written into a URL, and `@`, `/`, `#` and `?` all mean
    something there. Unquoted, `p@ss/w#rd` made the host `ss`: the application
    would have dialled a machine named after part of its own password."""
    from urllib.parse import unquote, urlsplit

    monkeypatch.setenv(db.ENV_URL, "postgresql://owner:secret@dbhost:5433/eagent?sslmode=require")
    monkeypatch.setenv(db.ENV_APP_PASSWORD, "p@ss:w/rd#x?y")
    parts = urlsplit(db.app_url())
    assert (parts.hostname, parts.port, parts.path) == ("dbhost", 5433, "/eagent")
    assert parts.query == "sslmode=require"
    assert unquote(parts.password) == "p@ss:w/rd#x?y"


def test_an_ipv6_database_host_keeps_its_brackets(monkeypatch):
    monkeypatch.setenv(db.ENV_URL, "postgresql://owner:secret@[::1]:5433/eagent")
    monkeypatch.setenv(db.ENV_APP_PASSWORD, "apppw")
    from urllib.parse import urlsplit

    parts = urlsplit(db.app_url())
    assert (parts.hostname, parts.port) == ("::1", 5433)
