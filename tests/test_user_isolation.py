"""Per-user data isolation.

Two boundaries have to hold independently: contexts live in a process-wide
registry, so one user must not be able to reach another's live context; and
chats are written to disk, so one user's files must not be readable at another
user's path.
"""

import threading

import pytest

from agent import AgentConfig, AgentContext
from helpers import files, user_scope
from helpers.context_utils import ContextAccessDenied, use_context
from helpers.task_scheduler import AdHocTask, TaskAccessDenied, assert_task_owner


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from testsupport import build_instance_root

    monkeypatch.setattr(files, "_base_dir", build_instance_root(tmp_path))
    return tmp_path


def _config():
    return AgentConfig(mcp_servers="", profile="default")


def _make_context(owner: str, ctxid: str) -> AgentContext:
    AgentContext.remove(ctxid)
    with user_scope.user_scope(owner):
        return AgentContext(config=_config(), id=ctxid, set_current=False)


# --- scope resolution -------------------------------------------------------

def test_path_traversal_in_a_user_id_is_refused():
    for bad in ("../../etc", "a/b", "", None, "x" * 65, "..", "a b"):
        assert not user_scope.is_safe_user_id(bad), bad
        with pytest.raises(user_scope.UserScopeError):
            user_scope.get_user_root(bad)


def test_scope_does_not_leak_out_of_its_block():
    assert user_scope.get_current_user_id() is None
    with user_scope.user_scope("alice-id"):
        assert user_scope.get_current_user_id() == "alice-id"
        with user_scope.user_scope("bob-id"):
            assert user_scope.get_current_user_id() == "bob-id"
        assert user_scope.get_current_user_id() == "alice-id"
    assert user_scope.get_current_user_id() is None


def test_scope_is_per_thread():
    """A scope pushed on one thread must not be visible on another, or a
    background agent run would adopt whichever user happened to be nearby."""
    seen = {}

    def worker():
        seen["worker"] = user_scope.get_current_user_id()

    with user_scope.user_scope("alice-id"):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert user_scope.get_current_user_id() == "alice-id"

    assert seen["worker"] is None


# --- in-memory context ownership -------------------------------------------

def test_context_records_its_owner():
    context = _make_context("alice-id", "ctx-owner-a")
    try:
        assert context.owner_id == "alice-id"
        assert context.is_owned_by("alice-id")
        assert not context.is_owned_by("bob-id")
        assert not context.is_owned_by(None)
    finally:
        AgentContext.remove("ctx-owner-a")


def test_listing_returns_only_your_own_contexts():
    _make_context("alice-id", "ctx-list-a")
    _make_context("bob-id", "ctx-list-b")
    try:
        alice = {c.id for c in AgentContext.resident_for_user("alice-id")}
        bob = {c.id for c in AgentContext.resident_for_user("bob-id")}
        assert "ctx-list-a" in alice and "ctx-list-b" not in alice
        assert "ctx-list-b" in bob and "ctx-list-a" not in bob
        # all() stays unscoped on purpose: framework maintenance needs it.
        every = {c.id for c in AgentContext.all()}
        assert {"ctx-list-a", "ctx-list-b"} <= every
    finally:
        AgentContext.remove("ctx-list-a")
        AgentContext.remove("ctx-list-b")


def test_the_index_is_scoped_the_same_way_the_contexts_are():
    """The index is what the sidebar lists once conversations load lazily, so it
    is a second surface the ownership boundary has to hold on -- and a new one.

    ADR 0003 records that the directory layout protected the data at rest and did
    nothing for a process-global cache sitting in front of it. This is such a
    cache.
    """
    _make_context("alice-id", "ctx-index-a")
    _make_context("bob-id", "ctx-index-b")
    try:
        alice = {summary.id for summary in AgentContext.index_for_user("alice-id")}
        bob = {summary.id for summary in AgentContext.index_for_user("bob-id")}
        assert "ctx-index-a" in alice and "ctx-index-b" not in alice
        assert "ctx-index-b" in bob and "ctx-index-a" not in bob
        assert AgentContext.index_for_user(None) == []
        assert AgentContext.index_for_user("") == []
    finally:
        AgentContext.remove("ctx-index-a")
        AgentContext.remove("ctx-index-b")


