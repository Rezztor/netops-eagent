from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from helpers.file_browser import FileBrowser


def read(*parts: str) -> str:
    return PROJECT_ROOT.joinpath(*parts).read_text(encoding="utf-8")


def test_file_browser_remember_last_directory_defaults_enabled() -> None:
    settings_source = read("helpers", "settings.py")

    assert "file_browser_remember_last_directory: bool" in settings_source
    assert "file_browser_remember_last_directory=get_default_value(" in settings_source
    assert '"file_browser_remember_last_directory",\n            True,' in settings_source


def test_file_browser_empty_api_path_uses_default_workdir_contract() -> None:
    """The default view is the caller's own directory, not the container.

    This asserted `current_path = "/eagent"` for as long as `FileBrowser` was
    rooted at `/`, which pinned the vulnerable behaviour as the contract: the
    default listing was every file the application could read, reachable by an
    account holding only `chat.use`. The default is now the browser's own root.
    """
    api_source = read("api", "get_work_dir_files.py")
    api_dox = read("api", "get_work_dir_files.py.dox.md")

    assert 'current_path = request.args.get("path", "") or "$WORK_DIR"' in api_source
    assert 'current_path = "/eagent"' not in api_source
    assert 'current_path = ""' in api_source
    assert "Empty `path` requests and explicit `$WORK_DIR` requests resolve" in api_dox


def test_file_browser_reports_missing_directory(tmp_path: Path) -> None:
    """A missing directory *inside* the root still reads as missing.

    The browser is rooted at the caller's directory now, so this passes its own
    root explicitly rather than reaching at an arbitrary absolute path -- which
    is the case the next test covers, and which must read as refused rather
    than as missing.
    """
    missing_directory = tmp_path / "missing"

    result = FileBrowser(base_dir=tmp_path).get_files("missing")

    assert result["entries"] == []
    assert result["error"] == "Directory not found"


def test_file_browser_refuses_a_path_outside_its_root(tmp_path: Path) -> None:
    """The guard that was a no-op for as long as the root was `/`.

    Every containment check in `FileBrowser` compared with `str.startswith`
    against `self.base_dir`, and against `/` every one of them was true. A
    `chat.use` account could list `/`, read `usr/.env` and reach every other
    tenant's files. `..` and an absolute escape are the two shapes that matter.
    """
    browser = FileBrowser(base_dir=tmp_path)

    assert browser.get_files("/etc")["error"] == "Invalid path"
    assert browser.get_files("../..")["error"] == "Invalid path"

    with pytest.raises(ValueError):
        browser.get_full_path("/etc/hostname")


def test_file_browser_containment_is_a_path_boundary_not_a_string_prefix(tmp_path: Path) -> None:
    """`/root-evil` must not pass a check rooted at `/root`.

    `str.startswith` accepted it, because a string prefix is not a path
    boundary. This is the case that would survive a naive fix.
    """
    root = tmp_path / "root"
    root.mkdir()
    sibling = tmp_path / "root-evil"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("nope", encoding="utf-8")

    browser = FileBrowser(base_dir=root)

    assert browser.get_files(str(sibling))["error"] == "Invalid path"


def test_file_browser_moves_selected_items_without_overwriting_or_self_nesting(tmp_path: Path) -> None:
    # base_dir rather than assigning after construction: the default root is
    # now resolved from settings, so a bare FileBrowser() needs a database.
    browser = FileBrowser(base_dir=tmp_path)
    source_file = tmp_path / "note.md"
    source_folder = tmp_path / "skills"
    destination = tmp_path / "archive"
    source_file.write_text("hello", encoding="utf-8")
    source_folder.mkdir()
    destination.mkdir()

    moved = browser.move_items(["note.md", "skills"], "archive")

    assert moved == [str(destination / "note.md"), str(destination / "skills")]
    assert (destination / "note.md").read_text(encoding="utf-8") == "hello"
    assert (destination / "skills").is_dir()

    collision = tmp_path / "collision.md"
    collision.write_text("source", encoding="utf-8")
    (destination / "collision.md").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        browser.move_items(["collision.md"], "archive")
    assert collision.read_text(encoding="utf-8") == "source"
    assert (destination / "collision.md").read_text(encoding="utf-8") == "keep"

    nested = destination / "skills" / "nested"
    nested.mkdir()
    with pytest.raises(ValueError, match="cannot be moved into itself"):
        browser.move_items(["archive/skills"], "archive/skills/nested")




def test_a_folder_can_be_created_at_the_root_of_the_working_directory(tmp_path):
    """The first thing anyone does in an empty account.

    `api/rename_work_dir_file.py` refused an empty `parentPath` as a missing
    value, which was harmless only while `get_work_dir_files` answered
    "/eagent" for the default view -- a non-empty string that happened to
    satisfy the guard. Rooting the browser at the caller's own directory made
    that path "", and creating a folder at the root became impossible through
    the interface. Found in a GUI QA pass on 2026-09-19, caused by the commit
    that rooted the browser.

    "" is the working directory itself. Containment is what makes accepting it
    safe, and the test below is the other half of this pair.
    """
    browser = FileBrowser(base_dir=tmp_path)

    assert browser.create_folder("", "reports") is True
    assert (tmp_path / "reports").is_dir()


def test_creating_a_folder_still_cannot_escape_the_root(tmp_path):
    """Accepting "" must not have widened anything else."""
    root = tmp_path / "workdir"
    root.mkdir()
    browser = FileBrowser(base_dir=root)

    with pytest.raises(ValueError):
        browser.create_folder("../..", "escaped")
    assert not (tmp_path / "escaped").exists()


def test_the_handler_treats_an_absent_parent_path_as_the_root():
    """The guard is gone from the source, not worked around in the caller."""
    source = read("api", "rename_work_dir_file.py")

    assert '"Parent path is required"' not in source
    assert 'input.get("parentPath", current_path) or ""' in source


def test_the_paths_the_handlers_send_are_read_from_the_root(tmp_path: Path) -> None:
    """Every handler puts a slash in front of a listing path before it gets here.

    Joined as it stood, that slash replaced the root, so delete, edit, rename
    and move refused everything the interface sent as "Invalid path" -- from the
    day the root stopped being "/". The root itself is the one thing they must
    still refuse to remove.
    """
    browser = FileBrowser(base_dir=tmp_path)
    (tmp_path / "notes").mkdir()
    (tmp_path / "archive").mkdir()
    (tmp_path / "notes" / "a.txt").write_text("one", encoding="utf-8")

    assert browser.get_full_path("/notes/a.txt") == str(tmp_path / "notes" / "a.txt")
    assert browser.save_text_file("/notes/a.txt", "two")
    assert browser.rename_item("/notes/a.txt", "b.txt")
    assert browser.move_items(["/notes/b.txt"], "/archive") == [str(tmp_path / "archive" / "b.txt")]
    assert browser.delete_file("/archive/b.txt")
    assert not (tmp_path / "archive" / "b.txt").exists()

    assert not browser.delete_file("/") and not browser.delete_file("")
    assert tmp_path.is_dir() and (tmp_path / "notes").is_dir()
