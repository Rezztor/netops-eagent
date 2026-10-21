"""Migration 0021 and the cloud database role (ADR 0061, 0064).

The application must never read the state it asks the executor to change, and the executor must reach nothing
but its own tables. Both are asserted by connecting as each role and trying.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege
from psycopg.types.json import Jsonb

from helpers import db

CLOUD_PASSWORD = "cloudpassword"


def _as(role: str, password: str):
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(db.owner_url())
    netloc = f"{role}:{password}@{parts.hostname}:{parts.port}"
    return psycopg.connect(urlunsplit((parts.scheme, netloc, parts.path, "", "")), autocommit=True)


@pytest.fixture
def cloud(database, monkeypatch):
    monkeypatch.setenv(db.ENV_CLOUD_PASSWORD, CLOUD_PASSWORD)
    db.bootstrap()


def _refused(conn, statement: str) -> bool:
    try:
        conn.execute(statement)
    except InsufficientPrivilege:
        return True
    return False


def test_the_application_role_reads_neither_the_state_nor_its_versions(cloud):
    with _as(db.APP_ROLE, "apppassword") as conn:
        assert _refused(conn, "SELECT * FROM states")
        assert _refused(conn, "SELECT * FROM cloud_state_version")
        assert _refused(conn, "INSERT INTO states (name, data) VALUES ('x', 'y')")
        # what it does own: the list of what it made
        conn.execute("INSERT INTO cloud_managed (native_id, workspace, change_id) VALUES ('/a', 'w', 'c')")
        assert conn.execute("SELECT count(*) FROM cloud_managed").fetchone()[0] == 1


def test_the_cloud_role_has_exactly_its_grants(cloud):
    with _as(db.CLOUD_ROLE, CLOUD_PASSWORD) as conn:
        conn.execute("INSERT INTO states (name, data) VALUES ('s/rg', 'ciphertext')")  # needs the sequence
        conn.execute("UPDATE states SET data = 'newer' WHERE name = 's/rg'")
        assert conn.execute("SELECT data FROM states").fetchone()[0] == "newer"
        conn.execute("DELETE FROM states WHERE name = 's/rg'")

        conn.execute(
            "INSERT INTO cloud_state_version (workspace, change_id, state, code, variables, plan_digest, reference) "
            "VALUES (%s, 'c1', 's', 'code', %s, 'd', 'CR-1234')", ("s/rg", Jsonb({})),
        )
        assert conn.execute("SELECT count(*) FROM cloud_state_version").fetchone()[0] == 1
        assert _refused(conn, "UPDATE cloud_state_version SET reference = 'x'")
        assert _refused(conn, "DELETE FROM cloud_state_version")

        conn.execute("INSERT INTO cloud_price (key) VALUES ('k')")
        conn.execute("UPDATE cloud_price SET unit = 'u'")
        assert _refused(conn, "DELETE FROM cloud_price")

        for table in ("users", "cloud_managed", "cloud_resource", "cloud_account", "schema_migrations"):
            assert _refused(conn, f"SELECT 1 FROM {table}"), table
        role = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (db.CLOUD_ROLE,)
        ).fetchone()
        assert role == (False, False)


def test_a_change_applies_once(cloud):
    with _as(db.CLOUD_ROLE, CLOUD_PASSWORD) as conn:
        row = "INSERT INTO cloud_state_version (workspace, change_id, state, code, plan_digest, reference) VALUES ('w', 'same', 's', 'c', 'd', 'CR-1234')"
        conn.execute(row)
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(row)


def test_a_deployment_without_the_cloud_executor_leaves_the_role_alone(cloud, monkeypatch):
    monkeypatch.delenv(db.ENV_CLOUD_PASSWORD)
    db.bootstrap()  # no password: nothing created, nothing reset
    _as(db.CLOUD_ROLE, CLOUD_PASSWORD).close()


def test_a_restore_leaves_the_state_sequence_behind_the_restored_ids(database):
    payload = {"tables": {"states": {"columns": ["id", "name", "data"], "rows": [[7, "a/b", "x"], [9, "c/d", "y"]]}}}
    db.load_data(payload)
    with db.unscoped_transaction() as conn:
        new = conn.execute("INSERT INTO states (name, data) VALUES ('e/f', 'z') RETURNING id").fetchone()[0]
    assert new == 10


def test_the_backup_carries_the_state_as_ciphertext_the_app_role_cannot_read(database):
    with db.unscoped_transaction() as conn:
        conn.execute("INSERT INTO states (name, data) VALUES ('a/b', 'ciphertext')")
    dump = db.dump_data()
    assert dump["tables"]["states"]["rows"][0][1:] == ["a/b", "ciphertext"]
    assert "cloud_state_version" in dump["tables"] and "cloud_managed" in dump["tables"]
