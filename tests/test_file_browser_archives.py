from pathlib import Path
import sys
import tarfile
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from api.extract_work_dir_archive import extract_archive
from helpers.file_browser import FileBrowser


@pytest.fixture(autouse=True)
def rooted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The caller's File Browser root, without the database that resolves it."""
    original = FileBrowser.__init__
    monkeypatch.setattr(
        FileBrowser, "__init__", lambda self, base_dir=None: original(self, base_dir=base_dir or tmp_path)
    )
    return tmp_path


def test_extract_archive_stays_inside_the_callers_root(tmp_path_factory: pytest.TempPathFactory, rooted: Path) -> None:
    """Another account's archive is not this account's to unpack, and the
    root-relative path the interface sends is."""
    elsewhere = tmp_path_factory.mktemp("another-account") / "theirs.zip"
    with zipfile.ZipFile(elsewhere, "w") as bundle:
        bundle.writestr("note.txt", "theirs")
    with pytest.raises(ValueError, match="Invalid file path"):
        extract_archive(str(elsewhere))
    assert not (elsewhere.parent / "theirs").exists()

    with zipfile.ZipFile(rooted / "mine.zip", "w") as bundle:
        bundle.writestr("note.txt", "mine")
    assert Path(extract_archive("/mine.zip")) == rooted / "mine"


def test_extract_archive_creates_unique_zip_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "notes.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("nested/note.txt", "hello")

    first = Path(extract_archive(str(archive)))
    second = Path(extract_archive(str(archive)))

    assert (first / "nested" / "note.txt").read_text() == "hello"
    assert second.name == "notes-2"


def test_extract_archive_handles_tar_gz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "readme.txt"
    source.write_text("hello")
    archive = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname="readme.txt")

    destination = Path(extract_archive(str(archive)))

    assert (destination / "readme.txt").read_text() == "hello"


def test_extract_archive_rejects_zip_path_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", "nope")

    with pytest.raises(ValueError, match="unsafe path"):
        extract_archive(str(archive))

    assert not (tmp_path / "unsafe").exists()
