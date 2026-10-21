from __future__ import annotations

import pytest

import importlib
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_fasta2a_server(monkeypatch):
    settings_stub = types.ModuleType("helpers.settings")
    settings_stub.get_settings = lambda: {
        "a2a_server_enabled": True,
        "mcp_server_token": "test-token",
    }
    monkeypatch.setitem(sys.modules, "helpers.settings", settings_stub)

    projects_stub = types.ModuleType("helpers.projects")
    projects_stub.activate_project = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "helpers.projects", projects_stub)

    print_style_stub = types.ModuleType("helpers.print_style")

    class _PrintStyle:
        def __init__(self, *args, **kwargs):
            pass

        def print(self, *args, **kwargs):
            pass

    print_style_stub.PrintStyle = _PrintStyle
    monkeypatch.setitem(sys.modules, "helpers.print_style", print_style_stub)

    starlette_stub = types.ModuleType("starlette")
    starlette_responses_stub = types.ModuleType("starlette.responses")

    class _Response:
        def __init__(self, content=b"", media_type=None, *args, **kwargs):
            self.body = content if isinstance(content, bytes) else str(content).encode()
            self.media_type = media_type

    starlette_responses_stub.Response = _Response
    starlette_requests_stub = types.ModuleType("starlette.requests")
    starlette_requests_stub.Request = object
    monkeypatch.setitem(sys.modules, "starlette", starlette_stub)
    monkeypatch.setitem(sys.modules, "starlette.responses", starlette_responses_stub)
    monkeypatch.setitem(sys.modules, "starlette.requests", starlette_requests_stub)

    agent_stub = types.ModuleType("agent")
    agent_stub.AgentContext = type(
        "AgentContext",
        (),
        {"remove": staticmethod(lambda *args, **kwargs: None)},
    )
    agent_stub.UserMessage = lambda **kwargs: types.SimpleNamespace(**kwargs)
    agent_stub.AgentContextType = types.SimpleNamespace(BACKGROUND="background")
    monkeypatch.setitem(sys.modules, "agent", agent_stub)

    initialize_stub = types.ModuleType("initialize")
    initialize_stub.initialize_agent = lambda: {}
    monkeypatch.setitem(sys.modules, "initialize", initialize_stub)

    persist_chat_stub = types.ModuleType("helpers.persist_chat")
    persist_chat_stub.remove_chat = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "helpers.persist_chat", persist_chat_stub)

    sys.modules.pop("helpers.fasta2a_server", None)
    return importlib.import_module("helpers.fasta2a_server")


def test_a2a_agent_card_streaming_capability_is_enabled_by_default(monkeypatch):
    module = _load_fasta2a_server(monkeypatch)

    updated = module._enable_streaming_capability(
        b'{"name":"Rezztor EAgent","capabilities":{"streaming":false,"pushNotifications":false}}'
    )

    agent_card = json.loads(updated)
    assert agent_card["capabilities"]["streaming"] is True
    assert agent_card["capabilities"]["pushNotifications"] is False


def test_a2a_agent_card_streaming_capability_creates_missing_block(monkeypatch):
    module = _load_fasta2a_server(monkeypatch)

    updated = module._enable_streaming_capability(b'{"name":"Rezztor EAgent"}')

    assert json.loads(updated)["capabilities"] == {"streaming": True}


def test_a2a_proxy_uses_streaming_enabled_fast_a2a_wrapper(monkeypatch):
    module = _load_fasta2a_server(monkeypatch)
    proxy = object.__new__(module.DynamicA2AProxy)

    proxy._configure()

    assert isinstance(proxy.app, module.RezztorEAgentFastA2A)


# --- who the A2A endpoint answers ---------------------------------------------


class _Request:
    """What the proxy reads of a request: its headers and its query string."""

    class _Headers(dict):
        def get(self, name, default=None):  # header names do not depend on case
            return super().get(name.lower(), default)

    def __init__(self, scope, receive=None):
        self.headers = self._Headers({k.decode().lower(): v.decode() for k, v in scope.get("headers", [])})
        self.query_params = dict(scope.get("query", {}))


def _ask(module, monkeypatch, token, path, headers=()):
    """The status the proxy answers, or "passed on" when the request reached the application."""
    import asyncio

    import helpers

    # Every object the proxy may reach as `settings`: the stub this file installs
    # in sys.modules, and the real module when another test file imported it
    # first -- `from helpers import settings` then answers with the package's
    # own attribute, not with the stub.
    for settings in {module.settings, sys.modules["helpers.settings"], getattr(helpers, "settings", module.settings)}:
        monkeypatch.setattr(
            settings, "get_settings", lambda: {"a2a_server_enabled": True, "mcp_server_token": token}
        )
    monkeypatch.setattr(module, "Request", _Request, raising=False)
    monkeypatch.setattr(module, "FASTA2A_AVAILABLE", True)  # the library is stubbed out here
    monkeypatch.setitem(sys.modules["starlette.requests"].__dict__, "Request", _Request)
    reached, sent = [], []

    async def application(scope, receive, send):
        reached.append(scope["path"])

    async def send(message):
        sent.append(message)

    proxy = object.__new__(module.DynamicA2AProxy)
    proxy.app, proxy._startup_done, proxy._reconfigure_needed = application, True, False
    proxy._lock = __import__("threading").RLock()
    scope = {"type": "http", "path": path, "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
    asyncio.run(proxy(scope, None, send))
    return "passed on" if reached else sent[0]["status"]


def test_a2a_takes_the_token_in_the_path_or_in_a_header_and_nothing_else(monkeypatch):
    module = _load_fasta2a_server(monkeypatch)

    assert _ask(module, monkeypatch, "test-token", "/a2a/t-test-token/") == "passed on"
    assert _ask(module, monkeypatch, "test-token", "/a2a/t-wrong/") == 401
    assert _ask(module, monkeypatch, "test-token", "/a2a/", [("X-API-KEY", "test-token")]) == "passed on"
    assert _ask(module, monkeypatch, "test-token", "/a2a/", [("Authorization", "Bearer test-token")]) == "passed on"
    assert _ask(module, monkeypatch, "test-token", "/a2a/", [("X-API-KEY", "wrong")]) == 401
    assert _ask(module, monkeypatch, "test-token", "/a2a/") == 401


@pytest.mark.parametrize("path", ["/a2a/t-anything/", "/a2a/t-/", "/a2a/"])
def test_a2a_with_no_token_configured_answers_nobody(monkeypatch, path):
    """With an empty token the check was skipped and the request passed on:
    the comparison was `if expected and given != expected`, and the header
    branch printed "No expected token found in settings" and carried on.
    Authentication fails closed everywhere else in this product (ADR 0004)."""
    module = _load_fasta2a_server(monkeypatch)

    assert _ask(module, monkeypatch, "", path) == 401
    assert _ask(module, monkeypatch, "", path, [("X-API-KEY", "")]) == 401
