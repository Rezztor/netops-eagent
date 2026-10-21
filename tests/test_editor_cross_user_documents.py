"""The editor's document store against ADR 0034's ownership boundary.

`helpers/workdir.py` states the intent plainly: crossing accounts "would let one
user's agent read, overwrite or delete another's work", so the workdir is scoped
per user unconditionally. `document_store.document_home()` honours that -- it
calls `workdir.scope_to_user()`.

These tests check whether the rest of the module honours it too. They are the
verification step for the outstanding work ADR 0034 records against
`plugins/_editor`: ownership columns with the right shape and no policy behind
them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import files, user_scope


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    for part in ("usr/workdir", "usr/users", "usr/plugins"):
        (tmp_path / part).mkdir(parents=True, exist_ok=True)
    # The accounts these tests act as have to exist, because an owner is now a
    # real account rather than any directory name that happens to be safe.
    for account in ("alice", "bob"):
        (tmp_path / "usr/users" / account).mkdir(parents=True, exist_ok=True)

    from plugins._editor.helpers import document_store

    # The module resolves these at import time from the real base dir.
    state = tmp_path / "usr/plugins/_editor/documents"
    monkeypatch.setattr(document_store, "STATE_DIR", state)
    monkeypatch.setattr(document_store, "DB_PATH", state / "documents.sqlite3")
    monkeypatch.setattr(document_store, "BACKUP_DIR", state / "backups")
    monkeypatch.setattr(document_store, "WORKDIR", tmp_path / "usr/workdir")
    monkeypatch.setattr(document_store, "DOCUMENTS_DIR", tmp_path / "usr/workdir/documents")
    # Keep the settings indirection from reaching a real settings file.
    monkeypatch.setattr(document_store, "_settings", lambda: _NoSettings())
    return tmp_path


class _NoSettings:
    def get_settings(self):
        return {}


def _write_document_for(base: Path, user_id: str, name: str, text: str) -> Path:
    """A document in one account's own scoped workdir, the way the agent writes one."""
    with user_scope.user_scope(user_id):
        from helpers import workdir as workdir_helper

        home = Path(workdir_helper.scope_to_user(str(base / "usr/workdir")))
    home.mkdir(parents=True, exist_ok=True)
    path = home / name
    path.write_text(text, encoding="utf-8")
    return path


def test_the_workdir_is_scoped_per_account(isolated):
    """The premise. If this fails the other tests are meaningless."""
    from helpers import workdir as workdir_helper

    with user_scope.user_scope("alice"):
        alice = workdir_helper.scope_to_user(str(isolated / "usr/workdir"))
    with user_scope.user_scope("bob"):
        bob = workdir_helper.scope_to_user(str(isolated / "usr/workdir"))

    assert alice != bob
    assert Path(alice).name == "alice"
    assert Path(bob).name == "bob"


def test_one_account_cannot_register_a_document_from_another(isolated):
    """`normalize_path` refuses a path in another account's workdir.

    `allowed_roots` carries only the account-scoped `document_home`, never the
    unscoped `usr/workdir` parent that contains every account's directory.
    """
    from plugins._editor.helpers import document_store

    secret = _write_document_for(isolated, "bob", "bob-private.md", "bob's private notes")

    with user_scope.user_scope("alice"):
        with pytest.raises(PermissionError):
            document_store.normalize_path(secret)


def test_opening_by_file_id_does_not_cross_accounts(isolated):
    """`get_document` selects on `file_id` alone, with no owner predicate.

    A `file_id` is an opaque uuid, so this is not trivially guessable -- but
    unguessability is not an authorisation check, and the API takes `file_id`
    straight from request input (`api/editor_session.py`, action "open").
    """
    from plugins._editor.helpers import document_store

    secret = _write_document_for(isolated, "bob", "bob-private.md", "bob's private notes")

    with user_scope.user_scope("bob"):
        registered = document_store.register_document(str(secret))
    file_id = registered["file_id"]

    with user_scope.user_scope("alice"):
        with pytest.raises((PermissionError, FileNotFoundError)):
            document_store.get_document(file_id)


