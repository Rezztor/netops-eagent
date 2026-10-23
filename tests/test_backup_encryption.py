"""A backup is sealed with a passphrase the administrator types.

The archive holds the instance's keys, every password hash and every user's
files, so the file that leaves the server must be unreadable without the
passphrase, and any damage to it must be noticed before anything is written.
Nothing here needs a database.
"""

from __future__ import annotations

import io
import json
import logging
import os
import zipfile

import pytest
from flask import Flask, request
from werkzeug.datastructures import FileStorage

from helpers import backup, backup_crypto
from helpers.backup import BackupService

PASSPHRASE = "correct horse battery staple"


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(backup_crypto, "CHUNK", 64)


def _seal(tmp_path, plaintext: bytes, name="sealed", passphrase=PASSPHRASE) -> str:
    source = tmp_path / f"{name}.in"
    source.write_bytes(plaintext)
    sealed = str(tmp_path / name)
    backup_crypto.encrypt_file(str(source), sealed, passphrase)
    return sealed


def _open(tmp_path, sealed: str, passphrase=PASSPHRASE) -> bytes:
    out = str(tmp_path / "out")
    backup_crypto.decrypt_file(sealed, out, passphrase)
    with open(out, "rb") as handle:
        return handle.read()


def _refused(tmp_path, sealed: str, passphrase=PASSPHRASE) -> None:
    out = tmp_path / "refused"
    with pytest.raises(backup_crypto.BackupCryptoError, match="passphrase is wrong or the file is damaged"):
        backup_crypto.decrypt_file(sealed, str(out), passphrase)
    assert not out.exists(), "a refused backup left plaintext behind"


@pytest.mark.parametrize("size", [0, 1, 63, 64, 65, 128, 1000])
def test_it_round_trips_at_every_chunk_boundary(tmp_path, small_chunks, size):
    data = os.urandom(size)
    assert _open(tmp_path, _seal(tmp_path, data)) == data


def test_the_file_is_sealed_and_private(tmp_path):
    marker = b"AZURE_CLIENT_SECRET=do-not-leak"
    sealed = _seal(tmp_path, b"PK\x03\x04 metadata.json " + marker)
    raw = open(sealed, "rb").read()

    assert raw.startswith(backup_crypto.MAGIC)
    assert marker not in raw and b"metadata.json" not in raw
    assert backup_crypto.is_encrypted(sealed)
    assert os.stat(sealed).st_mode & 0o777 == 0o600
    out = tmp_path / "plain"
    backup_crypto.decrypt_file(sealed, str(out), PASSPHRASE)
    assert os.stat(out).st_mode & 0o777 == 0o600


def test_each_file_has_its_own_salt_and_nonces(tmp_path):
    first, second = _seal(tmp_path, b"same", "a"), _seal(tmp_path, b"same", "b")
    assert open(first, "rb").read() != open(second, "rb").read()


@pytest.mark.parametrize("passphrase", ["", "wrong passphrase entirely", PASSPHRASE + " "])
def test_a_wrong_passphrase_is_refused(tmp_path, passphrase):
    _refused(tmp_path, _seal(tmp_path, b"secret" * 100), passphrase)


@pytest.mark.parametrize("passphrase", [None, "", "short", "x" * 11, 12345])
def test_a_short_passphrase_cannot_seal_a_backup(tmp_path, passphrase):
    (tmp_path / "in").write_bytes(b"x")
    with pytest.raises(backup_crypto.BackupCryptoError, match="at least 12 characters"):
        backup_crypto.encrypt_file(str(tmp_path / "in"), str(tmp_path / "out"), passphrase)
    assert not (tmp_path / "out").exists()


