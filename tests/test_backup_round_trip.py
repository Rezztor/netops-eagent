"""A backup has to restore an instance, not a directory.

`usr/**` described this instance's whole state until accounts left it. A backup
that still only zipped the volume would restore conversations and no accounts --
which does not fail. It seeds a fresh administrator and looks like a working
install with everyone's sign-in gone, and the operator finds out at the sign-in
screen.

So the archive carries the database beside the files, and the test that matters
is the whole loop: back up, destroy, restore, and check the accounts are the ones
that were there before.
"""

from __future__ import annotations

import json
import os
import zipfile

import pytest

from helpers import db, users
from helpers.backup import BackupService


@pytest.fixture
def instance(database, tmp_path, monkeypatch):
    from helpers import files

    from testsupport import build_instance_root

    monkeypatch.setattr(files, "_base_dir", build_instance_root(tmp_path))
    return tmp_path


@pytest.fixture
def instance_with_a_file(instance):
    """Most tests here want the ordinary case: some files and a database."""
    (instance / "usr" / "knowledge").mkdir(parents=True, exist_ok=True)
    (instance / "usr" / "knowledge" / "note.md").write_text("a file that is a file")
    return instance


class Uploaded:
    """What restore_backup expects: a Werkzeug FileStorage, which has .save().

    The API hands it one straight from the request. A test holding a path has to
    look like one rather than the code having to accept both, because "accepts
    both" is how a restore path ends up with two behaviours.
    """

    def __init__(self, path: str) -> None:
        self._path = path

    def save(self, destination: str) -> None:
        import shutil

        shutil.copy(self._path, destination)


async def _archive(service: BackupService, instance) -> str:
    return await service.create_backup(
        include_patterns=[f"{instance}/usr/**"],
        exclude_patterns=[],
        backup_name="round-trip",
    )


@pytest.mark.asyncio
async def test_the_archive_contains_the_database(instance_with_a_file):
    instance = instance_with_a_file
    users.create_user("asha", "correcthorsebattery", "admin", email="asha@x.example")
    path = await _archive(BackupService(), instance)

    with zipfile.ZipFile(path) as archive:
        assert BackupService.DATABASE_ENTRY in archive.namelist()
        payload = json.loads(archive.read(BackupService.DATABASE_ENTRY))

    usernames = [
        row[payload["tables"]["users"]["columns"].index("username")]
        for row in payload["tables"]["users"]["rows"]
    ]
    assert "asha" in usernames


@pytest.mark.asyncio
async def test_an_account_survives_backup_destroy_restore(instance_with_a_file):
    instance = instance_with_a_file
    """The loop the whole feature exists for."""
    users.create_user("asha", "correcthorsebattery", "admin", email="asha@x.example")
    path = await _archive(BackupService(), instance)

    with db.unscoped_transaction() as conn:
        conn.execute("TRUNCATE users CASCADE")
    assert users.list_users() == []

    result = await BackupService().restore_backup(Uploaded(path))

    assert result["database_restored"].get("users") == 1
    assert [user["username"] for user in users.list_users()] == ["asha"]


@pytest.mark.asyncio
async def test_the_password_survives_too(instance_with_a_file):
    instance = instance_with_a_file
    """Restoring the row is not enough if the hash does not come with it -- the
    account would exist and nobody could sign in to it."""
    users.create_user("asha", "correcthorsebattery", "admin")
    path = await _archive(BackupService(), instance)

    with db.unscoped_transaction() as conn:
        conn.execute("TRUNCATE users CASCADE")
    await BackupService().restore_backup(Uploaded(path))

    assert users.authenticate("asha", "correcthorsebattery") is not None
    assert users.authenticate("asha", "wrong") is None


