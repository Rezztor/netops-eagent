"""A permission means the same thing on the socket as it does over HTTP.

`_check_security` is a careful port of the HTTP checks -- loopback,
authentication, the forced password change, CSRF, the API key -- and its own
comment says why the password gate had to exist on both transports: "must
change" has to mean the same thing on both or it means neither.

Permissions were the one check that did not make the crossing. Nothing shipped
was bypassed, because no WebSocket handler needed a gate. The problem was the
shape of the failure: `WsHandler` had no `required_permission`, and
`_check_security` consulted neither it nor `helpers/api_policy`, so a handler
that declared one would have been given no error and no enforcement. A gate that
is silently ignored is worse than one that is missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from helpers import rbac, ws  # noqa: E402


#: The accounts the contexts below belong to, as the store would answer for them.
#: `_check_security` reads the account again on every message.
_ACCOUNTS: dict = {}


@pytest.fixture(autouse=True)
def account_store(monkeypatch):
    from helpers import users

    _ACCOUNTS.clear()
    monkeypatch.setattr(users, "get_user_by_id", _ACCOUNTS.get)


def context(role: str | None, **overrides):
    base = dict(
        authenticated=True,
        user_id="someone",
        csrf_token=None,
        client_csrf_token=None,
        csrf_cookie=None,
        remote_addr="127.0.0.1",
        api_key=None,
        must_change_password=False,
        role=role,
    )
    base.update(overrides)
    if base["user_id"]:
        _ACCOUNTS[base["user_id"]] = {
            "id": base["user_id"],
            "role": base["role"],
            "disabled": False,
            "must_change_password": base["must_change_password"],
        }
    return ws._SecurityContext(**base)


class _Open(ws.WsHandler):
    @classmethod
    def requires_csrf(cls) -> bool:
        return False


class _NeedsSettings(_Open):
    @classmethod
    def required_permission(cls) -> str | None:
        return rbac.PERM_SETTINGS_MANAGE


def test_a_handler_declaring_a_permission_is_enforced():
    denied = ws._check_security(_NeedsSettings, context(rbac.ROLE_USER))

    assert denied is not None, "a user-role account must not reach a settings handler"
    assert denied["code"] == "FORBIDDEN"
    assert rbac.PERM_SETTINGS_MANAGE in denied["error"]


def test_the_role_that_holds_it_is_allowed():
    assert ws._check_security(_NeedsSettings, context(rbac.ROLE_ADMIN)) is None


def test_a_handler_declaring_nothing_is_open_to_any_signed_in_account():
    assert ws._check_security(_Open, context(rbac.ROLE_USER)) is None


def test_the_permission_is_checked_behind_authentication():
    """A permission without an identity is meaningless, and answering FORBIDDEN
    to an anonymous caller would leak that the handler exists."""
    denied = ws._check_security(_NeedsSettings, context(None, authenticated=False, user_id=None))

    assert denied is not None
    assert denied["code"] == "AUTH_REQUIRED"


def test_a_forced_password_change_still_wins():
    """Order matters: an account told to change its password must not be able to
    do anything else first, whatever its role."""
    denied = ws._check_security(
        _NeedsSettings, context(rbac.ROLE_ADMIN, must_change_password=True)
    )

    assert denied is not None
    assert denied["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_the_policy_table_gates_socket_handlers_by_the_same_name():
    """Handlers load from the same api/ directories as ApiHandler, so a name in
    helpers/api_policy.py should gate either transport."""
    from helpers import api_policy

    class SettingsGet(_Open):
        pass

    SettingsGet._dispatch_path = "settings_get"  # type: ignore[attr-defined]
    assert api_policy.required_permission_for("settings_get") == rbac.PERM_SETTINGS_MANAGE

    denied = ws._check_security(SettingsGet, context(rbac.ROLE_USER))
    assert denied is not None and denied["code"] == "FORBIDDEN"

    assert ws._check_security(SettingsGet, context(rbac.ROLE_ADMIN)) is None


# --- the account, read again on every message --------------------------------
#
# Over HTTP the account is read from the store on every request, so disabling
# it, deleting it, demoting it or changing its password takes effect at once.
# The socket copied the account once, at connect, and trusted the copy for as
# long as the connection stayed open -- which a browser tab keeps it for hours.


def _as_it_is_now(monkeypatch, account):
    from helpers import users

    monkeypatch.setattr(users, "get_user_by_id", lambda user_id: account)


def test_a_socket_stops_working_when_its_account_is_disabled(monkeypatch):
    _as_it_is_now(monkeypatch, {"id": "someone", "role": rbac.ROLE_POWER, "disabled": True})
    denied = ws._check_security(_Open, context(rbac.ROLE_POWER))
    assert denied is not None and denied["code"] == "AUTH_REQUIRED"


def test_a_socket_stops_working_when_its_account_is_deleted(monkeypatch):
    _as_it_is_now(monkeypatch, None)
    denied = ws._check_security(_Open, context(rbac.ROLE_POWER))
    assert denied is not None and denied["code"] == "AUTH_REQUIRED"


def test_a_demotion_reaches_a_socket_that_is_already_open(monkeypatch):
    _as_it_is_now(monkeypatch, {"id": "someone", "role": rbac.ROLE_USER, "disabled": False})
    denied = ws._check_security(_NeedsSettings, context(rbac.ROLE_ADMIN))
    assert denied is not None and denied["code"] == "FORBIDDEN"


def test_a_password_change_ends_a_socket_opened_under_the_old_one(monkeypatch):
    from helpers import login

    before = {"id": "someone", "role": rbac.ROLE_USER, "disabled": False, "password_hash": "old-hash"}
    after = {**before, "password_hash": "new-hash"}
    opened = context(rbac.ROLE_USER, password_stamp=login._password_stamp(before))

    _as_it_is_now(monkeypatch, before)
    assert ws._check_security(_Open, opened) is None

    _as_it_is_now(monkeypatch, after)
    denied = ws._check_security(_Open, opened)
    assert denied is not None and denied["code"] == "AUTH_REQUIRED"


def test_a_forced_password_change_is_read_fresh_too(monkeypatch):
    """Set after the socket opened, it blocks; cleared, it lets the socket on without a reconnect."""
    account = {"id": "someone", "role": rbac.ROLE_USER, "disabled": False, "must_change_password": True}
    _as_it_is_now(monkeypatch, account)
    denied = ws._check_security(_Open, context(rbac.ROLE_USER))
    assert denied is not None and denied["code"] == "PASSWORD_CHANGE_REQUIRED"

    stale = context(rbac.ROLE_USER, must_change_password=True)  # what the socket saw at connect
    _as_it_is_now(monkeypatch, {**account, "must_change_password": False})
    assert ws._check_security(_Open, stale) is None


def test_an_account_that_cannot_be_looked_up_is_refused(monkeypatch):
    from helpers import users

    def unavailable(user_id):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(users, "get_user_by_id", unavailable)
    denied = ws._check_security(_Open, context(rbac.ROLE_USER))
    assert denied is not None and denied["code"] == "AUTH_REQUIRED"


def test_a_socket_idle_past_the_session_limit_is_refused_and_listed_for_closing(monkeypatch):
    """The HTTP session ends after IDLE_HOURS unused; a socket left open in a
    tab went on being served, and pushed to, for as long as the tab stayed."""
    import time

    from helpers import login

    fresh = context(rbac.ROLE_ADMIN)
    stale = context(rbac.ROLE_ADMIN, last_seen=time.time() - login.IDLE_HOURS * 3600 - 1)

    assert ws._check_security(_Open, fresh) is None
    assert ws._check_security(_Open, stale) == {"code": "AUTH_REQUIRED", "error": "Authentication required"}

    monkeypatch.setattr(ws, "_ws_contexts", {"fresh": fresh, "stale": stale})
    assert ws.idle_sids() == ["stale"]

    closed = []
    from extensions.python.job_loop._40_close_idle_sockets import CloseIdleSockets
    from helpers import ws_manager

    monkeypatch.setattr(
        ws_manager, "get_shared_ws_manager",
        lambda: type("M", (), {"disconnect_sids": lambda self, ids: closed.extend(ids)})(),
    )
    CloseIdleSockets(agent=None).execute()
    assert closed == [(ws.NAMESPACE, "stale")]


def test_a_message_on_the_socket_is_activity_and_an_idle_one_ends_it(monkeypatch):
    import asyncio
    import threading
    import time

    from flask import Flask

    from helpers import login

    registered, closed = {}, []

    class _Server:
        def on(self, event, namespace=None):
            return lambda fn: registered.setdefault(event, fn)

        async def disconnect(self, sid, namespace=None):
            closed.append(sid)

    class _Echo(_Open):
        async def process(self, event, data, sid):
            return {"ok": True}

    ws.register_ws_namespace(_Server(), Flask("test_ws_idle"), threading.RLock())
    limit = login.IDLE_HOURS * 3600
    active = context(rbac.ROLE_USER, last_seen=time.time() - limit + 60)
    idle = context(rbac.ROLE_USER, last_seen=time.time() - limit - 1)
    handler = _Echo(None, threading.RLock())  # type: ignore[arg-type]
    monkeypatch.setattr(ws, "_ws_contexts", {"active": active, "idle": idle})
    monkeypatch.setattr(ws, "_active_handlers", {"active": {"echo": handler}, "idle": {"echo": handler}})

    asyncio.run(registered["*"]("evt", "active", {}))
    assert time.time() - active.last_seen < 5, "a message counts as activity"

    refused = asyncio.run(registered["*"]("evt", "idle", {}))
    assert closed == ["idle"]
    assert "AUTH_REQUIRED" in str(refused)