def test_a_document_written_before_owners_were_recorded_is_backfilled(isolated):
    """The decorative default is recoverable, not orphaned.

    Rows predate identity being recorded at all, but they store a path, and the
    path carries the account segment. The backfill reads the owner back out of
    it rather than throwing the row away or leaving it readable by everyone.
    """
    from plugins._editor.helpers import document_store

    secret = _write_document_for(isolated, "bob", "bob-legacy.md", "written before owners")

    # Register it the way the old code did: the decorative default, explicitly.
    with user_scope.user_scope("bob"):
        registered = document_store.register_document(
            str(secret), owner_id=document_store.DEFAULT_OWNER
        )
    file_id = registered["file_id"]
    assert registered["owner_id"] == document_store.DEFAULT_OWNER

    # The next connect backfills it from the path, so bob still has his document
    with user_scope.user_scope("bob"):
        assert document_store.get_document(file_id)["owner_id"] == "bob"

    # ...and alice does not.
    with user_scope.user_scope("alice"):
        with pytest.raises(PermissionError):
            document_store.get_document(file_id)


def test_a_shared_document_stays_readable(isolated):
    """A document in no account's directory has no owner, and keeps working.

    The shared documents folder is a sibling of the per-account directories, not
    one of them. Treating it as owned by whoever touched it first would break
    the thing it exists for.
    """
    from plugins._editor.helpers import document_store

    shared_dir = isolated / "usr/workdir/documents"
    shared_dir.mkdir(parents=True, exist_ok=True)
    shared = shared_dir / "shared-notes.md"
    shared.write_text("everyone's", encoding="utf-8")

    assert document_store.account_from_path(shared) == ""

    with user_scope.user_scope("bob"):
        file_id = document_store.register_document(str(shared))["file_id"]
    with user_scope.user_scope("alice"):
        assert document_store.get_document(file_id)["path"] == str(shared.resolve())


def test_a_session_records_the_account_that_opened_it(isolated):
    """`create_session` used to stamp one literal on every session."""
    from plugins._editor.helpers import document_store

    doc = _write_document_for(isolated, "bob", "bob-session.md", "hello")
    with user_scope.user_scope("bob"):
        file_id = document_store.register_document(str(doc))["file_id"]
        session = document_store.create_session(file_id)

    # user_id is stored rather than returned, so read it back from the row.
    with document_store.connect() as conn:
        stored = conn.execute(
            "SELECT user_id FROM sessions WHERE session_id = ?",
            (session["session_id"],),
        ).fetchone()

    assert stored["user_id"] == "bob"


def test_a_directory_that_is_not_an_account_is_shared_rather_than_orphaned(isolated):
    """`usr/workdir/reports/` is not an account, whatever it looks like.

    Any name matching `is_safe_user_id` used to become an owner, so a report
    written with no user in scope was stamped `owner_id="reports"` and refused
    to every account there is -- and `_backfill_owners` re-stamped it on every
    connect, so a document that opened yesterday stopped opening today.
    """
    from plugins._editor.helpers import document_store

    shared = isolated / "usr/workdir/reports"
    shared.mkdir(parents=True, exist_ok=True)
    path = shared / "q3.md"
    path.write_text("# Q3", encoding="utf-8")

    assert document_store.account_from_path(path) == ""

    registered = document_store.register_document(str(path))
    file_id = registered["file_id"]
    for account in ("alice", "bob"):
        with user_scope.user_scope(account):
            assert document_store.get_document(file_id)["file_id"] == file_id