def test_a_flipped_byte_anywhere_is_refused(tmp_path, small_chunks):
    sealed = _seal(tmp_path, os.urandom(300))
    raw = open(sealed, "rb").read()
    # Header fields, the salt, first chunk, middle, last tag.
    for position in [10, 12, 20, 40, backup_crypto._HEADER.size, len(raw) // 2, len(raw) - 1]:
        damaged = bytearray(raw)
        damaged[position] ^= 0x01
        path = tmp_path / f"flip{position}"
        path.write_bytes(damaged)
        _refused(tmp_path, str(path))


def test_a_truncated_file_is_refused(tmp_path, small_chunks):
    sealed = _seal(tmp_path, os.urandom(300))
    raw = open(sealed, "rb").read()
    step = 64 + backup_crypto.TAG
    cuts = [0, 5, backup_crypto._HEADER.size, backup_crypto._HEADER.size + step,
            backup_crypto._HEADER.size + 2 * step, len(raw) - 1]
    for cut in cuts:
        path = tmp_path / f"cut{cut}"
        path.write_bytes(raw[:cut])
        _refused(tmp_path, str(path))


def test_bytes_added_after_the_end_are_refused(tmp_path, small_chunks):
    sealed = _seal(tmp_path, os.urandom(300))
    for extra in (b"x", os.urandom(64 + backup_crypto.TAG)):
        path = tmp_path / f"extra{len(extra)}"
        path.write_bytes(open(sealed, "rb").read() + extra)
        _refused(tmp_path, str(path))


def test_a_dropped_or_reordered_chunk_is_refused(tmp_path, small_chunks):
    sealed = _seal(tmp_path, os.urandom(64 * 5))
    raw = open(sealed, "rb").read()
    head, body = raw[: backup_crypto._HEADER.size], raw[backup_crypto._HEADER.size:]
    step = 64 + backup_crypto.TAG
    chunks = [body[i : i + step] for i in range(0, len(body), step)]
    assert len(chunks) >= 5

    variants = {
        "dropped-middle": chunks[:1] + chunks[2:],
        "dropped-first": chunks[1:],
        "swapped": [chunks[1], chunks[0], *chunks[2:]],
        "repeated": chunks[:2] + [chunks[1]] + chunks[2:],
        "last-moved-up": [*chunks[:1], chunks[-1], *chunks[1:-1]],
    }
    for name, parts in variants.items():
        path = tmp_path / name
        path.write_bytes(head + b"".join(parts))
        _refused(tmp_path, str(path))


def test_a_header_asking_for_absurd_work_is_refused(tmp_path):
    sealed = _seal(tmp_path, b"x")
    raw = bytearray(open(sealed, "rb").read())
    raw[9] = 31  # log2(n)
    path = tmp_path / "huge"
    path.write_bytes(raw)
    _refused(tmp_path, str(path))


# -- through BackupService ----------------------------------------------------


def _service(root) -> BackupService:
    service = BackupService()
    service.eagent_root = str(root)
    service.base_paths = {str(root): str(root)}
    return service


def _zip_bytes(source: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("metadata.json", json.dumps({"environment_info": {"eagent_root": f"/{source}"}, "backup_name": "t"}))
        archive.writestr(f"{source}/usr/kept.md", "restored text")
    return out.getvalue()


@pytest.fixture
def instance(tmp_path):
    root = tmp_path / "eagent"
    (root / "usr").mkdir(parents=True)
    return root, str(tmp_path).strip("/") + "/backups"


def _upload(data: bytes) -> FileStorage:
    return FileStorage(io.BytesIO(data), filename="b.rzbak")


@pytest.fixture
def temp_dirs(tmp_path, monkeypatch):
    """Where the service puts its temporary files, so a test can look inside."""
    made = []

    def mkdtemp():
        path = tmp_path / f"tmp{len(made)}"
        path.mkdir()
        made.append(path)
        return str(path)

    monkeypatch.setattr(backup.tempfile, "mkdtemp", mkdtemp)
    return made


@pytest.mark.asyncio
async def test_an_encrypted_backup_restores_with_its_passphrase(tmp_path, instance, temp_dirs):
    root, source = instance
    sealed = _seal(tmp_path, _zip_bytes(source))
    data = open(sealed, "rb").read()

    result = await _service(root).restore_backup(_upload(data), passphrase=PASSPHRASE)

    assert result["encrypted"] is True
    assert (root / "usr" / "kept.md").read_text() == "restored text"
    assert not any(path.exists() for path in temp_dirs), "decrypted data stayed on disk"


@pytest.mark.asyncio
@pytest.mark.parametrize("passphrase", [None, "", "not the passphrase"])
async def test_a_wrong_or_missing_passphrase_writes_nothing(tmp_path, instance, temp_dirs, passphrase):
    root, source = instance
    data = open(_seal(tmp_path, _zip_bytes(source)), "rb").read()
    service = _service(root)

    for call in (service.restore_backup, service.preview_restore, service.inspect_backup):
        with pytest.raises(backup_crypto.BackupCryptoError):
            await call(_upload(data), passphrase=passphrase)

    assert list((root / "usr").iterdir()) == []
    assert not any(path.exists() for path in temp_dirs)


@pytest.mark.asyncio
async def test_a_damaged_backup_writes_nothing(tmp_path, instance, temp_dirs):
    root, source = instance
    data = bytearray(open(_seal(tmp_path, _zip_bytes(source)), "rb").read())
    data[-5] ^= 0xFF

    with pytest.raises(backup_crypto.BackupCryptoError, match="wrong or the file is damaged"):
        await _service(root).restore_backup(_upload(bytes(data)), passphrase=PASSPHRASE)

    assert list((root / "usr").iterdir()) == []
    assert not any(path.exists() for path in temp_dirs)


@pytest.mark.asyncio
async def test_inspect_and_preview_work_with_the_passphrase(tmp_path, instance):
    root, source = instance
    data = open(_seal(tmp_path, _zip_bytes(source)), "rb").read()
    service = _service(root)

    inspected = await service.inspect_backup(_upload(data), passphrase=PASSPHRASE)
    previewed = await service.preview_restore(_upload(data), passphrase=PASSPHRASE)

    assert inspected["encrypted"] is True and inspected["backup_name"] == "t"
    assert [f["archive_path"] for f in previewed["files_to_restore"]] == [f"{source}/usr/kept.md"]
    assert previewed["encrypted"] is True
    assert list((root / "usr").iterdir()) == [], "a preview wrote a file"


@pytest.mark.asyncio
async def test_an_old_plain_backup_still_restores_and_says_it_was_not_protected(instance):
    root, source = instance
    service = _service(root)
    data = _zip_bytes(source)

    assert (await service.inspect_backup(_upload(data)))["encrypted"] is False
    assert (await service.preview_restore(_upload(data)))["encrypted"] is False
    result = await service.restore_backup(_upload(data))

    assert result["encrypted"] is False
    assert (root / "usr" / "kept.md").read_text() == "restored text"


@pytest.mark.asyncio
async def test_a_created_backup_is_sealed_and_restores(tmp_path, monkeypatch, temp_dirs):
    root = tmp_path / "eagent"
    (root / "usr").mkdir(parents=True)
    secret = root / "usr" / ".env"
    secret.write_text("API_KEY_OPENAI=sk-do-not-leak")
    service = _service(root)

    async def matched(metadata, max_files=1000):
        return [{"path": f"{root}/usr/.env", "real_path": str(secret), "size": secret.stat().st_size,
                 "modified": "2026-01-01T00:00:00+00:00", "type": "file"}]

    async def info():
        return {"eagent_root": str(root)}

    async def author():
        return "test"

    monkeypatch.setattr(service, "test_patterns", matched)
    monkeypatch.setattr(service, "_get_system_info", info)
    monkeypatch.setattr(service, "_get_environment_info", info)
    monkeypatch.setattr(service, "_get_backup_author", author)
    monkeypatch.setattr(backup.db, "dump_data", lambda: {"tables": {"users": "$2b$12$hash-do-not-leak"}})
    monkeypatch.setattr(backup.db, "data_tables", lambda: ["users"])
    monkeypatch.setattr(backup.db, "load_data", lambda payload: {})

    sealed = await service.create_backup(["x"], [], backup_name="nightly", passphrase=PASSPHRASE)

    assert sealed.endswith(".rzbak")
    assert os.listdir(os.path.dirname(sealed)) == ["nightly.rzbak"], "the plaintext zip was left beside it"
    raw = open(sealed, "rb").read()
    assert b"sk-do-not-leak" not in raw and b"hash-do-not-leak" not in raw and not raw.startswith(b"PK")

    secret.unlink()
    result = await service.restore_backup(_upload(raw), passphrase=PASSPHRASE)
    assert result["encrypted"] is True
    assert secret.read_text() == "API_KEY_OPENAI=sk-do-not-leak"


@pytest.mark.asyncio
async def test_sealing_is_the_same_in_the_community_and_the_enterprise_build(tmp_path, monkeypatch, temp_dirs):
    # Every build protects its backups: the backup code must never ask which build it is.
    from helpers import license_client

    def asked():
        raise AssertionError("the backup code asked which build this is")

    for name in ("enterprise_build", "community_build", "_stamp_licence"):
        monkeypatch.setattr(license_client, name, asked)
    await test_a_created_backup_is_sealed_and_restores(tmp_path, monkeypatch, temp_dirs)


@pytest.mark.asyncio
async def test_a_backup_cannot_be_created_without_a_proper_passphrase(tmp_path):
    for passphrase in ("", "too short", None):
        with pytest.raises(backup_crypto.BackupCryptoError):
            await BackupService().create_backup(["x"], [], passphrase=passphrase)


# -- the endpoints --------------------------------------------------------------


@pytest.fixture
def audit_events(monkeypatch):
    from plugins._audit.helpers import audit

    recorded = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: recorded.append((event, fields)))
    return recorded


@pytest.mark.asyncio
async def test_the_endpoints_never_echo_or_log_the_passphrase(tmp_path, instance, audit_events, caplog, capsys, monkeypatch):
    from api import backup_create, backup_inspect, backup_restore, backup_restore_preview

    root, source = instance
    secret = "a-very-unusual-passphrase-91"
    data = open(_seal(tmp_path, _zip_bytes(source), passphrase=secret), "rb").read()
    monkeypatch.setattr(BackupService, "__init__", lambda self: None)
    monkeypatch.setattr(BackupService, "eagent_root", str(root), raising=False)
    monkeypatch.setattr(BackupService, "base_paths", {str(root): str(root)}, raising=False)
    monkeypatch.setattr(backup_restore, "load_tmp_chats", lambda: None)
    caplog.set_level(logging.DEBUG)
    app = Flask("test_backup_encryption")
    responses = []

    for module, cls in [
        (backup_inspect, "BackupInspect"),
        (backup_restore_preview, "BackupRestorePreview"),
        (backup_restore, "BackupRestore"),
    ]:
        for passphrase in (secret, ""):
            with app.test_request_context(
                "/api/x", method="POST",
                data={"backup_file": (io.BytesIO(data), "b.rzbak"), "passphrase": passphrase, "metadata": "{}"},
            ):
                handler = getattr(module, cls)(app, None)
                responses.append(await handler.process({}, request))

    assert [r["success"] for r in responses] == [True, False, True, False, True, False]
    assert responses[1]["error"] == "This backup is encrypted. Enter its passphrase."
    assert (root / "usr" / "kept.md").read_text() == "restored text"

    built = []

    async def create_backup(self, **kwargs):
        built.append(kwargs)

    monkeypatch.setattr(BackupService, "create_backup", create_backup)
    with app.test_request_context("/api/backup_create", method="POST", json={}):
        refused = await backup_create.BackupCreate(app, None).process(
            {"backup_name": "n", "passphrase": secret[:5], "include_patterns": ["x"]}, request
        )
    assert refused["success"] is False and "at least 12" in refused["error"]
    assert built == [], "a short passphrase still built an archive"

    seen = capsys.readouterr()
    everything = json.dumps(responses, default=str) + json.dumps(audit_events, default=str) + caplog.text + seen.out + seen.err
    assert secret not in everything and secret[:5] not in everything

