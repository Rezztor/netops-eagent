"""A tool dispatched through `parallel` still has to reach the user.

`_run_direct_tool_job` builds a background context per job and deletes it in a
`finally`. `execute_tool_call` redirects the tool's own log item to the parent,
so a tool that only returns text never notices. One that shows the user
something -- a download control, a file filed against a conversation -- writes
it into the worker and loses it, and loses it silently.
"""

import pytest

from agent import AgentContext
from helpers.parallel_tools import (
    PARALLEL_WORKER_PARENT_CONTEXT_KEY,
    user_facing_context,
)


class _Agent:
    def __init__(self, context):
        self.context = context


@pytest.fixture
def contexts():
    from agent import AgentConfig

    made = []
    counter = [0]

    def make(**data):
        counter[0] += 1
        ctx_id = f"ctx-ufc-{counter[0]}"
        AgentContext.remove(ctx_id)
        ctx = AgentContext(
            AgentConfig(mcp_servers="", profile="primary"),
            id=ctx_id,
            set_current=False,
        )
        for key, value in data.items():
            ctx.set_data(key, value)
        made.append(ctx)
        return ctx

    yield make
    for ctx in made:
        try:
            AgentContext.remove(ctx.id)
        except Exception:
            pass


def test_an_ordinary_tool_call_uses_its_own_context(contexts):
    ctx = contexts()
    assert user_facing_context(_Agent(ctx)) is ctx


def test_a_parallel_worker_resolves_to_the_conversation(contexts):
    parent = contexts()
    worker = contexts(**{PARALLEL_WORKER_PARENT_CONTEXT_KEY: parent.id})
    assert user_facing_context(_Agent(worker)) is parent


def test_it_walks_more_than_one_level(contexts):
    parent = contexts()
    middle = contexts(**{PARALLEL_WORKER_PARENT_CONTEXT_KEY: parent.id})
    worker = contexts(**{PARALLEL_WORKER_PARENT_CONTEXT_KEY: middle.id})
    assert user_facing_context(_Agent(worker)) is parent


def test_a_vanished_parent_does_not_raise(contexts):
    """Losing a download is bad. Turning it into a crash is worse."""
    worker = contexts(**{PARALLEL_WORKER_PARENT_CONTEXT_KEY: "gone-forever"})
    assert user_facing_context(_Agent(worker)) is worker


def test_a_cycle_terminates(contexts):
    a = contexts()
    b = contexts(**{PARALLEL_WORKER_PARENT_CONTEXT_KEY: a.id})
    a.set_data(PARALLEL_WORKER_PARENT_CONTEXT_KEY, b.id)
    assert user_facing_context(_Agent(b)) in (a, b)


def test_the_resolved_context_is_the_one_that_survives_the_job(contexts):
    """The worker is deleted in a finally; the parent is what the user reloads."""
    parent = contexts()
    worker = contexts(**{PARALLEL_WORKER_PARENT_CONTEXT_KEY: parent.id})
    surface = user_facing_context(_Agent(worker))
    AgentContext.remove(worker.id)
    assert AgentContext.get(surface.id) is parent