def test_save_as_takes_its_owner_from_where_the_file_lands(isolated):
    """Not from the row it was copied from.

    The path guard already stops a Save As into *another* account's directory,
    so the reachable case is the shared document copied into the caller's own:
    carrying the source's owner left a file inside alice's private workdir
    marked shared, readable by everyone who could guess its id.
    """
    from plugins._editor.helpers import document_store

    shared_dir = isolated / "usr/workdir/documents"
    shared_dir.mkdir(parents=True, exist_ok=True)
    source_path = shared_dir / "template.md"
    source_path.write_text("# Template", encoding="utf-8")
    source = document_store.register_document(str(source_path))
    assert source["owner_id"] == document_store.DEFAULT_OWNER

    with user_scope.user_scope("alice"):
        from helpers import workdir as workdir_helper

        home = Path(workdir_helper.scope_to_user(str(isolated / "usr/workdir")))
        home.mkdir(parents=True, exist_ok=True)
        saved = document_store.save_text_document_as(
            source["file_id"], str(home / "from-template.md"), "# Mine"
        )
        assert saved["owner_id"] == "alice"

    with user_scope.user_scope("bob"):
        with pytest.raises((PermissionError, FileNotFoundError)):
            document_store.get_document(saved["file_id"])


# --- the buffer in front of the store ---------------------------------------

def _open_for(manager, account: str, path: Path, context_id: str = "") -> dict:
    from plugins._editor.helpers import document_store

    with user_scope.user_scope(account):
        doc = document_store.register_document(str(path))
        return manager.open(doc, sid="", context_id=context_id)


def test_one_account_cannot_see_anothers_open_documents(isolated):
    """`list_open` filtered on the conversation id alone, and the default
    conversation id is the empty string -- so every caller who named none shared
    one bucket, and B saw what A had open."""
    from plugins._editor.helpers.markdown_sessions import MarkdownSessionManager

    manager = MarkdownSessionManager()
    _open_for(manager, "alice", _write_document_for(isolated, "alice", "plan.md", "# Plan"))

    with user_scope.user_scope("bob"):
        assert manager.list_open() == []
    with user_scope.user_scope("alice"):
        assert [item["title"] for item in manager.list_open()] == ["plan.md"]


def test_one_account_cannot_read_or_write_anothers_buffer(isolated):
    """The document store checks ownership on read. The buffer sits in front of
    it: `input` never touches the store, and the victim's own save is what
    persists the text -- through the victim's scope, where the store's check
    sees the rightful owner and passes."""
    from plugins._editor.helpers.markdown_sessions import MarkdownSessionManager

    manager = MarkdownSessionManager()
    path = _write_document_for(isolated, "alice", "notes.md", "# Alice")
    opened = _open_for(manager, "alice", path)
    session_id = opened["session_id"]

    with user_scope.user_scope("bob"):
        for call in (
            lambda: manager.input(session_id, text="# Bob was here"),
            lambda: manager.save(session_id, text="# Bob was here"),
            lambda: manager.save_as(session_id, str(isolated / "usr/workdir/bob/stolen.md")),
            lambda: manager.activate(session_id),
        ):
            with pytest.raises(FileNotFoundError):
                call()
        # Closing is reported as nothing to close, for the same reason the
        # others report not-found: neither answer should be available.
        assert manager.close(session_id) == {"ok": True, "closed": 0}

    with user_scope.user_scope("alice"):
        assert manager._require(session_id).text == "# Alice"
    assert path.read_text(encoding="utf-8") == "# Alice"


def test_a_session_id_is_not_a_capability(isolated):
    """It is a uuid4, and it travels in every editor payload. Unguessability is
    not an authorisation check -- the same reason `get_document` stopped
    trusting a file_id."""
    from plugins._editor.helpers.markdown_sessions import MarkdownSessionManager

    manager = MarkdownSessionManager()
    opened = _open_for(manager, "alice", _write_document_for(isolated, "alice", "a.md", "# A"))

    with user_scope.user_scope("bob"):
        with pytest.raises(FileNotFoundError) as refused:
            manager.input(opened["session_id"], text="x")
    # Worded as not-found, so probing cannot tell it apart from a session that
    # never existed.
    assert "not found" in str(refused.value)


