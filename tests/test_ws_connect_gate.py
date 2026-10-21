"""What the socket handshake admits, and by what name it gates a handler.

The handlers always failed closed, but the transport admitted an anonymous
socket and kept it: it received every lifecycle broadcast and had each frame
decoded before NO_HANDLERS. A plugin handler was looked up in api_policy by its
file basename, a key the table never uses. And a socket that asked for another
account's conversation was pushed to each time that conversation changed.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from helpers import rbac, ws  # noqa: E402


class _Server:
    def __init__(self):
        self.registered = {}

    def on(self, event, namespace=None):
        return lambda fn: self.registered.setdefault(event, fn)


class _Manager:
    def __init__(self):
        self.connects = []

    async def handle_connect(self, namespace, sid, user_id=None):
        self.connects.append((sid, user_id))


def _connect(monkeypatch, user, auth, *, manager=None):
    from flask import Flask
    from werkzeug.test import EnvironBuilder

    from helpers import cache, login

    monkeypatch.setattr(login, "current_user", lambda: user)
    server = _Server()
    app = Flask("test_ws_connect_gate", static_folder=None)
    app.secret_key = "test-secret"
    cache.clear(ws.CACHE_AREA)
    ws.register_ws_namespace(server, app, threading.RLock(), manager)
    environ = EnvironBuilder(
        path="/socket.io/", headers={"Origin": "http://localhost"}
    ).get_environ()
    return asyncio.run(server.registered["connect"]("sid-gate", environ, auth))


@pytest.fixture(autouse=True)
def _clean():
    from helpers import cache

    yield
    cache.clear(ws.CACHE_AREA)
    ws._active_handlers.pop("sid-gate", None)
    ws._ws_contexts.pop("sid-gate", None)


def test_an_anonymous_handshake_is_refused_and_not_registered(monkeypatch):
    manager = _Manager()

    assert _connect(monkeypatch, None, {"handlers": ["ws_webui"]}, manager=manager) is False
    assert manager.connects == []
    assert "sid-gate" not in ws._ws_contexts


def test_a_signed_in_handshake_is_registered_under_its_own_account(monkeypatch):
    """Never under a placeholder: `_scoped_process` exempts only the manager's
    own, and any other string would be entered as a user scope."""
    manager = _Manager()

    assert _connect(monkeypatch, {"id": "u1", "role": "user"}, {}, manager=manager) is True
    assert manager.connects == [("sid-gate", "u1")]


def test_a_plugin_socket_handler_is_gated_by_its_policy_key(tmp_path, monkeypatch):
    from helpers import api_policy, plugins, users

    plugin_dir = tmp_path / "plugins" / "demo"
    (plugin_dir / "api").mkdir(parents=True)
    (plugin_dir / "api" / "ws_gatedemo.py").write_text(
        "from helpers.ws import WsHandler\n"
        "class GateDemo(WsHandler):\n"
        "    @classmethod\n"
        "    def requires_csrf(cls):\n"
        "        return False\n"
        "    async def process(self, event, data, sid):\n"
        "        return None\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        plugins, "find_plugin_dir", lambda name: str(plugin_dir) if name == "demo" else None
    )
    path = "plugins/demo/ws_gatedemo"
    monkeypatch.setitem(api_policy.API_PERMISSIONS, path, rbac.PERM_SETTINGS_MANAGE)
    accounts = {
        "u1": {"id": "u1", "role": rbac.ROLE_USER, "disabled": False},
        "a1": {"id": "a1", "role": rbac.ROLE_ADMIN, "disabled": False},
    }
    monkeypatch.setattr(users, "get_user_by_id", accounts.get)

    assert _connect(monkeypatch, accounts["u1"], {"handlers": [path]}) is True
    assert ws._active_handlers["sid-gate"] == {}, "the policy entry was not applied"

    assert _connect(monkeypatch, accounts["a1"], {"handlers": [path]}) is True
    assert list(ws._active_handlers["sid-gate"]) == [path]


def test_another_accounts_conversation_is_not_followed(monkeypatch):
    from helpers import state_monitor, user_scope
    from helpers.state_snapshot import StateRequestV1

    class _Ctx:
        def __init__(self, owner):
            self.owner = owner

        def is_owned_by(self, user_id):
            return bool(user_id) and user_id == self.owner

    contexts = {"theirs": _Ctx("bob"), "mine": _Ctx("alice")}
    monkeypatch.setattr(state_monitor.AgentContext, "get", staticmethod(contexts.get))

    monitor = state_monitor.StateMonitor(debounce_seconds=60.0)
    dirtied = []
    monkeypatch.setattr(monitor, "mark_dirty", lambda ns, sid, **kw: dirtied.append(sid))

    def ask(sid, context):
        request = StateRequestV1(context=context, log_from=0, notifications_from=0, timezone="UTC")
        with user_scope.user_scope("alice"):
            monitor.update_projection("/ws", sid, request=request, seq_base=1)

    ask("sid-theirs", "theirs")
    ask("sid-mine", "mine")
    ask("sid-new", "not-yet-created")

    monitor.mark_dirty_for_context("theirs")
    monitor.mark_dirty_for_context("mine")
    monitor.mark_dirty_for_context("not-yet-created")

    assert dirtied == ["sid-mine", "sid-new"]
    assert monitor._projections[("/ws", "sid-theirs")].request.context is None