@pytest.mark.asyncio
async def test_a_backup_with_no_database_says_so_rather_than_half_restoring(instance_with_a_file):
    instance = instance_with_a_file
    """An archive from before the database existed. Restoring its files and
    saying nothing would leave an instance whose accounts are silently gone."""
    users.create_user("asha", "correcthorsebattery", "admin")
    path = await _archive(BackupService(), instance)

    # Rewrite the archive without the database entry.
    stripped = str(instance / "old-style.zip")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(stripped, "w") as target:
        for name in source.namelist():
            if name != BackupService.DATABASE_ENTRY:
                target.writestr(name, source.read(name))

    result = await BackupService().restore_backup(Uploaded(stripped))

    assert "predates them moving out of the volume" in result["database_error"]
    assert any(e["path"] == BackupService.DATABASE_ENTRY for e in result["errors"])


@pytest.mark.asyncio
async def test_the_database_entry_is_not_written_onto_the_volume(instance_with_a_file):
    instance = instance_with_a_file
    """It is not a file this instance has. Restoring it into usr/ would leave a
    stray document nothing reads and a copy of every account on the disk."""
    users.create_user("asha", "correcthorsebattery", "admin")
    path = await _archive(BackupService(), instance)

    result = await BackupService().restore_backup(Uploaded(path))

    assert not (instance / BackupService.DATABASE_ENTRY).exists()
    assert BackupService.DATABASE_ENTRY not in [
        f.get("path", "") for f in result["restored_files"]
    ]


@pytest.mark.asyncio
async def test_an_instance_with_no_files_can_still_be_backed_up(instance):
    """The state a fresh install is in, now that accounts and settings are not
    files. Refusing here would mean the instance most in need of a backup --
    one that has just had its administrator created -- cannot take one."""
    users.create_user("asha", "correcthorsebattery", "admin")
    path = await _archive(BackupService(), instance)

    with zipfile.ZipFile(path) as archive:
        assert BackupService.DATABASE_ENTRY in archive.namelist()


@pytest.mark.asyncio
async def test_a_file_survives_backup_destroy_restore(instance_with_a_file):
    """The files half of the loop. Restore now refuses anything that does not
    land inside usr/, and an ordinary archive must not be caught by that."""
    instance = instance_with_a_file
    note = instance / "usr" / "knowledge" / "note.md"
    path = await _archive(BackupService(), instance)
    note.unlink()

    result = await BackupService().restore_backup(Uploaded(path))

    assert note.read_text() == "a file that is a file"
    assert result["skipped_files"] == []


# --- a backup whose columns are not this schema's ------------------------------


def _accounts_dump():
    """A dump holding one account, taken from the database the fixture made."""
    from helpers import db, users

    users.create_user("root", "root-password", "admin")
    return db.dump_data()


def test_a_backup_from_before_a_column_was_dropped_still_restores(database):
    """`load_data` says a backup taken on an older version restores onto a newer
    one: the migrations run first and the data lands in the schema they built.
    It inserted every column the file named, so the first column a later
    migration had dropped (0017 drops two) failed the whole restore."""
    from helpers import db, users

    dump = _accounts_dump()
    table = dump["tables"]["users"]
    table["columns"].append("a_column_this_schema_dropped")
    table["rows"] = [row + ["old value"] for row in table["rows"]]

    restored = db.load_data(dump)

    assert restored["users"] == 1
    assert users.get_user("root")["role"] == "admin"


def test_a_column_name_in_a_backup_is_never_sql(database):
    """The names went into the statement between double quotes as they were, on
    the owner's connection, so a name holding a quote ended the identifier and
    the rest was read as SQL. The driver refuses a second statement beside
    parameters, so this one failed the restore instead of running; what one
    statement can say was still the file's to write."""
    from helpers import db, users

    dump = _accounts_dump()
    table = dump["tables"]["users"]
    table["columns"].append('x") VALUES (NULL); DROP TABLE kvp; --')
    table["rows"] = [row + [None] for row in table["rows"]]

    db.load_data(dump)

    with db.unscoped_transaction() as conn:
        assert conn.execute("SELECT to_regclass('public.kvp')").fetchone()[0] == "kvp"
    assert users.get_user("root") is not None
