"""Storing and serving an attachment.

The property that matters is that one user cannot reach another's file, and it
is structural: the owner comes from the session, so an id from someone else's
transcript resolves against the caller's own directory and is simply absent.
"""

import asyncio
import threading

import pytest

from helpers import files, user_scope
from plugins._ea_attachments.api.attachment_get import AttachmentGet
from plugins._ea_attachments.helpers import store

ALICE = "alice"
BOB = "bob"


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    for part in ("usr/plugins", "plugins", f"usr/users/{ALICE}", f"usr/users/{BOB}"):
        (tmp_path / part).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _save(user, name="report.txt", body=b"hello", max_files=200):
    return store.save(
        user, filename=name, content=body, context_id="ctx-1",
        source_path=f"/workspace/{name}", max_files=max_files,
    )


class _Args:
    def __init__(self, **kw):
        self._kw = kw

    def get(self, key, default=None):
        return self._kw.get(key, default)


class _Request:
    def __init__(self, **kw):
        self.args = _Args(**kw)


def _fetch(attachment_id, as_user):
    handler = AttachmentGet(app=None, thread_lock=threading.RLock())
    with user_scope.user_scope(as_user):
        return asyncio.run(handler.process({"id": attachment_id}, _Request(id=attachment_id)))


# --- names are never paths --------------------------------------------------

@pytest.mark.parametrize(
    "given, expected",
    [
        ("report.xlsx", "report.xlsx"),
        ("../../etc/passwd", "passwd"),
        ("/absolute/path.txt", "path.txt"),
        ("with spaces.txt", "with_spaces.txt"),
        (".hidden", "hidden"),
        ("", "attachment"),
        ("..", "attachment"),
    ],
)
def test_a_name_the_model_chose_cannot_become_a_path(given, expected):
    assert store.safe_filename(given) == expected


def test_a_traversing_name_still_lands_inside_the_users_directory(instance):
    record = _save(ALICE, name="../../../../etc/passwd")
    found = store.locate(ALICE, record["id"])
    assert found is not None
    assert files.is_in_dir(found[0], store.root(ALICE))


# --- isolation --------------------------------------------------------------

def test_the_owner_can_download_their_own_attachment(instance):
    record = _save(ALICE, body=b"the report")
    response = _fetch(record["id"], ALICE)
    assert response.status_code == 200
    assert response.get_data() == b"the report"
    assert 'filename="report.txt"' in response.headers["Content-Disposition"]


def test_another_user_cannot_download_it(instance):
    # Not "is refused" -- it does not resolve at all. The id is looked up in the
    # caller's own directory, where it was never written.
    record = _save(ALICE, body=b"the report")
    response = _fetch(record["id"], BOB)
    assert response.status_code == 404


def test_an_unauthenticated_request_is_refused(instance):
    record = _save(ALICE)
    handler = AttachmentGet(app=None, thread_lock=threading.RLock())
    response = asyncio.run(handler.process({"id": record["id"]}, _Request(id=record["id"])))
    assert response.status_code == 401


@pytest.mark.parametrize(
    "bad", ["", "../../etc/passwd", "not-hex", "a" * 31, "a" * 33, "../" * 4]
)
def test_an_id_that_is_not_a_plain_token_reaches_nothing(instance, bad):
    _save(ALICE)
    assert store.locate(ALICE, bad) is None
    assert _fetch(bad, ALICE).status_code == 404


# --- serving ----------------------------------------------------------------

def test_it_is_served_as_a_download_and_never_rendered(instance):
    # A stored .html served as text/html would run in the user's own origin.
    record = _save(ALICE, name="page.html", body=b"<script>alert(1)</script>")
    response = _fetch(record["id"], ALICE)
    assert response.mimetype == "application/octet-stream"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Disposition"].startswith("attachment;")


# --- housekeeping -----------------------------------------------------------

def test_the_oldest_are_pruned_past_the_cap(instance):
    kept = None
    for index in range(4):
        kept = _save(ALICE, name=f"file{index}.txt", max_files=2)
    manifest = store.read_manifest(ALICE)
    assert len(manifest) == 2
    assert kept["id"] in manifest


def test_pruning_removes_the_file_as_well_as_the_record(instance):
    first = _save(ALICE, name="a.txt", max_files=1)
    _save(ALICE, name="b.txt", max_files=1)
    assert store.locate(ALICE, first["id"]) is None
    assert not (instance / "usr" / "users" / ALICE / "attachments" / first["id"]).exists()


def test_one_users_attachments_do_not_appear_in_anothers_manifest(instance):
    _save(ALICE)
    _save(BOB)
    assert len(store.read_manifest(ALICE)) == 1
    assert len(store.read_manifest(BOB)) == 1
    assert set(store.read_manifest(ALICE)) != set(store.read_manifest(BOB))


# --- reaching the conversation the user is reading -------------------------


class _Log:
    def __init__(self):
        self.entries = []

    def log(self, **kwargs):
        self.entries.append(kwargs)
        return object()


class _Ctx:
    def __init__(self, ctx_id, parent=None):
        self.id = ctx_id
        self.log = _Log()
        self._data = {"_parallel_parent_context_id": parent} if parent else {}

    def get_data(self, key):
        return self._data.get(key)

    def set_data(self, key, value):
        self._data[key] = value


def _attach_tool(context):
    from plugins._ea_attachments.tools.attach_file import AttachFile

    tool = AttachFile.__new__(AttachFile)
    tool.agent = type("A", (), {"context": context})()
    tool.args = {}
    return tool


def _run_attach(monkeypatch, tool, worker, parent):
    """Drive execute() with the runner read and the context lookup stubbed."""
    import asyncio

    from agent import AgentContext
    from helpers import user_scope
    from plugins._ea_attachments.tools import attach_file as module

    monkeypatch.setattr(
        AgentContext, "get", staticmethod(lambda cid: parent if cid == parent.id else None)
    )
    monkeypatch.setattr(module.AttachFile, "_read", lambda self, *a, **k: _async(b"pdf"))
    with user_scope.user_scope("alice"):
        return asyncio.run(tool.execute(path="/tmp/x.pdf", name="x.pdf"))


async def _async(value):
    return value


def test_a_parallel_dispatch_still_shows_the_user_the_download(instance, monkeypatch):
    """The worker context is deleted the moment the job returns. Logging the
    download control into it is how an attachment goes missing from the very
    conversation that asked for it, while the tool reports success."""
    parent = _Ctx("real-chat")
    worker = _Ctx("throwaway", parent="real-chat")
    tool = _attach_tool(worker)

    response = _run_attach(monkeypatch, tool, worker, parent)

    assert "Attached x.pdf" in response.message
    assert worker.log.entries == []
    assert len(parent.log.entries) == 1
    kvps = parent.log.entries[0]["kvps"]
    assert kvps["attachment_name"] == "x.pdf"
    assert kvps["attachment_id"]


def test_the_file_is_filed_against_the_conversation_not_the_worker(instance, monkeypatch):
    """A context id that no longer exists is not a conversation anyone can open."""
    from plugins._ea_attachments.helpers import store

    parent = _Ctx("real-chat")
    worker = _Ctx("throwaway", parent="real-chat")
    _run_attach(monkeypatch, _attach_tool(worker), worker, parent)

    records = list(store.read_manifest("alice").values())
    assert [r["context_id"] for r in records] == ["real-chat"]
