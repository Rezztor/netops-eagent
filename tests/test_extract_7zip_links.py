"""The 7-Zip path refuses a link member before 7-Zip writes anything.

7-Zip is not installed on every machine that runs the suite, so the binary is
replaced by a fake that answers the listing and records whether `x` ran.
"""

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from api import extract_work_dir_archive as extract
from helpers.file_browser import FileBrowser

HEADER = "7-Zip [64] 24.09\n\nListing archive: evil.7z\n\n--\nPath = evil.7z\nType = 7z\n\n----------\n"


@pytest.fixture(autouse=True)
def rooted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    original = FileBrowser.__init__
    monkeypatch.setattr(
        FileBrowser, "__init__", lambda self, base_dir=None: original(self, base_dir=base_dir or tmp_path)
    )
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/7zz")
    return tmp_path


def fake_7zip(monkeypatch: pytest.MonkeyPatch, listing: str) -> list:
    extracted = []

    def run(argv, **kwargs):
        if argv[1] == "l":
            return SimpleNamespace(stdout=HEADER + listing)
        output = Path(argv[3].removeprefix("-o"))
        extracted.append(output)
        (output / "note.txt").write_text("hello")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(subprocess, "run", run)
    return extracted


@pytest.mark.parametrize(
    "member",
    [
        "Path = lnk\nSize = 9\nAttributes = A_ lrwxrwxrwx\n",
        "Path = lnk\nSize = 0\nAttributes = AL\n",
        "Path = lnk\nSize = 0\nSymbolic Link = /eagent/usr\n",
        "Path = lnk\nSize = 0\nHard Link = ../../usr/.env\n",
    ],
)
def test_a_link_member_is_refused_before_extraction(rooted: Path, monkeypatch, member: str) -> None:
    (rooted / "evil.7z").write_bytes(b"x" * 64)
    extracted = fake_7zip(monkeypatch, member + "\nPath = lnk/.env\nSize = 5\nAttributes = A_ -rw-r--r--\n")

    with pytest.raises(ValueError, match="symbolic link"):
        extract.extract_archive("/evil.7z")

    assert extracted == []
    assert sorted(p.name for p in rooted.iterdir()) == ["evil.7z"]


def test_a_plain_archive_is_staged_then_moved_into_place(rooted: Path, monkeypatch) -> None:
    (rooted / "notes.7z").write_bytes(b"x" * 64)
    extracted = fake_7zip(
        monkeypatch,
        "Path = note.txt\nSize = 5\nAttributes = A_ -rw-r--r--\nSymbolic Link = \n\n"
        "Path = docs\nSize = 0\nAttributes = D_ drwxr-xr-x\n",
    )

    target = Path(extract.extract_archive("/notes.7z"))

    assert target == rooted / "notes"
    assert (target / "note.txt").read_text() == "hello"
    assert extracted and extracted[0] != target
    assert sorted(p.name for p in rooted.iterdir()) == ["notes", "notes.7z"]
