"""A chat that is imported names its agent profile, and the name is a folder.

`/api/chat_load` is open to every signed-in account and takes the chat as JSON.
The profile in it went straight to `subagents.get_paths`, which joins it under
`usr/agents/` and imports every Python file in `<profile>/extensions/python/`.
A name of `../workdir/<own id>/p` is the caller's own upload folder, so the
lowest role could run Python in the web server.
"""

import json

import pytest

from agent import AgentConfig, AgentContext
from helpers import cache, files, persist_chat, subagents

PLANTED = """from helpers.extension import Extension


class Planted(Extension):
    def execute(self, **kwargs):
        open({marker!r}, "w").write("ran")
"""


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(
        persist_chat,
        "initialize_agent",
        lambda override_settings=None: AgentConfig(
            mcp_servers="", profile=(override_settings or {}).get("agent_profile", "primary")
        ),
    )
    # Both exist on a real instance, and `..` only resolves through a folder that does.
    (tmp_path / "agents").mkdir()
    (tmp_path / "usr" / "agents").mkdir(parents=True)
    cache.clear_all()
    yield tmp_path
    cache.clear_all()


def _plant(root, marker):
    folder = root / "usr" / "workdir" / "u1" / "p" / "extensions" / "python" / "agent_init"
    folder.mkdir(parents=True)
    (folder / "_10_planted.py").write_text(PLANTED.format(marker=str(marker)), encoding="utf-8")


@pytest.mark.parametrize("where", ["context", "agent"])
def test_an_imported_chat_cannot_name_a_folder_as_its_profile(instance, where):
    marker = instance / "ran"
    _plant(instance, marker)
    chat = {"agents": [{"number": 0}], "log": {}}
    target = chat if where == "context" else chat["agents"][0]
    target["agent_profile"] = "../workdir/u1/p"

    for ctxid in persist_chat.load_json_chats([json.dumps(chat)]):
        AgentContext.remove(ctxid)

    assert not marker.exists(), "Python from the caller's upload folder ran in the web server"


@pytest.mark.parametrize("name", ["../workdir/u1/p", "/etc", "a/b", "..", ".", "a\\b", ""])
def test_a_profile_name_is_one_folder_name(name):
    assert subagents.safe_profile_name(name) == ""


def test_an_ordinary_profile_name_is_kept():
    assert subagents.safe_profile_name("network-researcher") == "network-researcher"
    assert subagents.safe_profile_name("_example") == "_example"


def test_the_external_api_refuses_a_profile_that_is_a_path(monkeypatch):
    import asyncio

    from api.api_message import ApiMessage

    handler = ApiMessage(None, None)  # type: ignore[arg-type]
    response = asyncio.run(handler.process({"message": "hi", "agent_profile": "../workdir/u1/p"}, None))  # type: ignore[arg-type]
    assert response.status_code == 400
    assert b"Invalid agent profile" in response.get_data()
