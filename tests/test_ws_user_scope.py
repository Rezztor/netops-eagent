"""WebSocket handlers must run as the user who owns the connection.

Found while porting the browser viewer: every handler that touched per-user
storage failed with "This operation needs a user context, and none is active."

The cause is that handlers execute on a separate event-loop thread
(`helpers/defer.py` `execute_inside`), so a ContextVar entered by the dispatcher
never reaches them. The scope has to be pushed on the thread that runs the
handler.

This was not specific to the browser: it affected any WebSocket handler
resolving a per-user path, and it was invisible until one did.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import user_scope  # noqa: E402
from helpers.ws_manager import WsManager  # noqa: E402


class _RecordingHandler:
    """Stands in for a WsHandler, recording the scope it observed."""

    namespace = "/ws"
    identifier = "test.Recording"

    def __init__(self) -> None:
        self.seen: list[str | None] = []

    async def process(self, event_type: str, payload: dict, sid: str):
        self.seen.append(user_scope.get_current_user_id())
        return {"ok": True}


def _manager() -> WsManager:
    import threading

    return WsManager(socketio=None, lock=threading.RLock())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_handler_runs_as_the_connection_owner():
    manager = _manager()
    handler = _RecordingHandler()
    manager.sid_to_user[("/ws", "sid-1")] = "user-abc"

    await manager._scoped_process(handler)("some_event", {}, "sid-1")

    assert handler.seen == ["user-abc"]


@pytest.mark.asyncio
async def test_the_scope_does_not_leak_past_the_handler():
    """A ContextVar left set on a pooled thread would hand the next connection's
    events the previous user's identity."""
    manager = _manager()
    handler = _RecordingHandler()
    manager.sid_to_user[("/ws", "sid-1")] = "user-abc"

    await manager._scoped_process(handler)("some_event", {}, "sid-1")

    assert user_scope.get_current_user_id() is None


@pytest.mark.asyncio
async def test_two_connections_do_not_see_each_others_identity():
    manager = _manager()
    handler = _RecordingHandler()
    manager.sid_to_user[("/ws", "sid-1")] = "user-abc"
    manager.sid_to_user[("/ws", "sid-2")] = "user-xyz"

    scoped = manager._scoped_process(handler)
    await scoped("e", {}, "sid-1")
    await scoped("e", {}, "sid-2")
    await scoped("e", {}, "sid-1")

    assert handler.seen == ["user-abc", "user-xyz", "user-abc"]


@pytest.mark.asyncio
async def test_an_unauthenticated_connection_gets_no_scope():
    """`single_user` is the placeholder bucket for a connection with no account.
    Treating it as a user id would create a directory named after a placeholder
    and quietly pool everyone's data into it."""
    manager = _manager()
    handler = _RecordingHandler()
    manager.sid_to_user[("/ws", "sid-anon")] = "single_user"

    await manager._scoped_process(handler)("e", {}, "sid-anon")

    assert handler.seen == [None]


@pytest.mark.asyncio
async def test_an_unknown_connection_gets_no_scope():
    manager = _manager()
    handler = _RecordingHandler()

    await manager._scoped_process(handler)("e", {}, "sid-never-connected")

    assert handler.seen == [None]


def test_the_dispatcher_routes_through_the_scoped_wrapper():
    """A direct call to handler.process would bypass the scope entirely, and
    nothing else would report it."""
    source = (PROJECT_ROOT / "helpers" / "ws_manager.py").read_text(encoding="utf-8")
    invoke = source[source.index("async def _invoke_handler"):]
    invoke = invoke[: invoke.index("async def handle_connect")]
    assert "_scoped_process(handler)" in invoke
    assert "handler.process," not in invoke, (
        "the handler is being invoked directly, outside the user scope"
    )