def test_closing_a_store_session_is_bounded_by_the_account(isolated):
    """`store_session_id` arrives from request input like everything else here,
    and closing somebody's session drops the record of who has a document
    open."""
    from plugins._editor.helpers import document_store

    path = _write_document_for(isolated, "alice", "shared-open.md", "# Open")
    with user_scope.user_scope("alice"):
        doc = document_store.register_document(str(path))
        session = document_store.create_session(doc["file_id"], permission="write")

    with user_scope.user_scope("bob"):
        assert document_store.close_session(session_id=session["session_id"]) == 0

    with user_scope.user_scope("alice"):
        assert document_store.close_session(session_id=session["session_id"]) == 1


def test_socket_disconnect_closes_editor_sessions_without_a_user_scope(isolated, monkeypatch):
    """WebSocket disconnect calls `close_sid` outside any request scope. `close`
    refused to drop a buffer whose owner did not match the empty current owner,
    so every session opened over the socket stayed in memory after the socket
    went away."""
    import asyncio
    from unittest.mock import MagicMock

    from plugins._editor.api.ws_editor import WsEditor
    from plugins._editor.helpers import markdown_sessions
    from plugins._editor.helpers.markdown_sessions import MarkdownSessionManager

    manager = MarkdownSessionManager()
    markdown_sessions.get_manager()  # ensure the module-level slot exists
    monkeypatch.setattr(markdown_sessions, "_manager", manager)

    path = _write_document_for(isolated, "alice", "live.md", "# Live")
    opened = _open_for(manager, "alice", path)
    session_id = opened["session_id"]
    manager._sessions[session_id].sid = "sid-alice"

    assert user_scope.get_current_user_id() in (None, "")
    asyncio.run(WsEditor(MagicMock(), MagicMock()).on_disconnect("sid-alice"))

    assert session_id not in manager._sessions


@pytest.mark.parametrize(
    "inside",
    [
        ".eaproj/instructions/extra.md",  # read into every account's system prompt
        ".eaproj/agents/primary/prompts/agent.system.main.md",
        ".eaproj/knowledge/main/note.txt",
        ".git/description.txt",
    ],
)
def test_a_projects_private_folders_are_not_documents(isolated, inside):
    """`api/projects.py` keeps a project's instructions to administrators, because
    they are every account's system prompt, and `workdir.confine_path` keeps every
    file tool out of `.eaproj`. The editor admitted the whole projects folder, so
    any signed-in account could write those files as documents."""
    from plugins._editor.helpers import document_store

    target = isolated / "usr/projects/demo" / inside
    target.parent.mkdir(parents=True)

    with user_scope.user_scope("bob"):
        with pytest.raises(PermissionError):
            document_store.normalize_path(target)
        # The project's own files stay documents (ADR 0034: a project has no owner).
        assert document_store.normalize_path(isolated / "usr/projects/demo/README.md")


def test_a_link_into_a_projects_private_folder_is_refused_too(isolated):
    from plugins._editor.helpers import document_store

    secret = isolated / "usr/projects/demo/.eaproj/instructions/x.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("instructions", encoding="utf-8")
    link = isolated / "usr/projects/demo/notes.md"
    link.symlink_to(secret)

    with user_scope.user_scope("bob"):
        with pytest.raises(PermissionError):
            document_store.normalize_path(link)


def test_an_owned_document_is_refused_when_no_account_is_in_scope(isolated):
    """Audit 6, L7: with nobody in scope `_assert_may_read` used to pass, the
    opposite of what row-level security answers a query with no scope."""
    from plugins._editor.helpers import document_store

    doc = _write_document_for(isolated, "bob", "owned.md", "bob's")
    with user_scope.user_scope("bob"):
        file_id = document_store.register_document(str(doc))["file_id"]
        assert document_store.get_document(file_id)["owner_id"] == "bob"

    assert user_scope.get_current_user_id() in (None, "")
    with pytest.raises(PermissionError):
        document_store.get_document(file_id)
