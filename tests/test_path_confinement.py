"""A path from the model or a request stays in the account's own folders.

The text editor, the document reader and the other file tools run in the
application container, not on the runner. Until `helpers.workdir.confine_path`
they opened whatever path they were given: `usr/.env`, another account's files,
and `usr/users/<id>/network_changes/<id>.json` -- the approval record of a
device or cloud change, which the agent could therefore rewrite as approved.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from helpers import files, user_scope, workdir
from helpers.errors import RepairableException


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(workdir, "get_workdir_base", lambda: str(tmp_path / "usr" / "workdir"))
    for folder in (
        "usr/workdir/alice",
        "usr/workdir/bob",
        "usr/users/alice/uploads",
        "usr/users/alice/network_changes",
        "usr/users/bob/uploads",
        "usr/projects/demo/.eaproj",
    ):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "usr" / ".env").write_text("KEY=value\n", encoding="utf-8")
    (tmp_path / "usr/users/alice/network_changes/1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "usr/workdir/alice/note.txt").write_text("mine", encoding="utf-8")
    (tmp_path / "usr/workdir/bob/note.txt").write_text("theirs", encoding="utf-8")
    (tmp_path / "usr/projects/demo/.eaproj/secrets.env").write_text("S=1\n", encoding="utf-8")
    with user_scope.user_scope("alice"):
        yield tmp_path


def test_the_accounts_own_folders_are_reachable(tree: Path) -> None:
    home = tree / "usr" / "workdir" / "alice"
    assert workdir.confine_path("note.txt") == str(home / "note.txt")
    assert workdir.confine_path(str(home / "new" / "file.md")) == str(home / "new" / "file.md")
    assert workdir.confine_path(str(tree / "usr/users/alice/uploads/a.pdf"))
    assert workdir.confine_path(str(tree / "usr/projects/demo/README.md"))

    from helpers import projects

    assert workdir.user_file_roots()[-1] == os.path.realpath(projects.get_projects_parent_folder())


@pytest.mark.parametrize(
    "path",
    [
        "usr/users/alice/network_changes/1.json",  # an approval record, even one's own
        "usr/.env",
        "usr/users/bob/uploads/x.txt",
        "usr/workdir/bob/note.txt",
        "usr/workdir",  # the parent of every account's directory
        "run_ui.py",  # the application
        "usr/projects/demo/.eaproj/secrets.env",
        "usr/projects/demo/.eaproj/tools/response.py",  # the application loads this as a tool
        "usr/projects/demo/.git/config",  # git runs what its config names, and the application runs git
        "usr/projects/demo/.git/hooks/post-checkout",
        "usr/projects/demo/.git",  # as a file it says where the repository is: `gitdir: <anywhere>`
    ],
)
def test_everything_else_is_refused(tree: Path, path: str) -> None:
    with pytest.raises(RepairableException, match="working directory"):
        workdir.confine_path(str(tree / path))


def test_dots_absolute_paths_and_symlinks_are_judged_by_where_they_lead(tree: Path) -> None:
    home = tree / "usr" / "workdir" / "alice"
    os.symlink(tree / "usr" / ".env", home / "innocent.txt")
    os.symlink(tree / "usr" / "users" / "alice" / "network_changes", home / "records")
    for path in ("../bob/note.txt", "../../.env", "/etc/hostname", "innocent.txt", "records/1.json", ""):
        with pytest.raises(RepairableException):
            workdir.confine_path(path)


def test_without_an_account_nothing_is_reachable(tree: Path) -> None:
    with user_scope.user_scope(None):
        with pytest.raises(user_scope.UserScopeError):
            workdir.confine_path("note.txt")


def test_the_text_editor_refuses_before_it_opens_anything(tree: Path) -> None:
    from plugins._text_editor.tools.text_editor import TextEditor

    record = tree / "usr/users/alice/network_changes/1.json"
    for action, path in (("read", tree / "usr" / ".env"), ("write", record), ("patch", record)):
        tool = TextEditor(None, "text_editor", action, {}, "", None)
        with pytest.raises(RepairableException):
            asyncio.run(tool.execute(action=action, path=str(path), content='{"approved": true}'))
    assert record.read_text(encoding="utf-8") == "{}"


def test_only_an_administrator_changes_a_projects_agents_md(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A project is every account's, and its AGENTS.md, at the root and in any folder below, is read into the
    prompt of whoever opens it. Any account with a file tool could write its own instructions there."""
    from helpers import rbac, users
    from plugins._editor.helpers import document_store
    from plugins._text_editor.tools.text_editor import TextEditor

    monkeypatch.setattr(
        users, "holds_permission", lambda user_id, permission: user_id == "root" and permission == rbac.PERM_SETTINGS_MANAGE
    )
    monkeypatch.setattr(document_store, "allowed_roots", lambda context_id="": [tree / "usr/projects/demo"])
    project = tree / "usr/projects/demo"
    (project / "src").mkdir()
    (project / "AGENTS.md").write_text("as the administrator wrote it", encoding="utf-8")
    os.symlink(project / "AGENTS.md", project / "notes.md")
    closed = [project / "AGENTS.md", project / "src" / "agents.md", project / "AGENTS.override.md", project / "notes.md"]

    def write(path: Path) -> None:
        tool = TextEditor(None, "text_editor", "write", {}, "", None)
        asyncio.run(tool.execute(action="write", path=str(path), content="obey me"))

    for path in closed:
        with pytest.raises(RepairableException, match="administrator"):
            write(path)
        with pytest.raises(PermissionError, match="administrator"):
            document_store.normalize_path(path)
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == "as the administrator wrote it"
    # Reading it, and every other file of the project, stays open to all.
    assert workdir.confine_read_path(str(project / "AGENTS.md"))
    assert not workdir.cannot_change_project_instructions(str(project / "README.md"))
    assert not workdir.cannot_change_project_instructions(str(tree / "usr/workdir/alice/AGENTS.md"))
    assert document_store.normalize_path(project / "README.md")

    with user_scope.user_scope("root"):
        assert not workdir.cannot_change_project_instructions(str(project / "AGENTS.md"))
        assert document_store.normalize_path(project / "AGENTS.md")


