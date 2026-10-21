"""`cloud_state_version` as the `eagent_cloud` role: the write-ahead record, the once-only change id, and the grants.
Needs PostgreSQL (the root conftest's `database` fixture); the price copy's rewrite is checked without it."""
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from cloud_executor import db, store
from helpers import db as app_db

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def as_cloud(database, monkeypatch):
    """The executor's connection: the real `eagent_cloud` role the application's bootstrap creates."""
    from conftest import test_database_url

    monkeypatch.setenv(app_db.ENV_CLOUD_PASSWORD, "cloud-test-password")
    app_db._bootstrap_cloud_role()
    parts = urlsplit(test_database_url())
    url = urlunsplit(parts._replace(netloc=f"{db.ROLE}:cloud-test-password@{parts.hostname}:{parts.port}"))
    monkeypatch.setattr(db, "url", lambda: url)
    return url


def row(**over):
    return {"workspace": "sub/rg/resource_group/abc", "state_name": "w1", "change_id": "chg-1", "code": "code",
            "variables": {"region": "uaenorth"}, "plan_digest": "d" * 64, "reference": "CR-1234", **over}


def test_a_change_is_recorded_before_it_runs_and_only_once(as_cloud):
    assert store.applied_before("chg-1") is False
    store.record_apply(**row())
    assert store.applied_before("chg-1") is True
    with pytest.raises(store.AlreadyApplied):
        store.record_apply(**row())
    with db.transaction() as conn:
        saved = conn.execute("SELECT workspace, state, variables, reference FROM cloud_state_version").fetchone()
    assert saved == ("sub/rg/resource_group/abc", "", {"region": "uaenorth"}, "CR-1234")


def test_the_state_as_it_was_is_copied_untouched_into_the_record(as_cloud):
    with db.transaction() as conn:
        conn.execute("INSERT INTO states (name, data) VALUES ('w1', 'ciphertext-of-the-state')")
    store.record_apply(**row())
    with db.transaction() as conn:
        assert conn.execute("SELECT state FROM cloud_state_version").fetchone()[0] == "ciphertext-of-the-state"


def test_the_cloud_role_holds_exactly_its_grants(as_cloud):
    import psycopg

    with db.transaction() as conn:
        conn.execute("INSERT INTO states (name, data) VALUES ('w9', 'x')")  # OpenTofu's backend writes the state
        conn.execute("DELETE FROM states WHERE name = 'w9'")  # and deletes a workspace by name
        for statement in ("UPDATE cloud_state_version SET reference = 'x'", "DELETE FROM cloud_state_version",
                          "SELECT 1 FROM cloud_managed", "SELECT 1 FROM users", "CREATE TABLE t (a int)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SAVEPOINT s")
                try:
                    conn.execute(statement)
                finally:
                    conn.execute("ROLLBACK TO SAVEPOINT s")


def test_the_application_role_cannot_read_the_state_or_its_versions(database):
    import psycopg

    with app_db.transaction(app_db.NO_SCOPE) as conn:
        for table in ("states", "cloud_state_version"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SAVEPOINT s")
                try:
                    conn.execute(f"SELECT 1 FROM {table}")
                finally:
                    conn.execute("ROLLBACK TO SAVEPOINT s")


def test_the_price_copy_rewrites_one_import_line_and_nothing_else():
    source = (ROOT / "plugins" / "_ea_azure" / "helpers" / "prices.py").read_text(encoding="utf-8")
    assert len(re.findall(r"(?m)^from helpers import db$", source)) == 1, "the Dockerfile's sed would miss it"
    assert "helpers" not in re.sub(r"(?m)^from helpers import db$", "", source).replace("`helpers.db`", "")
    docker = (ROOT / "cloud_executor" / "Dockerfile").read_text(encoding="utf-8")
    assert "from helpers import db" in docker and "from cloud_executor import db" in docker


# ------------------------------------------------------------------------------------------- made_groups (ADR 0074)

SUB = "11111111-2222-3333-4444-555555555555"


def made(monkeypatch, tmp_path, *rows, ledger=()):
    monkeypatch.setenv("REZZTOR_CLOUD_STATE_DIR", str(tmp_path))
    for n, workspace in enumerate(rows):
        store.record_apply(**row(workspace=workspace, change_id=f"chg-{n}"))
    for n in ledger:
        store.commit_usd(f"chg-{n}", 0)


def test_a_group_is_made_when_a_maker_template_applied_into_it_and_the_ledger_has_the_change(as_cloud, monkeypatch, tmp_path):
    made(monkeypatch, tmp_path, f"{SUB}/rg-Hub/resource_group/abc", ledger=[0])
    assert store.made_groups(SUB, ["resource_group"]) == {"rg-hub"}
    assert store.made_groups(SUB.upper(), ["resource_group"]) == {"rg-hub"}


def test_a_row_with_no_ledger_entry_another_template_another_subscription_or_no_maker_does_not_count(as_cloud, monkeypatch, tmp_path):
    made(monkeypatch, tmp_path, f"{SUB}/rg-a/resource_group/1", f"{SUB}/rg-b/vnet/2", f"other/rg-c/resource_group/3",
         f"{SUB}/rg-d/resource_group/4", ledger=[1, 2])  # chg-0 and chg-3 never reached the ledger
    assert store.made_groups(SUB, ["resource_group"]) == set()
    assert store.made_groups(SUB, ["resource_group", "vnet"]) == {"rg-b"}
    assert store.made_groups(SUB, []) == set()


def test_the_workspace_is_split_on_slashes_so_an_underscore_is_not_a_wildcard(as_cloud, monkeypatch, tmp_path):
    made(monkeypatch, tmp_path, f"{SUB}/rg_a/resource_group/1", f"{SUB}/rgxa/resource_group/2", ledger=[0])
    assert store.made_groups(SUB, ["resource_group"]) == {"rg_a"}  # `rg_a` made, `rgxa` not: different groups


def test_a_database_that_cannot_be_read_raises_and_is_never_an_empty_set(monkeypatch, tmp_path):
    monkeypatch.setenv("REZZTOR_CLOUD_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(db, "transaction", lambda: (_ for _ in ()).throw(RuntimeError("no database")))
    with pytest.raises(RuntimeError):
        store.made_groups(SUB, ["resource_group"])


def test_a_ledger_that_cannot_be_read_raises_and_none_yet_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("REZZTOR_CLOUD_STATE_DIR", str(tmp_path))
    assert store.ledger_ids() == set()
    (tmp_path / "ledger.json").write_text("{not json")
    with pytest.raises(ValueError):
        store.ledger_ids()
