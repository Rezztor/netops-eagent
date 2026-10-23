"""A backup archive is untrusted input, and it is everyone's data.

Untrusted, because restore used to write each entry wherever its name pointed.
`eagent/usr/../api/pwn.py` passes the default `usr/**` pattern and landed in
api/, which the server imports on first request: code execution in the web pod
for whoever got an administrator to restore their file. The patterns cannot be
the guard -- the interface copies them out of the archive's own metadata -- so
these restore with no patterns as well as the default one, and expect nothing
outside usr/ to be written.

Everyone's data, because the archive holds every user's chats and a dump of the
database: the copy the server builds must not outlive its download.

The archives claim to come from a directory under tmp_path, so even a restore
that trusted every name would write into the test's own directory rather than
/etc or the checkout. None of this needs a database.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import zipfile

import pytest
from flask import Flask, request
from werkzeug.datastructures import FileStorage

from helpers.backup import BackupService


def _upload(path) -> FileStorage:
    """The object the endpoint hands restore: a fresh one per call, because
    saving it consumes the stream."""
    return FileStorage(io.BytesIO(path.read_bytes()), filename=path.name)


def _symlink(name: str) -> zipfile.ZipInfo:
    entry = zipfile.ZipInfo(name)
    entry.create_system = 3  # Unix, so the high bits are read as a file mode
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    return entry


def _write_archive(path, metadata, entries) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps(metadata))
        for name, payload in entries:
            archive.writestr(name, payload)


def _service(root) -> BackupService:
    service = BackupService()
    service.eagent_root = str(root)
    # clean_before_restore walks base_paths; left alone it walks the checkout.
    service.base_paths = {str(root): str(root)}
    return service


def _refused(result) -> set[str]:
    return {f["archive_path"] for f in result["skipped_files"] if f["reason"] == "refused"}


@pytest.fixture
def crafted(tmp_path):
    """An instance at <tmp>/eagent, and an archive taken from <tmp>/backups
    holding one legitimate file beside every way out of usr/."""
    root = tmp_path / "eagent"
    (root / "usr").mkdir(parents=True)
    (root / "api").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # A link already in usr/, pointing out of it. Only realpath sees through it.
    (root / "usr" / "linked").symlink_to(outside)

    tmp = str(tmp_path).strip("/")
    source = f"{tmp}/backups"
    hostile = {
        f"{source}/usr/../api/pwn.py": root / "api" / "pwn.py",
        # Stays inside usr/, and is still refused: `..` is never accepted.
        f"{source}/usr/sub/../inside.md": root / "usr" / "inside.md",
        f"{source}/usr-adjacent/x.md": root / "usr-adjacent" / "x.md",
        f"/{tmp}/outside/absolute.py": outside / "absolute.py",
        f"{tmp}/outside/foreign.py": outside / "foreign.py",
        # As long as "backups", so only the prefix check tells them apart.
        f"{tmp}/another/usr/stray.md": root / "usr" / "stray.md",
        f"{source}/usr/linked/through.py": outside / "through.py",
    }
    symlink = f"{source}/usr/symlink"
    archive = tmp_path / "crafted.zip"
    _write_archive(
        archive,
        {"environment_info": {"eagent_root": f"/{source}"}},
        [
            (f"{source}/usr/kept.md", "a real file"),
            *((name, "import os") for name in hostile),
            (_symlink(symlink), str(outside / "target")),
        ],
    )
    return root, archive, source, {*hostile, symlink}, list(hostile.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("default_pattern", [False, True], ids=["no-patterns", "default-pattern"])
async def test_restore_writes_nothing_outside_usr(crafted, default_pattern):
    root, archive, source, refused, targets = crafted
    patterns = [f"/{source}/usr/**"] if default_pattern else None

    result = await _service(root).restore_backup(_upload(archive), restore_include_patterns=patterns)

    assert [f["archive_path"] for f in result["restored_files"]] == [f"{source}/usr/kept.md"]
    assert (root / "usr" / "kept.md").read_text() == "a real file"
    assert _refused(result) == refused
    for target in targets:
        assert not target.exists(), target
    assert not os.path.lexists(root / "usr" / "symlink")


@pytest.mark.asyncio
async def test_the_preview_never_promises_what_restore_refuses(crafted):
    root, archive, source, refused, _ = crafted

    preview = await _service(root).preview_restore(_upload(archive))

    assert [f["archive_path"] for f in preview["files_to_restore"]] == [f"{source}/usr/kept.md"]
    assert _refused(preview) == refused
    assert not (root / "usr" / "kept.md").exists(), "a preview wrote a file"


@pytest.mark.asyncio
async def test_an_archive_that_does_not_say_where_it_came_from_restores_nothing(tmp_path):
    """The old answer was to write every name as an absolute path."""
    root = tmp_path / "eagent"
    (root / "usr").mkdir(parents=True)
    tmp = str(tmp_path).strip("/")
    names = [f"{tmp}/eagent/usr/notes.md", f"{tmp}/outside.py"]
    archive = tmp_path / "rootless.zip"
    _write_archive(archive, {"backup_name": "no environment_info"}, [(n, "x") for n in names])

    result = await _service(root).restore_backup(_upload(archive))

    assert result["restored_files"] == []
    assert _refused(result) == set(names)
    assert not (root / "usr" / "notes.md").exists()
    assert not (tmp_path / "outside.py").exists()


@pytest.mark.asyncio
async def test_removing_files_not_in_the_backup_never_reaches_outside_usr(tmp_path):
    """Its patterns come from the archive too, and can name the whole tree."""
    root = tmp_path / "eagent"
    (root / "usr").mkdir(parents=True)
    (root / "api").mkdir()
    (root / "api" / "keep.py").write_text("code the server imports")
    (root / "usr" / "stale.md").write_text("created after the backup")
    source = f"/{str(tmp_path).strip('/')}/backups"
    metadata = {
        "environment_info": {"eagent_root": source},
        "include_patterns": [f"{source}/**"],
        "exclude_patterns": [],
    }
    archive = tmp_path / "wide.zip"
    _write_archive(archive, metadata, [])
    service = _service(root)

    preview = await service.preview_restore(
        _upload(archive), clean_before_restore=True, user_edited_metadata=metadata
    )
    result = await service.restore_backup(
        _upload(archive), clean_before_restore=True, user_edited_metadata=metadata
    )

    stale = str(root / "usr" / "stale.md")
    assert [f["real_path"] for f in preview["files_to_delete"]] == [stale]
    assert [f["real_path"] for f in result["deleted_files"]] == [stale]
    assert (root / "api" / "keep.py").exists()


@pytest.mark.asyncio
async def test_a_restore_is_audited_with_what_it_refused(tmp_path, monkeypatch, capsys):
    from api import backup_restore
    from helpers import files
    from plugins._audit.helpers import audit

    # A bare root, not conftest's build_instance_root: that one links api/
    # back to the checkout, and a regression here would write into it.
    root = tmp_path / "eagent"
    (root / "usr").mkdir(parents=True)
    monkeypatch.setattr(files, "_base_dir", str(root))
    # It indexes every user's chats in the database; not what this is about.
    monkeypatch.setattr(backup_restore, "load_tmp_chats", lambda: None)

    name = f"{str(root).strip('/')}/usr/../api/pwn.py"
    upload = io.BytesIO()
    with zipfile.ZipFile(upload, "w") as archive:
        archive.writestr("metadata.json", json.dumps({"environment_info": {"eagent_root": str(root)}}))
        archive.writestr(name, "import os")
    body = upload.getvalue()

    app = Flask("test_backup_archive_safety")
    with app.test_request_context(
        "/api/backup_restore",
        method="POST",
        data={"backup_file": (io.BytesIO(body), "crafted.zip"), "metadata": "{}"},
        content_type="multipart/form-data",
    ):
        response = await backup_restore.BackupRestore(app, None).process({}, request)

    assert response["success"] is True
    assert [f["reason"] for f in response["skipped_files"]] == ["refused"]
    assert not (root / "api" / "pwn.py").exists()

    events = [
        json.loads(line[len(audit.AUDIT_PREFIX) + 1 :])
        for line in capsys.readouterr().out.splitlines()
        if line.startswith(audit.AUDIT_PREFIX)
    ]
    [event] = [e for e in events if e["event"] == "backup.restore"]
    assert event["outcome"] == "success"
    assert event["archive"] == "crafted.zip"
    assert event["archive_sha256"] == hashlib.sha256(body).hexdigest()
    assert event["refused"] == 1
    assert event["refused_entries"] == [name]


@pytest.mark.asyncio
async def test_the_archive_does_not_outlive_its_download(tmp_path, monkeypatch):
    from api import backup_create

    built = tmp_path / "mkdtemp"
    built.mkdir()
    zip_path = built / "nightly.zip"
    _write_archive(zip_path, {"backup_name": "nightly"}, [])

    # Building a real one walks usr/ and dumps the database; what is under test
    # is what the endpoint does with the directory create_backup hands back.
    async def create_backup(self, **kwargs):
        return str(zip_path)

    monkeypatch.setattr(BackupService, "create_backup", create_backup)
    monkeypatch.setattr(backup_create, "save_tmp_chats", lambda: None)

    app = Flask("test_backup_archive_safety")
    with app.test_request_context("/api/backup_create", method="POST", json={}):
        response = await backup_create.BackupCreate(app, None).process(
            {"backup_name": "nightly", "passphrase": "a passphrase of some length"}, request
        )
        assert not built.exists(), "the archive is still on disk once the response exists"
        body = b"".join(response.response)  # passthrough: the open, unlinked file
        response.close()

    assert zipfile.ZipFile(io.BytesIO(body)).namelist() == ["metadata.json"]


@pytest.mark.asyncio
async def test_a_failed_backup_leaves_no_directory_behind(tmp_path, monkeypatch):
    from helpers import backup

    built = tmp_path / "mkdtemp"

    def mkdtemp():
        built.mkdir()
        return str(built)

    def dump_data():
        raise RuntimeError("the database went away mid-backup")

    monkeypatch.setattr(backup.tempfile, "mkdtemp", mkdtemp)
    # No files matched; rows in the database are what make it go ahead.
    monkeypatch.setattr(backup.db, "data_tables", lambda: ["users"])
    monkeypatch.setattr(backup.db, "dump_data", dump_data)
    service = _service(tmp_path / "eagent")

    with pytest.raises(Exception, match="the database went away"):
        await service.create_backup(
            include_patterns=[f"{tmp_path}/eagent/usr/**"], exclude_patterns=[], passphrase="a passphrase of some length"
        )

    assert not built.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../escape", "/tmp/elsewhere/x", "a/b", ".."])
async def test_a_backup_name_is_a_file_name_and_never_a_path(tmp_path, monkeypatch, name):
    """The name was joined onto the temporary directory as it came, and the
    endpoint then deletes the directory the archive is in. `../x` put the
    archive beside the temporary directory and the delete took that parent --
    /tmp, with every runner key in it; an absolute name took whichever folder
    it named."""
    from helpers import backup

    built = tmp_path / "parent" / "mkdtemp"
    monkeypatch.setattr(backup.tempfile, "mkdtemp", lambda: (built.mkdir(parents=True), str(built))[1])
    monkeypatch.setattr(backup.db, "data_tables", lambda: ["users"])
    monkeypatch.setattr(backup.db, "dump_data", lambda: {"tables": {}})
    service = BackupService()

    async def nothing(*args, **kwargs):
        return []

    async def empty():
        return {}

    monkeypatch.setattr(service, "test_patterns", nothing)
    monkeypatch.setattr(service, "_get_system_info", empty)
    monkeypatch.setattr(service, "_get_environment_info", empty)
    monkeypatch.setattr(service, "_get_backup_author", lambda: _text("test"))

    zip_path = await service.create_backup(
        include_patterns=["x"], exclude_patterns=[], backup_name=name, passphrase="a passphrase of some length"
    )

    assert os.path.dirname(zip_path) == str(built), "the archive is outside the directory made for it"
    assert os.path.basename(zip_path).endswith(".rzbak") and "/" not in os.path.basename(zip_path)


async def _text(value):
    return value