def test_an_index_entry_does_not_survive_removal():
    """Otherwise a deleted conversation would keep being listed, and clicking it
    would try to load a file that is gone."""
    _make_context("alice-id", "ctx-gone")
    AgentContext.remove("ctx-gone")
    assert "ctx-gone" not in {s.id for s in AgentContext.index_for_user("alice-id")}
    assert AgentContext.get("ctx-gone") is None


def test_unowned_contexts_belong_to_nobody():
    AgentContext.remove("ctx-unowned")
    context = AgentContext(config=_config(), id="ctx-unowned", set_current=False)
    try:
        assert context.owner_id is None
        assert AgentContext.resident_for_user(None) == []
        assert context not in AgentContext.resident_for_user("alice-id")
    finally:
        AgentContext.remove("ctx-unowned")


def test_opening_another_users_context_is_refused():
    _make_context("alice-id", "ctx-private")
    try:
        with user_scope.user_scope("alice-id"):
            assert use_context(threading.RLock(), "ctx-private").id == "ctx-private"

        with user_scope.user_scope("bob-id"):
            with pytest.raises(ContextAccessDenied):
                use_context(threading.RLock(), "ctx-private", create_if_not_exists=False)
    finally:
        AgentContext.remove("ctx-private")


def test_refusal_is_indistinguishable_from_not_found():
    """Otherwise an id probe tells you which conversations exist.

    This holds when the caller cannot mint a context at an id of their
    choosing. It is why the read-only endpoints pass create_if_not_exists=False:
    with auto-creation on, a fabricated id answers 200 while a real foreign id
    answers 404, which is exactly the oracle this asserts against.
    """
    _make_context("alice-id", "ctx-probe")
    try:
        with user_scope.user_scope("bob-id"):
            with pytest.raises(ContextAccessDenied) as taken:
                use_context(threading.RLock(), "ctx-probe", create_if_not_exists=False)
            with pytest.raises(ContextAccessDenied) as absent:
                use_context(threading.RLock(), "ctx-does-not-exist", create_if_not_exists=False)
        assert str(taken.value).replace("ctx-probe", "X") == str(
            absent.value
        ).replace("ctx-does-not-exist", "X")
    finally:
        AgentContext.remove("ctx-probe")


def test_read_endpoints_do_not_mint_contexts_for_arbitrary_ids():
    """Guards the fix for a leak this suite found in a live run: a non-owner got
    404 for a real conversation id but 200 for a fabricated one, because
    use_context created it. That difference enumerates existing ids."""
    import pathlib

    api_dir = pathlib.Path(__file__).resolve().parents[1] / "api"
    for name in (
        "history_get.py",
        "ctx_window_get.py",
        "chat_files_path_get.py",
        "chat_export.py",
        "subagents.py",
    ):
        source = (api_dir / name).read_text(encoding="utf-8")
        assert "create_if_not_exists=False" in source, name


@pytest.mark.parametrize(
    "module, payload",
    [
        ("message_queue_add", {"text": "run this as alice"}),
        ("message_queue_remove", {}),
        ("message_queue_send", {"send_all": True}),
        ("chat_create", {"new_context": "ctx-bobs-new"}),
        ("chat_reset", {}),
    ],
)
def test_endpoints_that_take_a_context_id_refuse_another_accounts(database, module, payload):
    """The queue handlers looked the conversation up directly, so one account
    could queue a message in another's chat and send it -- and a run executes as
    the owner. chat_create copied the other chat's project and model, and
    chat_reset cancelled its running task before checking whose it was."""
    import asyncio
    import importlib

    from flask import Flask

    from helpers import message_queue
    from helpers.api import ApiHandler
    from helpers.task_scheduler import TaskScheduler

    handler_module = importlib.import_module(f"api.{module}")
    handler_cls = next(
        value for value in vars(handler_module).values()
        if isinstance(value, type) and issubclass(value, ApiHandler) and value is not ApiHandler
    )
    cancelled = []
    scheduler = TaskScheduler.get()
    original = scheduler.cancel_tasks_by_context
    scheduler.cancel_tasks_by_context = lambda ctxid, **_: cancelled.append(ctxid)  # type: ignore[method-assign]

    alice = _make_context("alice-id", "ctx-alices")
    try:
        handler = handler_cls(Flask("ownership"), threading.RLock())
        body = {"context": "ctx-alices", "current_context": "ctx-alices", **payload}
        with user_scope.user_scope("bob-id"):
            with pytest.raises(ContextAccessDenied):
                asyncio.run(handler.process(body, None))
        assert not message_queue.has_queue(alice)
        assert cancelled == []
    finally:
        scheduler.cancel_tasks_by_context = original  # type: ignore[method-assign]
        AgentContext.remove("ctx-alices")
        AgentContext.remove("ctx-bobs-new")


