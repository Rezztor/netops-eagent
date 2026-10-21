"""Both download resolvers stop at the File Browser root.

There are two, and they had the same defect: `base_dir = Path("/")`, against
which the `relative_to` containment check succeeds for every absolute path on
the box. The single-file and the multi-file download therefore handed out
anything the application could read, to any signed-in account -- including the
`user` role, whose whole permission set is `chat.use`.

This file used to assert that, in as many words: it wrote `outside-workdir.txt`
outside the working directory and required the resolver to return it. The name
said the quiet part. What the two resolvers must agree on is the root, not the
absence of one.
"""

from pathlib import Path
import zipfile

import pytest

from api.download_work_dir_file import resolve_download_path as resolve_one
from api.download_work_dir_files import (
    create_selected_zip,
    resolve_download_path as resolve_many,
)
from helpers.file_browser import FileBrowser


@pytest.fixture()
def rooted(tmp_path, monkeypatch):
    """Point both resolvers at a root this test owns.

    They construct their own `FileBrowser`, which resolves the caller's
    directory from settings and therefore a database. Patching the constructor
    keeps this a unit test of the containment rule.
    """
    root = tmp_path / "workdir"
    root.mkdir()

    original = FileBrowser.__init__

    def fixed(self, base_dir=None):
        original(self, base_dir=base_dir or root)

    monkeypatch.setattr(FileBrowser, "__init__", fixed)
    return root


def test_a_file_inside_the_root_resolves(rooted):
    inside = rooted / "note.txt"
    inside.write_text("downloadable", encoding="utf-8")

    assert resolve_one(str(inside)) == str(inside.resolve())
    assert resolve_many(str(inside), rooted) == inside.resolve()
    assert resolve_one("note.txt") == str(inside.resolve())


def test_a_leading_slash_reads_as_relative_to_the_root(rooted):
    """The handler prepends "/" to every relative path, and `get_files` reports
    entries relative to the root -- so "/notes/x.txt" means the root's notes
    folder, not the filesystem's. The two readings coincided only while the
    root was "/", and rooting the browser broke downloading your own file until
    both were accepted. Only the live app showed it; every unit test passed.
    """
    (rooted / "notes").mkdir()
    inside = rooted / "notes" / "mine.txt"
    inside.write_text("hello", encoding="utf-8")

    assert resolve_one("/notes/mine.txt") == str(inside.resolve())
    assert resolve_many("/notes/mine.txt", rooted) == inside.resolve()


def test_a_file_outside_the_root_is_refused(rooted, tmp_path):
    outside = tmp_path / "outside-workdir.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError):
        resolve_one(str(outside))
    with pytest.raises(ValueError):
        resolve_many(str(outside), rooted)


def test_traversal_out_of_the_root_is_refused(rooted):
    with pytest.raises(ValueError):
        resolve_one("../../etc/hostname")
    with pytest.raises(ValueError):
        resolve_many("/etc/hostname", rooted)


def test_the_zip_carries_what_is_inside_the_root(rooted):
    inside = rooted / "kept.txt"
    inside.write_text("kept", encoding="utf-8")

    archive_path = Path(create_selected_zip([str(inside)]))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.namelist() == ["kept.txt"]
            assert archive.read("kept.txt") == b"kept"
    finally:
        archive_path.unlink(missing_ok=True)


def test_a_selection_touching_anything_outside_the_root_is_refused(rooted, tmp_path):
    """The whole request fails, rather than the archive quietly arriving short.

    `create_selected_zip` skips a path that does not *exist*, but an
    out-of-root path raises: a selection nobody could have produced from a
    legitimate listing is a malformed request, and the handler answers 400.
    Dropping it silently would hand back an archive that looks complete.
    """
    inside = rooted / "kept.txt"
    inside.write_text("kept", encoding="utf-8")
    outside = tmp_path / "leaked.txt"
    outside.write_text("leaked", encoding="utf-8")

    with pytest.raises(ValueError):
        create_selected_zip([str(inside), str(outside)])
    with pytest.raises(ValueError):
        create_selected_zip([str(outside)])


def test_file_info_answers_only_inside_the_root(rooted, tmp_path):
    """It took any path, so it told any account whether a file exists anywhere
    in the container, how big it is and what its mode is."""
    import asyncio
    import threading

    from flask import Flask, request

    from api.file_info import FileInfoApi

    (rooted / "note.txt").write_text("mine", encoding="utf-8")
    outside = tmp_path / "theirs.txt"
    outside.write_text("theirs", encoding="utf-8")

    app = Flask("test_file_info")
    handler = FileInfoApi(app, threading.Lock())
    with app.test_request_context("/api/file_info"):
        inside = asyncio.run(handler.process({"path": "/note.txt"}, request))
        refused = asyncio.run(handler.process({"path": str(outside)}, request))

    assert inside["exists"] and inside["size"] == 4
    assert "exists" not in refused and refused["error"]


def test_a_folder_download_does_not_leave_its_archive_behind(rooted):
    """The zip is a temporary file made for one response, and nothing removed it."""
    from api.download_work_dir_file import stream_file_download

    (rooted / "note.txt").write_text("downloadable", encoding="utf-8")
    archive = create_selected_zip(["/note.txt"])

    response = stream_file_download(archive, download_name="selection.zip", delete_after=True)
    assert b"".join(response.response).startswith(b"PK")
    response.close()

    assert not Path(archive).exists()
