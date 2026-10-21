"""/api/message: what it does with no context, a form post, and a failed turn.

Three findings from the 2026-10-18 UAT run: an empty `context` continued the
caller's previous chat (F30), a form post answered 500 with Werkzeug's 415 text
in the body (F26), and a runner failure reached the caller as a 500 whose body
named a slot and a path inside the server (F29).
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from flask import Flask

from agent import AgentConfig, AgentContext
from helpers import errors, user_scope


class _Done:
    async def result(self):
        return "ok"


class _Failed:
    def __init__(self, exc: Exception):
        self.exc = exc

    async def result(self):
        raise self.exc


def _handler():
    from api.message import Message

    return Message(Flask("message-endpoint-test"), threading.RLock())


def _post(handler, **kwargs):
    from flask import request

    with handler.app.test_request_context("/api/message", method="POST", **kwargs):
        return asyncio.run(handler.process({}, request))


@pytest.fixture
def quiet_send(monkeypatch):
    from helpers import message_queue

    monkeypatch.setattr(message_queue, "log_user_message", lambda *a, **k: None)
    monkeypatch.setattr(AgentContext, "communicate", lambda self, msg: _Done())


def test_no_context_starts_a_new_chat(database, quiet_send):
    AgentContext.remove("ctx-earlier")
    with user_scope.user_scope("alice-id"):
        AgentContext(config=AgentConfig(mcp_servers="", profile="default"), id="ctx-earlier", set_current=False)
        output = None
        try:
            output = _post(_handler(), json={"text": "hello", "context": ""})
            assert output["context"] != "ctx-earlier"
            assert AgentContext.get(output["context"]).is_owned_by("alice-id")
        finally:
            AgentContext.remove("ctx-earlier")
            if isinstance(output, dict):
                AgentContext.remove(output["context"])


def test_a_form_post_is_415_not_500():
    response = _post(_handler(), data={"text": "hello"})
    assert response.status_code == 415
    assert "JSON" in json.loads(response.get_data(as_text=True))["error"]


def test_a_failed_turn_answers_one_plain_sentence(database, quiet_send, monkeypatch):
    class RunnerUnavailable(RuntimeError):
        pass

    leak = "cannot register a key for eagent-001: [Errno 13] Permission denied: '/srv/keys/eagent-001/.tmp'"
    monkeypatch.setattr(
        AgentContext, "communicate",
        lambda self, msg: _Failed(errors.HandledException(RunnerUnavailable(leak))),
    )
    with user_scope.user_scope("alice-id"):
        response = _post(_handler(), json={"text": "run something", "context": "ctx-fails"})
    try:
        body = response.get_data(as_text=True)
        assert response.status_code != 500
        assert "/srv/keys" not in body and "eagent-001" not in body
        assert json.loads(body)["error"] == errors.user_message(RunnerUnavailable(leak))
        assert "runner" in json.loads(body)["error"]
    finally:
        AgentContext.remove("ctx-fails")


def test_the_chat_log_gets_the_sentence_and_only_an_admin_gets_the_traceback(monkeypatch):
    from helpers.log import Log
    from helpers import tool_permissions

    class RunnerUnavailable(RuntimeError):
        pass

    def failed():
        try:
            raise RunnerUnavailable("cannot register a key for eagent-001: /srv/keys")
        except RunnerUnavailable as exc:
            return exc

    class _Context:
        owner_id = "someone"

        def __init__(self):
            self.log = Log()

    for role, sees_details in (("user", False), ("power", False), ("admin", True)):
        monkeypatch.setattr(tool_permissions, "role_for_context", lambda context, role=role: role)
        context = _Context()
        item = errors.log_failure(context, failed())
        assert "/srv/keys" not in item.content and "Traceback" not in item.content
        assert item.content == errors.user_message(RunnerUnavailable())
        assert bool(item.kvps and "/srv/keys" in item.kvps.get("details", "")) is sees_details
