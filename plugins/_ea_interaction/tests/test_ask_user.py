"""The tool ends the turn, and a refused card does not.

Both halves matter. Ending the turn is what makes a question cheap -- nothing
blocks, nothing is held open. Not ending it on a validation failure is what stops
a malformed question leaving the user looking at a turn that finished with
nothing on screen.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugins._ea_interaction.tools.ask_user import AskUser


class FakeLog:
    def __init__(self):
        self.items: list[dict] = []

    def log(self, **kwargs):
        self.items.append(kwargs)
        return SimpleNamespace(update=lambda **_: None)


def run(args: dict, monkeypatch) -> tuple[object, FakeLog]:
    log = FakeLog()
    surface = SimpleNamespace(log=log, id="ctx-1")
    monkeypatch.setattr(
        "plugins._ea_interaction.tools.ask_user.user_facing_context", lambda _agent: surface
    )
    tool = AskUser.__new__(AskUser)
    tool.args = args
    tool.agent = SimpleNamespace(context=surface)
    return asyncio.run(tool.execute()), log


def test_a_question_ends_the_turn(monkeypatch):
    response, log = run(
        {"question": "Which switches?", "options": ["Core", "Access"]}, monkeypatch
    )
    assert response.break_loop is True
    assert len(log.items) == 1
    assert log.items[0]["kvps"]["interaction"] == "question"
    assert log.items[0]["heading"] == "Which switches?"


def test_the_history_records_that_the_question_was_put(monkeypatch):
    """Otherwise the next turn asks it again."""
    response, _ = run({"question": "Which?", "options": ["Core", "Access"]}, monkeypatch)
    assert "Which?" in response.message
    assert "Core" in response.message and "Access" in response.message


def test_a_refused_card_does_not_end_the_turn(monkeypatch):
    response, log = run({"question": "Proceed?", "options": ["Yes"]}, monkeypatch)
    assert response.break_loop is False
    assert log.items == [], "nothing should be shown for a question that was not asked"
    assert "not asked" in response.message


def test_context_becomes_the_card_body(monkeypatch):
    _, log = run(
        {"question": "Which?", "options": ["a", "b"], "context": "Three groups exist."},
        monkeypatch,
    )
    assert log.items[0]["content"] == "Three groups exist."


@pytest.mark.parametrize("value,expected", [(True, "true"), ("false", "false"), (None, "true")])
def test_allow_other_accepts_what_a_model_actually_sends(value, expected, monkeypatch):
    args = {"question": "Which?", "options": ["a", "b"]}
    if value is not None:
        args["allow_other"] = value
    _, log = run(args, monkeypatch)
    assert log.items[0]["kvps"]["allow_other"] == expected


# --- request_approval --------------------------------------------------------

def run_approval(args: dict, monkeypatch) -> tuple[object, FakeLog]:
    from plugins._ea_interaction.tools.request_approval import RequestApproval

    log = FakeLog()
    surface = SimpleNamespace(log=log, id="ctx-1")
    monkeypatch.setattr(
        "plugins._ea_interaction.tools.request_approval.user_facing_context",
        lambda _agent: surface,
    )
    tool = RequestApproval.__new__(RequestApproval)
    tool.args = args
    tool.agent = SimpleNamespace(context=surface)
    return asyncio.run(tool.execute()), log


def test_an_approval_ends_the_turn(monkeypatch):
    response, log = run_approval(
        {"prompt": "Restart the interface?", "consequence": "Port drops ~10s."}, monkeypatch
    )
    assert response.break_loop is True
    assert log.items[0]["kvps"]["interaction"] == "approval"
    assert log.items[0]["content"] == "Port drops ~10s."


def test_the_agent_is_told_not_to_read_silence_as_approval(monkeypatch):
    response, _ = run_approval({"prompt": "Restart it?"}, monkeypatch)
    assert "silence" in response.message.lower()


def test_a_refused_approval_does_not_end_the_turn(monkeypatch):
    response, log = run_approval({"prompt": "   "}, monkeypatch)
    assert response.break_loop is False
    assert log.items == []
    assert "not requested" in response.message


def test_the_approval_tool_cannot_be_handed_an_endpoint(monkeypatch):
    """Passing one is ignored rather than honoured: there is no parameter for it."""
    _, log = run_approval(
        {"prompt": "Approve?", "endpoint": "plugins/_ea_ansible/change_approve"}, monkeypatch
    )
    assert "endpoint" not in log.items[0]["kvps"]
