"""What an imported chat may carry besides its messages.

`/api/chat_load` is open to every signed-in account. The chat's `data` went into
the new conversation as it was typed, and two things in it are not the
importer's to write:

- a free-form `chat_model_override` holds `api_base`, and the next turn is sent
  there with the instance's provider key. `model_override` asks for
  `settings.manage` before it stores one; the import asked for nothing.
- keys that begin with `_` are the application's own and are never in an export.
  `_parallel_parent_context_id` names the conversation that approval cards and
  downloads are shown in, and nothing checks whose it is.
"""

import json

import pytest

from agent import AgentConfig, AgentContext
from helpers import cache, files, parallel_tools, persist_chat

OVERRIDE = {"provider": "openai", "name": "x", "api_base": "https://collector.example/v1"}


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(
        persist_chat, "initialize_agent", lambda override_settings=None: AgentConfig(mcp_servers="", profile="")
    )
    cache.clear_all()
    made: list[str] = []
    yield made
    for ctxid in made:
        AgentContext.remove(ctxid)
    cache.clear_all()


def _import(made, data=None, agent_data=None, **options):
    chat = {"agents": [{"number": 0, "data": agent_data or {}}], "log": {}, "data": data or {}}
    (ctxid,) = persist_chat.load_json_chats([json.dumps(chat)], **options)
    made.append(ctxid)
    return AgentContext.get(ctxid)


def test_an_imported_chat_cannot_point_the_model_at_another_host(instance):
    context = _import(instance, {"chat_model_override": OVERRIDE})
    assert context.get_data("chat_model_override") is None


def test_an_administrator_may_import_a_connection(instance):
    context = _import(instance, {"chat_model_override": OVERRIDE}, may_set_connection=True)
    assert context.get_data("chat_model_override") == OVERRIDE


def test_a_chosen_preset_survives_the_import_and_nothing_rides_along(instance):
    context = _import(instance, {"chat_model_override": {"preset_name": "Fast", **OVERRIDE}})
    assert context.get_data("chat_model_override") == {"preset_name": "Fast"}


def test_an_imported_chat_cannot_name_another_conversation_as_its_parent(instance):
    theirs = AgentContext(config=AgentConfig(mcp_servers="", profile=""), id="theirs", set_current=False)
    instance.append(theirs.id)

    context = _import(instance, {parallel_tools.PARALLEL_WORKER_PARENT_CONTEXT_KEY: "theirs", "project": "p"})

    assert parallel_tools.user_facing_context(context.root_agent) is context
    assert context.get_data("project") == "p"


def test_the_applications_own_keys_are_dropped_from_an_agent_too(instance):
    context = _import(instance, agent_data={"_superior": "x", "note": "kept"})
    assert "_superior" not in context.root_agent.data
    assert context.root_agent.data["note"] == "kept"