def test_anonymous_caller_cannot_open_an_owned_context():
    _make_context("alice-id", "ctx-anon")
    try:
        with pytest.raises(ContextAccessDenied):
            use_context(threading.RLock(), "ctx-anon", create_if_not_exists=False)
    finally:
        AgentContext.remove("ctx-anon")


# --- on-disk separation -----------------------------------------------------

def test_chat_paths_are_rooted_per_user(isolated):
    from helpers import persist_chat

    alice = persist_chat.get_chat_folder_path("ctx-1", "alice-id")
    bob = persist_chat.get_chat_folder_path("ctx-1", "bob-id")
    assert alice != bob
    assert "/usr/users/alice-id/chats/ctx-1" in alice
    assert "/usr/users/bob-id/chats/ctx-1" in bob


@pytest.mark.parametrize("ctxid", ["..", "../..", "../../bob-id/chats/ctx-1", "/etc", "a/b", ""])
def test_a_context_id_is_one_path_segment(isolated, ctxid):
    """An id nobody owns passes the ownership check, and removing a chat deletes
    this folder -- so `../..` was the directory above every account's chats."""
    from helpers import persist_chat

    with pytest.raises(ValueError):
        persist_chat.get_chat_folder_path(ctxid, "alice-id")
    with pytest.raises(ValueError):
        persist_chat.get_chat_msg_files_folder(ctxid, "alice-id")


def test_saved_chat_ids_are_scoped(database, isolated):
    """Scoping used to be which directory was listed. It is the policy now, and
    `saved_chat_ids` does not mention owner_id -- the row-level policy decides
    which rows exist for the scope it is asked under."""
    from datetime import datetime, timezone

    from psycopg.types.json import Jsonb

    from helpers import db, persist_chat

    with db.unscoped_transaction() as conn:
        for owner, ctxid in (("alice-id", "ctx-a"), ("bob-id", "ctx-b")):
            conn.execute(
                """
                INSERT INTO conversations (id, owner_id, name, type, created_at,
                                           last_message, document)
                VALUES (%s, %s, %s, 'user', %s, %s, %s)
                """,
                (ctxid, owner, ctxid, datetime.now(timezone.utc),
                 datetime.now(timezone.utc), Jsonb({"id": ctxid})),
            )

    assert persist_chat.saved_chat_ids("alice-id") == {"ctx-a"}
    assert persist_chat.saved_chat_ids("bob-id") == {"ctx-b"}
    # No owner resolvable means nothing is claimed.
    assert persist_chat.saved_chat_ids(None) == set()


def test_a_context_without_an_owner_is_not_written(database, isolated):
    """Nothing to own it, so nowhere to put it.

    It used to mean no file was written. It means no row is written, and the
    stakes went up slightly: a row with no owner would be invisible under the
    policy and therefore undeletable, which is worse than not existing.
    """
    from types import SimpleNamespace

    from agent import AgentContextType
    from helpers import db, persist_chat

    context = SimpleNamespace(
        id="ctx-nowhere", type=AgentContextType.USER, data={}, owner_id=None
    )
    persist_chat.save_tmp_chat(context)

    with db.unscoped_transaction() as conn:
        assert conn.execute(
            "SELECT count(*) FROM conversations WHERE id = 'ctx-nowhere'"
        ).fetchone() == (0,)


def test_save_uses_the_contexts_own_owner_not_the_ambient_scope(database, isolated):
    """save_tmp_chats() sweeps every context, so each must be stored under its
    own owner regardless of who is scoped at the time.

    This matters more than it did. When the owner decided a directory, getting it
    wrong misfiled a conversation; now it decides which policy the write is
    checked against, so getting it wrong means the write is refused -- and a
    background sweep would fail rather than misfile.
    """
    from helpers import db, persist_chat

    context = _make_context("alice-id", "ctx-sweep")
    with user_scope.user_scope("bob-id"):
        persist_chat.save_tmp_chat(context)

    with db.unscoped_transaction() as conn:
        stored = conn.execute(
            "SELECT owner_id FROM conversations WHERE id = %s", ("ctx-sweep",)
        ).fetchone()
    assert stored == ("alice-id",)
    AgentContext.remove("ctx-sweep")


