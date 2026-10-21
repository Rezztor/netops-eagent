import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.asyncio
async def test_state_monitor_debounce_coalesces_without_postponing_and_cleanup_cancels_pending():
    from helpers.state_monitor import StateMonitor
    from helpers.state_snapshot import StateRequestV1

    namespace = "/ws"
    monitor = StateMonitor(debounce_seconds=10.0)
    monitor.register_sid(namespace, "sid-1")
    monitor.bind_manager(type("FakeManager", (), {"_dispatcher_loop": None})())
    monitor.update_projection(
        namespace,
        "sid-1",
        request=StateRequestV1(context=None, log_from=0, notifications_from=0, timezone="UTC"),
        seq_base=1,
    )

    monitor.mark_dirty(namespace, "sid-1")
    first = monitor._debounce_handles[(namespace, "sid-1")]

    monitor.mark_dirty(namespace, "sid-1")
    second = monitor._debounce_handles[(namespace, "sid-1")]

    # Throttled coalescing: subsequent dirties keep the scheduled push instead of postponing it.
    assert first is second
    assert not second.cancelled()

    monitor.unregister_sid(namespace, "sid-1")
    assert second.cancelled()
    assert (namespace, "sid-1") not in monitor._debounce_handles


@pytest.mark.asyncio
async def test_state_monitor_namespace_identity_prevents_cross_namespace_state_push(monkeypatch) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from helpers.state_monitor import StateMonitor
    from helpers.state_snapshot import StateRequestV1

    loop = asyncio.get_running_loop()
    push_ready = asyncio.Event()
    captured: list[tuple[str, str]] = []

    async def _emit_to(namespace: str, sid: str, event_type: str, _payload: object, **_kwargs):
        if event_type == "state_push":
            captured.append((namespace, sid))
            push_ready.set()

    class FakeManager:
        def __init__(self):
            self._dispatcher_loop = loop
            self.emit_to = AsyncMock(side_effect=_emit_to)

    monitor = StateMonitor(debounce_seconds=0.0)
    manager = FakeManager()
    monitor.bind_manager(manager, handler_id="tester")

    sid = "shared-sid"
    ns_a = "/a"
    ns_b = "/b"
    monitor.register_sid(ns_a, sid)
    monitor.register_sid(ns_b, sid)
    monitor.update_projection(
        ns_a,
        sid,
        request=StateRequestV1(context=None, log_from=0, notifications_from=0, timezone="UTC"),
        seq_base=1,
    )
    monitor.update_projection(
        ns_b,
        sid,
        request=StateRequestV1(context=None, log_from=0, notifications_from=0, timezone="UTC"),
        seq_base=1,
    )

    async def _fake_snapshot(**_kwargs):
        return {
            "log_version": 0,
            "notifications_version": 0,
            "logs": [],
            "contexts": [],
            "tasks": [],
            "notifications": [],
        }

    # Patch build_snapshot used by StateMonitor so this test stays lightweight.
    monkeypatch.setattr("helpers.state_monitor.build_snapshot_from_request", _fake_snapshot)

    monitor.mark_dirty(ns_a, sid, reason="test")
    await asyncio.wait_for(push_ready.wait(), timeout=1.0)

    assert captured
    assert all(ns == ns_a for ns, _ in captured)


@pytest.mark.asyncio
async def test_a_push_is_built_as_the_connections_own_user_whoever_triggered_it(monkeypatch) -> None:
    """Seen live: a push scheduled from another user's thread was built as that user."""
    import asyncio
    from unittest.mock import AsyncMock

    from helpers import user_scope
    from helpers.state_monitor import StateMonitor
    from helpers.state_snapshot import StateRequestV1

    loop = asyncio.get_running_loop()
    built_as: dict[str, str | None] = {}
    both = asyncio.Event()

    async def _emit_to(_namespace: str, sid: str, event_type: str, payload: dict, **_kwargs):
        if event_type == "state_push":
            built_as[sid] = payload["snapshot"]["built_as"]
            if len(built_as) == 2:
                both.set()

    class FakeManager:
        def __init__(self):
            self._dispatcher_loop = loop
            self.emit_to = AsyncMock(side_effect=_emit_to)

    async def _fake_snapshot(**_kwargs):
        return {"log_version": 0, "notifications_version": 0, "built_as": user_scope.get_current_user_id()}

    monkeypatch.setattr("helpers.state_monitor.build_snapshot_from_request", _fake_snapshot)
    monitor = StateMonitor(debounce_seconds=0.0)
    monitor.bind_manager(FakeManager(), handler_id="tester")
    request = StateRequestV1(context=None, log_from=0, notifications_from=0, timezone="UTC")
    for sid, user in (("sid-alice", "alice"), ("sid-bob", "bob")):
        monitor.register_sid("/ws", sid)
        with user_scope.user_scope(user):
            monitor.update_projection("/ws", sid, request=request, seq_base=1)

    with user_scope.user_scope("bob"):  # bob's agent changes something; every connection is marked
        monitor.mark_dirty_all(reason="test")
    await asyncio.wait_for(both.wait(), timeout=2.0)

    assert built_as == {"sid-alice": "alice", "sid-bob": "bob"}
