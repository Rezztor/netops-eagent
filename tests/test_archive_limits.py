"""An archive an account supplies may not expand without bound."""

import io
import tarfile
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

from api import extract_work_dir_archive as extract
from helpers import archive_limits, skills_import
from helpers.archive_limits import ArchiveTooLarge, Limits
from helpers.backup import BackupService

BOMB = b"\0" * (3 * 1024 * 1024)  # a few KB deflated: well past 200:1


def _bomb_zip(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.bin", BOMB)
    return path


def test_zip_limits_count_member_total_ratio(tmp_path):
    z = zipfile.ZipFile(_bomb_zip(tmp_path / "b.zip"))
    for limits in (Limits(), Limits(ratio=10**9, member=1024), Limits(ratio=10**9, total=1024), Limits(ratio=10**9, members=0)):
        with pytest.raises(ArchiveTooLarge):
            archive_limits.extract_zip(z, tmp_path / "out", limits)
    archive_limits.extract_zip(z, tmp_path / "ok", Limits(ratio=10**9))
    assert (tmp_path / "ok" / "a.bin").stat().st_size == len(BOMB)


def test_a_lying_header_still_hits_the_cap_as_it_is_written():
    budget = archive_limits.Budget(Limits(total=1024, ratio=10**9))
    with pytest.raises(ArchiveTooLarge):
        budget.copy(io.BytesIO(b"x" * 4096), io.BytesIO())


def test_file_browser_refuses_zip_and_tar_bombs(tmp_path):
    with pytest.raises(ArchiveTooLarge):
        extract.extract_zip(_bomb_zip(tmp_path / "b.zip"), tmp_path / "z")
    tgz = tmp_path / "b.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        info = tarfile.TarInfo("a.bin")
        info.size = len(BOMB)
        t.addfile(info, io.BytesIO(BOMB))
    with pytest.raises(ArchiveTooLarge):
        extract.extract_tar(tgz, tmp_path / "t")
    assert not (tmp_path / "t" / "a.bin").exists()


def test_skills_import_refuses_a_zip_bomb(tmp_path):
    with pytest.raises(ArchiveTooLarge):
        skills_import._safe_extract_zip(_bomb_zip(tmp_path / "s.zip"), tmp_path / "out")


@pytest.mark.asyncio
async def test_backup_restore_refuses_before_it_cleans_or_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_limits, "BACKUP_LIMITS", Limits(members=2))
    path = tmp_path / "b.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name in ("metadata.json", "x", "y"):
            z.writestr(name, "{}")
    service = BackupService()
    service.eagent_root = str(tmp_path / "root")
    with pytest.raises(Exception, match="more than 2 members"):
        await service.restore_backup(backup_file=FileStorage(io.BytesIO(path.read_bytes()), filename="b.zip"), clean_before_restore=True)