# --- endpoints that reach AgentContext directly -----------------------------

def _handler_files():
    """Every HTTP and WebSocket handler, core and plugin.

    Plugins were not scanned until 2026-09-18, and that is precisely where the
    gap was: `plugins/_editor` took a conversation id from request input and
    never checked it, and eight more handlers did the same. A guard that only
    looks at `api/` proves nothing about the two thirds of the surface that
    lives in `plugins/*/api/`.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    directories = [root / "api"] + sorted(root.glob("plugins/*/api"))
    for directory in directories:
        for path in sorted(directory.glob("*.py")):
            yield path.relative_to(root), path.read_text(encoding="utf-8")


def test_every_api_touching_a_context_checks_ownership():
    """Guard against the failure this suite already caught once: chat_remove
    resolved a context through AgentContext.use() instead of use_context(), so
    one user could delete another's conversation.

    Any handler reaching for AgentContext.use()/remove() bypasses the check in
    use_context and must make it explicitly.
    """
    offenders = [
        str(name)
        for name, source in _handler_files()
        if ("AgentContext.use(" in source or "AgentContext.remove(" in source)
        and "assert_context_access" not in source
    ]
    assert offenders == [], f"unguarded context access in: {offenders}"


def test_a_handler_taking_a_conversation_id_resolves_it_through_the_chokepoint():
    """The other half of the same rule, and the half that was missing.

    A handler does not have to call `AgentContext.use()` to cross accounts. It
    is enough to read `context_id` out of request input and use it to select
    data -- an editor buffer, a browser runtime, a model override, a skills
    catalogue. Nine handlers did exactly that, and the id is not a secret: it
    appears in every URL and every payload of the conversation it names.

    Either resolution goes through `use_context` (which checks) or the handler
    calls `assert_context_access` itself.
    """
    import re

    takes = re.compile(r'(?:input|data)\.get\(\s*["\'](?:ctxid|context_id|active_context_id)["\']')
    offenders = [
        str(name)
        for name, source in _handler_files()
        if takes.search(source)
        and not re.search(r"use_context|assert_context_access", source)
    ]
    assert offenders == [], (
        "these handlers take a conversation id from request input and never "
        f"check who owns it: {offenders}"
    )


def test_access_guard_allows_the_owner_and_blocks_everyone_else():
    from helpers.context_utils import assert_context_access

    _make_context("alice-id", "ctx-guard")
    try:
        with user_scope.user_scope("alice-id"):
            assert_context_access("ctx-guard")  # must not raise

        with user_scope.user_scope("bob-id"):
            with pytest.raises(ContextAccessDenied):
                assert_context_access("ctx-guard")

        # Anonymous callers get nothing either.
        with pytest.raises(ContextAccessDenied):
            assert_context_access("ctx-guard")
    finally:
        AgentContext.remove("ctx-guard")


def test_access_guard_is_silent_for_ids_that_do_not_exist():
    """Removing something already gone is not an ownership violation."""
    from helpers.context_utils import assert_context_access

    with user_scope.user_scope("alice-id"):
        assert_context_access("ctx-never-existed")
        assert_context_access("")


def test_denial_is_reported_as_not_found_not_as_a_server_error():
    """A traceback would leak internals, and a distinct status would confirm
    that the conversation exists."""
    api_source = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "helpers" / "api.py"
    ).read_text(encoding="utf-8")
    assert "ContextAccessDenied" in api_source
    assert "status=404" in api_source
    assert '"error": "Context not found."' in api_source
    # Same mapping, for the analogous scheduled-task guard below.
    assert "TaskAccessDenied" in api_source
    assert '"error": "Task not found."' in api_source


# --- scheduled task ownership ------------------------------------------------
# A task lives in one instance-wide usr/scheduler/tasks.json, unlike a context,
# which is at least keyed per registry lookup. assert_task_owner is what makes
# the personal scheduler_my_* endpoints (helpers/api_policy.py, chat.use) safe
# to open to every signed-in account instead of restricting them to
# scheduler.manage: every one of them checks a task's owner_id before acting.

def _task(owner_id, uuid="task-guard"):
    return AdHocTask(
        uuid=uuid,
        name="guarded task",
        system_prompt="",
        prompt="do a thing",
        owner_id=owner_id,
    )


def test_task_access_guard_allows_the_owner_and_blocks_everyone_else():
    task = _task("alice-id")
    assert_task_owner(task, "alice-id")  # must not raise

    with pytest.raises(TaskAccessDenied):
        assert_task_owner(task, "bob-id")

    # Anonymous callers get nothing either.
    with pytest.raises(TaskAccessDenied):
        assert_task_owner(task, None)


def test_task_access_guard_rejects_ownerless_tasks():
    """A task created before ownership was tracked, or by the scheduler tool
    outside a request, belongs to nobody's personal view -- not even an
    administrator's, through this guard. It stays reachable only through the
    instance-wide, scheduler.manage-gated screen, which never calls this."""
    task = _task(None)
    with pytest.raises(TaskAccessDenied):
        assert_task_owner(task, "alice-id")


def test_task_refusal_is_indistinguishable_from_not_found():
    task = _task("alice-id", uuid="task-probe")
    with pytest.raises(TaskAccessDenied) as taken:
        assert_task_owner(task, "bob-id")
    absent_task = _task(None, uuid="task-does-not-exist")
    with pytest.raises(TaskAccessDenied) as absent:
        assert_task_owner(absent_task, "bob-id")
    assert str(taken.value).replace("task-probe", "X") == str(absent.value).replace(
        "task-does-not-exist", "X"
    )


def test_every_personal_scheduler_api_checks_task_ownership():
    """Guard against the same failure class test_every_api_touching_a_context_
    checks_ownership guards for contexts: a scheduler_my_* handler that reaches
    a task by id without calling assert_task_owner would let any signed-in
    account act on anyone's reminder, not just their own."""
    import pathlib

    api_dir = pathlib.Path(__file__).resolve().parents[1] / "api"
    offenders = []
    for path in sorted(api_dir.glob("scheduler_my_*.py")):
        source = path.read_text(encoding="utf-8")
        acts_on_one_task = "task_id" in source and "get_task_by_uuid(" in source
        if acts_on_one_task and "assert_task_owner(" not in source:
            offenders.append(path.name)
    assert offenders == [], f"unguarded task access in: {offenders}"


