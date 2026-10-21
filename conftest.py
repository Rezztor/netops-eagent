"""Shared fixtures, at the root rather than under tests/.

Plugins have their own test directories, and a fixture under `tests/` does not
reach them -- which showed up as `fixture 'database' not found` the first time a
plugin test needed one. The database is not a property of the `tests/` folder.

The one here is the database. Accounts, settings and the rest moved into
PostgreSQL, so the tests that cover them need one — and this build has no file
fallback, which means there is nothing for them to fall back to either.

They skip without a reachable database rather than failing, on the same terms as
the runner-sandbox tests, and the skip message is the command that fixes it. That
is a real reduction in what a bare `pytest` run proves, so it is said out loud in
the skip rather than left to be noticed.
"""

from __future__ import annotations

import os

import pytest

from helpers import db

DEV_URL = "postgresql://eagent:devpassword@127.0.0.1:55432/eagent"
DEV_APP_PASSWORD = "apppassword"

NEEDS_POSTGRES = (
    "needs PostgreSQL: docker run -d --name eagent-pg-dev "
    "-e POSTGRES_PASSWORD=devpassword -e POSTGRES_USER=eagent -e POSTGRES_DB=eagent "
    "-p 127.0.0.1:55432:5432 pgvector/pgvector:pg17"
)


def test_database_url() -> str:
    return os.getenv("REZZTOR_TEST_DB_URL") or DEV_URL


def postgres_reachable() -> bool:
    try:
        import psycopg

        with psycopg.connect(test_database_url(), connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def database(monkeypatch):
    """A migrated database with every table empty.

    Truncated rather than recreated: a fresh database per test would spend more
    time running migrations than running tests, and `TRUNCATE ... CASCADE` leaves
    the same state a new one would.

    One consequence, and it bites: **two pytest runs cannot share this database
    at once.** Each truncates every table out from under the other, and the
    result is a scatter of unrelated failures that look like a regression and
    are not. If you are verifying commits in a worktree, let that finish before
    running the suite again -- the two runs point at the same server.
    """
    if not postgres_reachable():
        pytest.skip(NEEDS_POSTGRES)

    monkeypatch.setenv(db.ENV_URL, test_database_url())
    monkeypatch.setenv(db.ENV_APP_PASSWORD, DEV_APP_PASSWORD)
    db.reset_for_tests()
    db.bootstrap()

    with db.unscoped_transaction() as conn:
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'schema_migrations'"
            ).fetchall()
        ]
        if tables:
            conn.execute(
                "TRUNCATE " + ", ".join(f'"{name}"' for name in tables) + " CASCADE"
            )
    try:
        yield
    finally:
        db.reset_for_tests()


#: What `helpers.license_client.state()` answers in every test that is not about the licence.
LICENSED_FOR_TESTS = {
    "state": "licensed", "reason": "ok", "message": None, "problem": "", "clock_behind": False, "now": 0,
    "end": None, "restricted_at": None, "days_left": None, "lease": None, "has_token": False, "pending": None,
    "last_attempt_at": 0, "last_success_at": 0, "last_result": "", "next_attempt_at": 0,
    "licence": {
        "lic": "REZ-TEST-SUITE", "ver": 1, "customer": "Test suite", "kind": "subscription", "edition": "test",
        "features": ["network_automation", "azure", "azure_change", "solarwinds", "siem_forward", "mail",
                     "auto_skills", "attachments"],
        "seats": 1000, "instances": 1, "offline": True, "exp": 4102444800, "grace_days": 0,
    },
}


@pytest.fixture(autouse=True)
def licensed_installation(monkeypatch):
    """Every test runs on a licensed installation unless it puts the real verdict back.

    An installation with no licence is Restricted and starts no agent run, and the real verdict reads the
    database. Without this, every test that starts a run would need a database and a licence it is not about.
    The licence tests (tests/test_licence_client.py) restore the real function and prove the rest.
    """
    from helpers import license_client

    monkeypatch.setattr(license_client, "state", lambda now=None: dict(LICENSED_FOR_TESTS))


@pytest.fixture(autouse=True)
def no_dev_licence_stamp(monkeypatch):
    """A test sees an unstamped checkout, even where dev/run.sh has stamped this working tree.

    dev/run.sh writes `build_info.json` and `licence_trust.json` beside this file so that a host run trusts the
    licence server's DEV root (ADR 0085). A test reading them would trust that root instead of the one it sets up.
    Only those two files at this folder are hidden; a test that stamps a folder of its own still reads its own.
    """
    from helpers import files, license_client

    root = os.path.dirname(os.path.realpath(__file__))
    stamped = {path for path in (os.path.join(root, "build_info.json"), os.path.join(root, "licence_trust.json"))
               if os.path.lexists(path)}
    if not stamped:
        yield
        return
    real = files.get_abs_path
    monkeypatch.setattr(files, "get_abs_path",
                        lambda *parts: (path + ".hidden-from-tests")
                        if os.path.realpath(path := real(*parts)) in stamped else path)
    license_client.uat_trust.cache_clear()
    license_client.lab_build.cache_clear()
    yield
    license_client.uat_trust.cache_clear()
    license_client.lab_build.cache_clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    """A test that pointed the product at another folder leaves no list of that folder's plugins behind.

    Some fifty test files set `files._base_dir` to a temporary tree. The path is put back when the test ends; the
    cached list of plugins and extensions found under it was not, so a later test of the same run could see a
    product with one plugin. Looked at before the fixtures are undone, cleared after.
    """
    from helpers import cache, files

    moved = os.path.realpath(files.get_base_dir()) != os.path.dirname(os.path.realpath(__file__))
    yield
    if moved:
        cache.clear("*(extensions)*")
        cache.clear("*(plugins)*")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say out loud when a green run proved less than it looks like.

    Without a database, 150-odd tests skip -- accounts, sign-in, password reset,
    licensing. pytest reports that as a small grey number next to a large green
    one, and "2040 passed" reads like everything passed. It did not.
    """
    if postgres_reachable():
        return
    skipped = len(terminalreporter.stats.get("skipped", []))
    if skipped < 20:
        return
    terminalreporter.write_sep("=", "no database: this run proved less than it looks", red=True)
    terminalreporter.write_line(
        f"{skipped} tests were skipped because PostgreSQL was not reachable. "
        "Accounts, sign-in, password reset and licensing were not exercised."
    )
    terminalreporter.write_line(NEEDS_POSTGRES)


from testsupport import build_instance_root  # noqa: E402,F401  (re-export; see testsupport.py)
