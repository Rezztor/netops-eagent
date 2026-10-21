"""Branching and compacting a conversation are for its owner.

Both handlers reached for `AgentContext.get`, which answers for any account's
conversation. Branching then handed the caller a full copy of it under their own
account, and compaction rewrote the owner's history.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from agent import AgentContext
from helpers.context_utils import ContextAccessDenied
from plugins._chat_branching.api.branch_chat import BranchChat
from plugins._chat_compaction.api.compact_chat import CompactChat


@pytest.mark.parametrize("handler_cls", [BranchChat, CompactChat])
def test_another_accounts_conversation_is_refused(monkeypatch, handler_cls):
    theirs = SimpleNamespace(is_owned_by=lambda user_id: False)
    monkeypatch.setattr(AgentContext, "get", staticmethod(lambda ctxid: theirs))

    handler = handler_cls(Flask("test_chat_plugin_ownership"), threading.Lock())
    with pytest.raises(ContextAccessDenied):
        asyncio.run(handler.process({"context": "theirs", "log_no": 1, "action": "stats"}, None))


@pytest.mark.parametrize("action", ["save", "delete", "duplicate"])
def test_writing_a_shared_command_takes_plugin_management(monkeypatch, action):
    """A script command is Python that `resolve` runs in the web server, and the
    endpoint was open to every signed-in account."""
    from helpers import login
    from plugins._commands.api.commands import Commands
    from plugins._commands.helpers import commands as commands_helper

    monkeypatch.setattr(login, "has_permission", lambda permission: False)
    called = []
    for name in ("save_command", "delete_command", "duplicate_command"):
        monkeypatch.setattr(commands_helper, name, lambda **kw: called.append(kw), raising=False)

    handler = Commands(Flask("test_commands_gate"), threading.Lock())
    response = asyncio.run(
        handler.process({"action": action, "name": "x", "command_type": "script", "body": "run = print"}, None)
    )

    assert response.status_code == 403
    assert called == []


def test_a_request_named_api_base_takes_settings_management(monkeypatch):
    """Both endpoints send the instance's provider key to the base they are given."""
    from helpers import login, rbac
    from plugins._model_config.api import model_override
    from plugins._model_config.api.model_search import ModelSearch

    assert ModelSearch.required_permission() == rbac.PERM_SETTINGS_MANAGE

    saved = []
    context = SimpleNamespace(
        is_owned_by=lambda user_id: True, root_agent=None, set_data=lambda *a: saved.append(a)
    )
    monkeypatch.setattr(model_override.ModelOverride, "use_context", lambda self, ctxid, **kw: context)
    monkeypatch.setattr(AgentContext, "get", staticmethod(lambda ctxid: context))
    monkeypatch.setattr(model_override.model_config, "is_chat_override_allowed", lambda agent: True)
    monkeypatch.setattr(login, "has_permission", lambda permission: False)

    handler = model_override.ModelOverride(Flask("test_model_override_gate"), threading.Lock())
    response = asyncio.run(
        handler.process(
            {"context_id": "c", "action": "set", "override": {"provider": "openai", "api_base": "http://elsewhere/v1"}},
            None,
        )
    )

    assert response.status_code == 403
    assert saved == []


@pytest.mark.parametrize("action", ["get", "generate", "save"])
@pytest.mark.parametrize("kind", ["chat", "task"])
def test_naming_another_accounts_conversation_is_refused(monkeypatch, action, kind):
    """`chat_name` read the title, had the utility model summarise the messages,
    and renamed the conversation or its scheduled task, for any id it was given."""
    from plugins._chat_naming.api.chat_name import ChatName

    theirs = SimpleNamespace(is_owned_by=lambda user_id: False, name="Quarterly losses", root_agent=None)
    monkeypatch.setattr(AgentContext, "get", staticmethod(lambda ctxid: theirs))

    handler = ChatName(Flask("test_chat_name_ownership"), threading.Lock())
    with pytest.raises(ContextAccessDenied):
        asyncio.run(handler.process({"action": action, "kind": kind, "item_id": "theirs", "name": "x"}, None))
    assert theirs.name == "Quarterly losses"


def _may_manage_plugins(monkeypatch, allowed):
    from helpers import login, rbac

    monkeypatch.setattr(login, "has_permission", lambda permission: allowed and permission == rbac.PERM_PLUGINS_MANAGE)


def test_the_profile_command_does_not_create_a_profile_for_an_ordinary_account(monkeypatch):
    """An agent profile is a system prompt and a tool policy that every account
    can load, which is why the profile editor and `subagents` take
    `plugins.manage`. `/profile "Title" "instructions"` called the editor's own
    save with no check, from an endpoint open to every signed-in account."""
    from plugins._agent_editor.helpers import editor
    from plugins._commands.commands import connector_commands

    saved = []
    monkeypatch.setattr(editor, "save_easy_profile", lambda *args, **kwargs: saved.append(args) or ("helpdesk", {}))
    monkeypatch.setattr(connector_commands.subagents, "get_available_agents_dict", lambda project: {})
    monkeypatch.setattr(connector_commands.projects, "get_context_project_name", lambda context: "")
    arguments = {"positional": ["Helpdesk", "Always", "do", "this", "first"]}
    context = SimpleNamespace(id="c1")

    _may_manage_plugins(monkeypatch, False)
    refused = connector_commands._handle_profile(context, '"Helpdesk" Always do this first', arguments)
    assert saved == []
    assert refused["effects"][0]["level"] == "error" and "administrator" in refused["effects"][0]["message"]

    _may_manage_plugins(monkeypatch, True)
    connector_commands._handle_profile(context, '"Helpdesk" Always do this first', arguments)
    assert len(saved) == 1


def test_asking_where_commands_live_does_not_make_a_project_folder(monkeypatch):
    """`scope_info` with `ensure_directory` ran `mkdir -p` under the projects
    folder for any name, for any account: a folder that then shows in every
    account's project list, and takes the name a real project would want."""
    from plugins._commands.api.commands import Commands
    from plugins._commands.helpers import commands as commands_helper

    asked = []
    monkeypatch.setattr(commands_helper, "get_context_scope", lambda context_id: {"project_name": ""})
    monkeypatch.setattr(
        commands_helper,
        "get_scope_payload",
        lambda project_name="", agent_profile="", ensure_directory=False: asked.append(ensure_directory) or {},
    )
    monkeypatch.setattr(commands_helper, "strip_private_scope", lambda scope: scope)
    handler = Commands(Flask("test_scope_info"), threading.Lock())
    request = {"action": "scope_info", "project_name": "phantom", "ensure_directory": True}

    _may_manage_plugins(monkeypatch, False)
    asyncio.run(handler.process(dict(request), None))
    _may_manage_plugins(monkeypatch, True)
    asyncio.run(handler.process(dict(request), None))

    assert asked == [False, True]
