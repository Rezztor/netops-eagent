import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.ws import WsHandler, _SecurityContext, _check_security


# Handler variants with different security flag combinations

class _OpenHandler(WsHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    async def process(self, event, data, sid):
        return None


class _AuthOnlyHandler(WsHandler):
    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    async def process(self, event, data, sid):
        return None


class _CsrfHandler(WsHandler):
    """Default: requires_auth=True, requires_csrf=True (via default)."""

    async def process(self, event, data, sid):
        return None


class _LoopbackHandler(WsHandler):
    @classmethod
    def requires_loopback(cls) -> bool:
        return True

    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    async def process(self, event, data, sid):
        return None


class _ApiKeyHandler(WsHandler):
    @classmethod
    def requires_api_key(cls) -> bool:
        return True

    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    async def process(self, event, data, sid):
        return None


# Helper to build _SecurityContext quickly

#: The accounts the contexts below belong to, as the store would answer for them:
#: `_check_security` reads the account again on every message.
_ACCOUNTS: dict = {}


@pytest.fixture(autouse=True)
def _account_store(monkeypatch):
    from helpers import users

    _ACCOUNTS.clear()
    monkeypatch.setattr(users, "get_user_by_id", _ACCOUNTS.get)


def _ctx(
    *,
    authenticated=True,
    user_id="test-user-id",
    csrf_token="tok",
    client_csrf_token="tok",
    csrf_cookie="tok",
    remote_addr="127.0.0.1",
    api_key=None,
    must_change_password=False,
) -> _SecurityContext:
    if user_id:
        _ACCOUNTS[user_id] = {"id": user_id, "disabled": False, "must_change_password": must_change_password}
    return _SecurityContext(
        authenticated=authenticated,
        user_id=user_id,
        csrf_token=csrf_token,
        client_csrf_token=client_csrf_token,
        csrf_cookie=csrf_cookie,
        remote_addr=remote_addr,
        api_key=api_key,
        must_change_password=must_change_password,
    )


# Development harnesses do not answer on a deployed instance

def test_development_handlers_are_refused_outside_development(monkeypatch):
    from api.ws_dev_test import WsDevTest
    from api.ws_hello import WsHello
    from helpers import runtime

    monkeypatch.setattr(runtime, "is_development", lambda: False)
    for handler in (WsDevTest, WsHello):
        assert _check_security(handler, _ctx())["code"] == "NOT_AVAILABLE"
    assert _check_security(_OpenHandler, _ctx()) is None

    monkeypatch.setattr(runtime, "is_development", lambda: True)
    assert _check_security(WsDevTest, _ctx()) is None


# Open handler (no security)

def test_open_handler_always_passes():
    assert _check_security(_OpenHandler, _ctx()) is None


def test_open_handler_passes_even_without_tokens():
    assert _check_security(_OpenHandler, _ctx(csrf_token=None, client_csrf_token=None, csrf_cookie=None)) is None


# Loopback

def test_loopback_allows_127_0_0_1():
    assert _check_security(_LoopbackHandler, _ctx(remote_addr="127.0.0.1")) is None


def test_loopback_allows_ipv6():
    assert _check_security(_LoopbackHandler, _ctx(remote_addr="::1")) is None


def test_loopback_rejects_remote():
    result = _check_security(_LoopbackHandler, _ctx(remote_addr="192.168.1.50"))
    assert result is not None
    assert result["code"] == "FORBIDDEN"


def test_loopback_rejects_none():
    result = _check_security(_LoopbackHandler, _ctx(remote_addr=None))
    assert result is not None
    assert result["code"] == "FORBIDDEN"


# Auth

def test_auth_passes_for_authenticated_session():
    result = _check_security(_AuthOnlyHandler, _ctx(authenticated=True))
    assert result is None


def test_auth_rejects_unauthenticated_session():
    result = _check_security(_AuthOnlyHandler, _ctx(authenticated=False, user_id=None))
    assert result is not None
    assert result["code"] == "AUTH_REQUIRED"


def test_auth_rejects_session_without_user():
    result = _check_security(_AuthOnlyHandler, _ctx(authenticated=False, user_id=None))
    assert result is not None
    assert result["code"] == "AUTH_REQUIRED"


def test_auth_fails_closed_when_no_account_session():
    """Rezztor EAgent fails CLOSED. Upstream let the socket through whenever no
    credentials were configured; an unauthenticated socket is now always
    rejected."""
    result = _check_security(_AuthOnlyHandler, _ctx(authenticated=False, user_id=None))
    assert result is not None
    assert result["code"] == "AUTH_REQUIRED"


# CSRF

def test_csrf_passes_with_all_tokens_matching():
    result = _check_security(_CsrfHandler, _ctx(csrf_token="abc", client_csrf_token="abc", csrf_cookie="abc"))
    assert result is None


def test_csrf_rejects_missing_server_token():
    result = _check_security(_CsrfHandler, _ctx(csrf_token=None, client_csrf_token="abc", csrf_cookie="abc"))
    assert result is not None
    assert result["code"] == "CSRF_MISSING"


def test_csrf_rejects_missing_client_token():
    result = _check_security(_CsrfHandler, _ctx(csrf_token="abc", client_csrf_token=None, csrf_cookie="abc"))
    assert result is not None
    assert result["code"] == "CSRF_INVALID"


def test_csrf_rejects_mismatched_client_token():
    result = _check_security(_CsrfHandler, _ctx(csrf_token="abc", client_csrf_token="xyz", csrf_cookie="abc"))
    assert result is not None
    assert result["code"] == "CSRF_INVALID"


def test_csrf_rejects_mismatched_cookie():
    result = _check_security(_CsrfHandler, _ctx(csrf_token="abc", client_csrf_token="abc", csrf_cookie="wrong"))
    assert result is not None
    assert result["code"] == "CSRF_COOKIE"


# API Key

@patch("helpers.settings.get_settings", return_value={"mcp_server_token": "secret-key-123"})
def test_api_key_passes_with_correct_key(_mock_settings):
    result = _check_security(_ApiKeyHandler, _ctx(api_key="secret-key-123"))
    assert result is None


@patch("helpers.settings.get_settings", return_value={"mcp_server_token": "secret-key-123"})
def test_api_key_rejects_wrong_key(_mock_settings):
    result = _check_security(_ApiKeyHandler, _ctx(api_key="wrong-key"))
    assert result is not None
    assert result["code"] == "API_KEY_REQUIRED"


@patch("helpers.settings.get_settings", return_value={"mcp_server_token": "secret-key-123"})
def test_api_key_rejects_missing_key(_mock_settings):
    result = _check_security(_ApiKeyHandler, _ctx(api_key=None))
    assert result is not None
    assert result["code"] == "API_KEY_REQUIRED"


# Combined flags

class _FullSecurityHandler(WsHandler):
    @classmethod
    def requires_loopback(cls) -> bool:
        return True

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def requires_api_key(cls) -> bool:
        return True

    async def process(self, event, data, sid):
        return None


@patch("helpers.settings.get_settings", return_value={"mcp_server_token": "key"})
def test_full_security_passes_when_all_match(_mock_settings):
    result = _check_security(
        _FullSecurityHandler,
        _ctx(
            remote_addr="127.0.0.1",
            authenticated=True,
            csrf_token="tok",
            client_csrf_token="tok",
            csrf_cookie="tok",
            api_key="key",
        ),
    )
    assert result is None


@patch("helpers.settings.get_settings", return_value={"mcp_server_token": "key"})
def test_full_security_fails_at_first_check_loopback(_mock_settings):
    """Loopback check runs first; if it fails, later checks don't matter."""
    result = _check_security(
        _FullSecurityHandler,
        _ctx(
            remote_addr="10.0.0.1",
            authenticated=True,
            csrf_token="tok",
            client_csrf_token="tok",
            csrf_cookie="tok",
            api_key="key",
        ),
    )
    assert result is not None
    assert result["code"] == "FORBIDDEN"


# Forced password change


def test_a_pending_password_change_blocks_authenticated_socket_handlers():
    """helpers/api.py blocks every /api/* endpoint but six while a password
    change is pending. This transport had no such gate, so a client holding a
    valid session could drive socket handlers on a password it had been told to
    replace -- which on a SKU that ships a published default is the difference
    between "the default is safe because it must be changed" and "the default
    is a working credential until someone changes it".
    """
    result = _check_security(_AuthOnlyHandler, _ctx(must_change_password=True))
    assert result is not None
    assert result["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_a_cleared_password_change_lets_the_socket_through():
    assert _check_security(_AuthOnlyHandler, _ctx(must_change_password=False)) is None


def test_the_password_gate_does_not_apply_to_handlers_that_need_no_auth():
    """An unauthenticated handler has no user to have a pending change, and
    gating it would deny sockets on a property that cannot be evaluated."""
    assert _check_security(_OpenHandler, _ctx(must_change_password=True)) is None


# The handler name is the client's, and resolving it imports a file

def test_a_handler_name_cannot_leave_the_api_directories(tmp_path, monkeypatch):
    """`auth.handlers` is read from the connect payload before anything is known
    about the caller, and each name is imported. `../` in one reached any `.py`
    on disk, so a file uploaded to a working directory ran in the web server."""
    import asyncio
    import threading

    from flask import Flask
    from werkzeug.test import EnvironBuilder

    from helpers import cache, files, login, plugins, ws

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    # Signed in: an anonymous handshake is refused before any name is resolved.
    monkeypatch.setattr(login, "current_user", lambda: {"id": "u1", "role": "user"})
    marker = tmp_path / "ran"
    upload = tmp_path / "usr" / "users" / "u1" / "workdir"
    upload.mkdir(parents=True)
    (upload / "evil.py").write_text(
        f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8"
    )
    (tmp_path / "usr" / "api").mkdir()
    plugin_dir = tmp_path / "plugins" / "demo"
    (plugin_dir / "api").mkdir(parents=True)
    monkeypatch.setattr(
        plugins, "find_plugin_dir", lambda name: str(plugin_dir) if name == "demo" else None
    )

    registered = {}

    class _Server:
        def on(self, event, namespace=None):
            return lambda fn: registered.setdefault(event, fn)

    app = Flask("test_ws_handler_names", static_folder=None)
    app.secret_key = "test-secret"
    cache.clear(ws.CACHE_AREA)
    try:
        ws.register_ws_namespace(_Server(), app, threading.RLock())
        environ = EnvironBuilder(
            path="/socket.io/", headers={"Origin": "http://localhost"}
        ).get_environ()
        names = [
            "../users/u1/workdir/evil",
            "plugins/demo/../../../usr/users/u1/workdir/evil",
        ]
        assert asyncio.run(registered["connect"]("sid-1", environ, {"handlers": names}))
        assert not marker.exists()
    finally:
        cache.clear(ws.CACHE_AREA)
        ws._active_handlers.pop("sid-1", None)
        ws._ws_contexts.pop("sid-1", None)