def test_the_personal_task_list_filters_by_owner_not_by_a_client_supplied_id():
    """scheduler_my_tasks has no single task_id to guard -- it must filter its
    own listing, or every account's reminders would be visible to every other
    account the moment this endpoint was opened up to chat.use."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1] / "api" / "scheduler_my_tasks.py"
    ).read_text(encoding="utf-8")
    assert "require_current_user_id" in source
    assert "task.owner_id == owner_id" in source


# --- memory, workdir, uploads ----------------------------------------------

def test_memory_vector_store_is_per_user():
    """A vector store holds the text of what a user told the agent, so a shared
    index would surface one person's conversations in another's recall."""
    from plugins._memory.helpers import memory

    with user_scope.user_scope("alice-id"):
        alice = memory.abs_db_dir("main")
    with user_scope.user_scope("bob-id"):
        bob = memory.abs_db_dir("main")

    assert alice != bob
    assert "/usr/users/alice-id/memory/main" in alice
    assert "/usr/users/bob-id/memory/main" in bob


def test_memory_refuses_to_resolve_without_an_owner():
    """Failing closed beats falling back to a shared directory."""
    from plugins._memory.helpers import memory

    with pytest.raises(user_scope.UserScopeError):
        memory.abs_db_dir("main")


def test_knowledge_stays_shared():
    """Knowledge is the organisation's reference material, deliberately common
    to everyone -- unlike memory, which is what one person said."""
    from plugins._memory.helpers import memory

    with user_scope.user_scope("alice-id"):
        alice = memory.abs_knowledge_dir("default")
    with user_scope.user_scope("bob-id"):
        bob = memory.abs_knowledge_dir("default")

    assert alice == bob


def test_embeddings_cache_stays_shared():
    """It is keyed by a hash of the text and holds only vectors, so it exposes
    nothing to someone who does not already have the text."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "plugins" / "_memory" / "helpers" / "memory.py"
    ).read_text(encoding="utf-8")
    assert 'files.get_abs_path("tmp/memory/embeddings")' in source
    assert "Shared on purpose" in source