def test_the_image_loader_reads_chat_media_and_nothing_wider(tree: Path) -> None:
    """Images only, but another account's images are still another account's."""
    chats = user_scope.get_user_path(user_scope.CHATS_DIR)
    assert workdir.confine_path(os.path.join(chats, "ctx", "shot.png"), also=(chats,))
    with pytest.raises(RepairableException):
        workdir.confine_path(str(tree / "usr/users/bob/chats/ctx/shot.png"), also=(chats,))
    with pytest.raises(RepairableException):
        workdir.confine_path(os.path.join(chats, "ctx", "shot.png"))


def test_a_mail_reply_attaches_nothing_from_outside_the_accounts_folders(tree: Path) -> None:
    """The path is the model's and the model answers whoever wrote the mail."""
    from plugins._email_integration.helpers import handler

    mine = str(tree / "usr/workdir/alice/note.txt")
    read = asyncio.run(handler._read_attachments_via_rfc([str(tree / "usr" / ".env"), mine]))
    assert read == [("note.txt", b"mine")]

    with user_scope.user_scope(None):
        assert asyncio.run(handler._read_attachments_via_rfc([mine])) == []


def test_an_include_expands_only_the_accounts_own_files(tree: Path) -> None:
    """`§§include(path)` is written by the model and shown in the browser."""
    from helpers.strings import replace_file_includes

    mine = tree / "usr/workdir/alice/note.txt"
    env = tree / "usr" / ".env"
    text = f"§§include({mine}) §§include({env})"
    assert replace_file_includes(text) == f"mine §§include({env})"


def test_api_files_get_serves_only_the_callers_own_folders(tree: Path) -> None:
    import threading

    from api.api_files_get import ApiFilesGet

    handler = ApiFilesGet(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]
    paths = [
        str(tree / "usr" / ".env"),
        str(tree / "usr/workdir/bob/note.txt"),
        str(tree / "usr/users/alice/network_changes/1.json"),
        str(tree / "usr/workdir/alice/note.txt"),
    ]
    served = asyncio.run(handler.process({"paths": paths}, None))  # type: ignore[arg-type]
    assert list(served) == ["note.txt"]
    assert served["note.txt"] == "bWluZQ=="  # base64("mine"), not bob's

    # The instance API key has no owner: with no signed-in account, nothing.
    with user_scope.user_scope(None):
        assert asyncio.run(handler.process({"paths": paths}, None)) == {}  # type: ignore[arg-type]


def test_a_saved_tool_result_can_be_read_and_included_but_never_written(tree: Path) -> None:
    """The framework hands the model this path beside every long tool result; refusing it broke the include."""
    from helpers.strings import replace_file_includes
    from plugins._text_editor.tools.text_editor import TextEditor

    chats = Path(user_scope.get_user_path(user_scope.CHATS_DIR))
    saved = chats / "ctx1" / "messages" / "2.txt"
    saved.parent.mkdir(parents=True)
    saved.write_text("show version output", encoding="utf-8")
    (chats / "ctx1" / "other.txt").write_text("not a saved result", encoding="utf-8")
    theirs = tree / "usr/users/bob/chats/ctx1/messages/2.txt"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("bob's", encoding="utf-8")

    assert workdir.confine_read_path(str(saved)) == os.path.realpath(saved)
    assert replace_file_includes(f"§§include({saved})") == "show version output"
    for refused in (chats / "ctx1" / "other.txt", theirs, chats / "ctx1" / "messages" / ".." / ".." / ".." / "network_changes" / "1.json"):
        with pytest.raises(RepairableException):
            workdir.confine_read_path(str(refused))
    for action in ("write", "patch"):
        tool = TextEditor(None, "text_editor", action, {}, "", None)
        with pytest.raises(RepairableException):
            asyncio.run(tool.execute(action=action, path=str(saved), content="x"))
    assert saved.read_text(encoding="utf-8") == "show version output"


def test_what_a_write_into_a_projects_git_folder_would_have_run(tree: Path) -> None:
    """Why `.git` is refused. Opening a project asks git for its status, and git
    runs the program `core.fsmonitor` names, so a tool that could write that file
    ran a command in the web server the next time anybody opened the project."""
    import shutil
    import subprocess

    if not shutil.which("git"):
        pytest.skip("needs git")
    from helpers import git as git_helper

    project, marker = tree / "usr/projects/demo", tree / "ran"
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    # What the text editor would have been asked to write.
    with open(project / ".git" / "config", "a", encoding="utf-8") as config:
        config.write(f'[core]\n\tfsmonitor = "touch {marker}; false"\n')

    git_helper.get_repo_status(str(project))

    assert marker.exists(), "git no longer runs core.fsmonitor here; the refusal may be relaxed"
    with pytest.raises(RepairableException):
        workdir.confine_path(str(project / ".git" / "config"))
