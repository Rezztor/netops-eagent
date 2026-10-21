"""The chat commands that list conversations list the caller's own.

`/chats` and `/sessions` were built from `AgentContext.all()`, which is every
loaded conversation of every account, so any signed-in account could read the
titles and ids of the others' -- and `/sessions` answers the external API and
inbound mail as well. `/chat <id>` said whether an id existed.
"""

import pytest

from agent import AgentConfig, AgentContext
from helpers import integration_commands, user_scope
from plugins._commands.commands import connector_commands


@pytest.fixture
def chats():
    made = [
        AgentContext(config=AgentConfig(mcp_servers="", profile=""), id=ctxid, name=name, owner_id=owner, set_current=False)
        for ctxid, name, owner in (
            ("mine-1", "My notes", "alice"),
            ("theirs-1", "Quarterly losses", "bob"),
        )
    ]
    yield made
    for context in made:
        AgentContext.remove(context.id)


def test_chats_lists_only_the_callers_conversations(chats):
    with user_scope.user_scope("alice"):
        listing = connector_commands._chat_list(chats[0], {})
    assert "My notes" in listing
    assert "Quarterly losses" not in listing and "theirs-1" not in listing


def test_chat_answers_the_same_for_another_accounts_id_as_for_none(chats):
    with user_scope.user_scope("alice"):
        theirs = connector_commands._handle_chat({"positional": ["theirs-1"]})
        missing = connector_commands._handle_chat({"positional": ["no-such-id"]})
        mine = connector_commands._handle_chat({"positional": ["mine-1"]})
    assert [effect["type"] for effect in theirs["effects"]] == ["toast"]
    assert theirs["effects"][0]["level"] == missing["effects"][0]["level"] == "error"
    assert any(effect["type"] == "select_chat" for effect in mine["effects"])


def test_sessions_lists_only_the_owner_of_the_conversation_it_was_asked_in(chats):
    listing = integration_commands._handle_sessions(chats[0])
    assert "My notes" in listing
    assert "Quarterly losses" not in listing


def test_sessions_in_a_conversation_nobody_owns_lists_only_itself(chats):
    """The external API and inbound mail run in conversations with no owner."""
    unowned = AgentContext(config=AgentConfig(mcp_servers="", profile=""), id="api-1", name="From the API", set_current=False)
    unowned.owner_id = None
    try:
        listing = integration_commands._handle_sessions(unowned)
    finally:
        AgentContext.remove("api-1")
    assert "From the API" in listing
    assert "Quarterly losses" not in listing and "My notes" not in listing
