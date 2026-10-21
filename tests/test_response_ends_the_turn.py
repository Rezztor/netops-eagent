"""Responding to the user ends the turn, and nothing may quietly change that.

ADR 0031 removed `plugins/_goal` because its `tools/response.py` overrode the
core response tool and set `break_loop = False`, cancelling the end of the turn
and instructing the model to continue on its own. The decision is only worth
recording if reimplementing it is noticed, so this is the check.

The mechanism is specific and easy to repeat by accident: a plugin ships
`tools/response.py`, the loader prefers it over the core one, and every
conversation silently stops ending. Nothing else in the tool protocol has that
property -- an ordinary tool returning `break_loop=False` is normal and correct,
which is why this asserts on the override rather than on the value.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_no_plugin_overrides_the_response_tool():
    overrides = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in REPO_ROOT.glob("plugins/*/tools/response.py")
    )
    assert not overrides, (
        "A plugin is overriding the core response tool: "
        + ", ".join(overrides)
        + ". ADR 0031: responding always ends the turn."
    )


def test_the_core_response_tool_breaks_the_loop():
    from tools.response import ResponseTool

    tool = ResponseTool.__new__(ResponseTool)
    tool.args = {"text": "Here is what I found."}

    response = asyncio.run(tool.execute())
    assert response.break_loop is True
    assert response.message == "Here is what I found."


def test_the_core_response_tool_refuses_an_empty_answer():
    from helpers.errors import RepairableException
    from tools.response import ResponseTool

    tool = ResponseTool.__new__(ResponseTool)
    tool.args = {"text": "   "}

    with pytest.raises(RepairableException):
        asyncio.run(tool.execute())


def test_the_goal_plugin_is_gone():
    """Belt and braces: no goal code, not merely no goal directory.

    Asserting the directory is absent looked equivalent and is not. Git removes
    tracked files but leaves a directory that still holds untracked ones, so
    checking out a commit from before the removal and back again leaves
    `plugins/_goal/__pycache__` behind -- and the directory exists with no code
    in it. Found by running this suite at each commit of the branch in a
    worktree, where the checkouts happen back to back.
    """
    goal = REPO_ROOT / "plugins" / "_goal"
    leftovers = sorted(str(p.relative_to(REPO_ROOT)) for p in goal.rglob("*.py"))
    leftovers += [str(p.relative_to(REPO_ROOT)) for p in goal.glob("plugin.yaml")]
    assert not leftovers, "goal plugin code is still present: " + ", ".join(leftovers)


# --- an answer knows which question it answered -------------------------------
#
# Decision C1 in the chat interaction plan: a clicked answer sends the label,
# plus the card's id on the user message. The label is what the agent reads and
# what the transcript shows; the id is what ties the reply to the question in the
# record, so a transcript read later does not have to infer it from adjacency.

def test_a_clicked_answer_records_which_card_it_answered():
    from helpers import message_queue

    logged: dict = {}

    class FakeLog:
        def log(self, **kwargs):
            logged.update(kwargs)

    context = type("Ctx", (), {"log": FakeLog()})()
    message_queue.log_user_message(context, "Approve", [], "mid", answers="d_abc123")

    assert logged["type"] == "user"
    assert logged["content"] == "Approve"
    assert logged["kvps"]["answers"] == "d_abc123"


def test_a_typed_message_carries_no_card_id():
    """Absent rather than empty. A key that is always present but usually blank
    reads as a card that was answered with nothing."""
    from helpers import message_queue

    logged: dict = {}

    class FakeLog:
        def log(self, **kwargs):
            logged.update(kwargs)

    context = type("Ctx", (), {"log": FakeLog()})()
    message_queue.log_user_message(context, "hello", [], "mid")

    assert "answers" not in logged["kvps"]
