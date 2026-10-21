"""A password typed into chat must not reach the log, history or the model."""

import asyncio
from types import SimpleNamespace

import pytest

from agent import UserMessage
from api.message import Message

SECRET = "Zq9-fake-Pass-1234"


@pytest.fixture(autouse=True)
def _no_project(monkeypatch):
    from helpers import projects

    monkeypatch.setattr(projects, "get_context_project_name", lambda ctx: "")


def _send(text: str):
    """Run text through the real message API with the real user_message_ui hooks."""
    logs, sent = [], []
    agent = SimpleNamespace(config=SimpleNamespace(profile=""), context=SimpleNamespace(get_data=lambda key: None, log=SimpleNamespace(log=lambda **kw: logs.append(kw))))
    context = SimpleNamespace(
        get_agent=lambda: agent,
        log=agent.context.log,
        communicate=lambda m: sent.append(m) or "task",
    )
    handler = Message.__new__(Message)
    handler.use_context = lambda ctxid: context
    request = SimpleNamespace(content_type="application/json", get_json=lambda: {"text": text, "context": "c"})
    asyncio.run(handler.communicate({}, request))
    return logs, sent


@pytest.mark.parametrize("text", [
    f"the password is {SECRET}",
    f"pw: {SECRET}",
    f"admin password = '{SECRET}' please",
    f"the snmp community is {SECRET}",
    f"enable secret 0 {SECRET}",
    f"neighbor 10.0.0.1 password {SECRET}",
    f"my token: {SECRET}",
    "key -----BEGIN RSA PRIVATE KEY-----\nMIIBabc\n-----END RSA PRIVATE KEY----- ok",
    "aws AKIAABCDEFGHIJKLMNOP",
    f"Authorization: Bearer {SECRET}",
    f"The enable password for lab-sonic is {SECRET}.",
    f"the snmp community on core-1 is {SECRET}",
    f"password of admin: {SECRET}",
])
def test_typed_secret_never_reaches_log_or_model(text):
    logs, sent = _send(text)
    user = [l for l in logs if l.get("type") == "user"]
    for seen in [sent[0].message] + [l["content"] for l in user]:
        assert SECRET not in seen and "MIIBabc" not in seen and "AKIAABCDEFGHIJKLMNOP" not in seen
        assert "[REDACTED:" in seen
    assert any(l.get("type") == "warning" for l in logs)


@pytest.mark.parametrize("text", [
    "please reset my password",
    "what is the secret",
    "the token is required for login",
    "my password is wrong",
    "show interfaces on lab-sonic",
    "the community string is not set",
    "password",
    "the password for admin is wrong",
    "the password for the switch is required",
])
def test_ordinary_sentences_pass_untouched(text):
    logs, sent = _send(text)
    assert sent[0].message == text
    assert not any(l.get("type") == "warning" for l in logs)


def test_a_broken_guard_lets_the_message_through(monkeypatch):
    import plugins._ea_inventory.helpers.redact as redact

    def boom(_):
        raise RuntimeError("x")

    monkeypatch.setattr(redact, "redact_text", boom)
    _, sent = _send(f"pw: {SECRET}")
    assert isinstance(sent[0], UserMessage) and SECRET in sent[0].message