def test_workdir_is_separated_per_user():
    """Agent-executed code reads and writes here; one shared directory would let
    one user's agent read or overwrite another's work."""
    from helpers import workdir

    base = "/eagent/usr/workdir"
    alice = workdir.scope_to_user(base, "alice-id")
    bob = workdir.scope_to_user(base, "bob-id")

    assert alice != bob
    assert alice.endswith("/alice-id")
    assert bob.endswith("/bob-id")


def test_workdir_separation_survives_an_administrator_repointing_it():
    """The user segment is appended unconditionally, so changing a setting that
    does not mention users cannot silently remove isolation."""
    from helpers import workdir

    scoped = workdir.scope_to_user("/mnt/shared-volume/work", "alice-id")
    assert scoped.endswith("/alice-id")


def test_workdir_falls_back_to_the_base_without_an_owner():
    from helpers import workdir

    assert workdir.scope_to_user("/eagent/usr/workdir", None) == "/eagent/usr/workdir"


def test_upload_and_download_paths_are_per_user():
    with user_scope.user_scope("alice-id"):
        alice_up = user_scope.get_user_path(user_scope.UPLOADS_DIR)
        alice_down = user_scope.get_user_path(user_scope.DOWNLOADS_DIR)
    with user_scope.user_scope("bob-id"):
        bob_up = user_scope.get_user_path(user_scope.UPLOADS_DIR)

    assert alice_up != bob_up
    assert "/usr/users/alice-id/uploads" in alice_up
    assert "/usr/users/alice-id/downloads" in alice_down


def test_no_handler_still_writes_to_a_shared_upload_folder():
    """Bare filenames used to resolve under one usr/uploads for everybody."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    searched = list((root / "api").glob("*.py"))
    searched += list((root / "helpers").glob("*.py"))
    searched += list((root / "plugins").rglob("*.py"))

    offenders = []
    for path in searched:
        if "/tests/" in str(path) or "migration.py" in path.name:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if '"usr/uploads"' in source or "/eagent/usr/uploads" in source:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"shared upload folder still referenced in: {offenders}"


def test_prompt_rendering_degrades_without_an_owner():
    """Behaviour rules live in the user's memory directory. Outside a user
    context the system prompt must still render, using the defaults."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "plugins" / "_memory" / "extensions" / "python" / "system_prompt"
        / "_20_behaviour_prompt.py"
    ).read_text(encoding="utf-8")
    assert "except user_scope.UserScopeError" in source
    assert "return None" in source


def test_memory_index_cache_is_keyed_per_user_not_per_subdir_name():
    """Regression: the loaded-index cache was keyed by the subdir name.

    Every user calls their memory area "main", so the first user to load it
    populated the cache and every later user was handed that same FAISS index --
    one person's memories surfacing in another person's recall. The path is
    per-user, so keying on the resolved path separates them, while shared
    project memory resolves to one path and stays correctly shared.
    """
    from plugins._memory.helpers import memory

    with user_scope.user_scope("alice-id"):
        alice_key = memory.Memory.index_key("main")
    with user_scope.user_scope("bob-id"):
        bob_key = memory.Memory.index_key("main")

    assert alice_key != bob_key
    assert "alice-id" in alice_key and "bob-id" in bob_key


def test_memory_index_cache_is_shared_for_shared_project_memory():
    from plugins._memory.helpers import memory

    with user_scope.user_scope("alice-id"):
        alice_key = memory.Memory.index_key("projects/shared-project")
    with user_scope.user_scope("bob-id"):
        bob_key = memory.Memory.index_key("projects/shared-project")

    assert alice_key == bob_key


def test_the_mcp_server_reaches_only_chats_it_started(database):
    """One token serves the whole instance. With any chat id accepted, its holder
    continued an account's own conversation -- which runs as that account -- and
    could delete it."""
    import asyncio

    from helpers import mcp_server

    send = getattr(mcp_server.send_message, "fn", mcp_server.send_message)
    finish = getattr(mcp_server.finish_chat, "fn", mcp_server.finish_chat)

    _make_context("alice-id", "ctx-alice-mcp")
    try:
        sent = asyncio.run(send(message="run this as alice", chat_id="ctx-alice-mcp"))
        finished = asyncio.run(finish(chat_id="ctx-alice-mcp"))
        assert sent.error == "Chat not found"
        assert finished.error == "Chat not found"
        assert AgentContext.get("ctx-alice-mcp") is not None
    finally:
        AgentContext.remove("ctx-alice-mcp")
