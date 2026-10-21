from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent import Agent, AgentConfig, AgentContext
from helpers import persist_chat, projects, settings, user_scope
from helpers.errors import RepairableException


class _FakeContext:
    def __init__(self, id: str = "ctx") -> None:
        self.id = id
        self.name = None
        self.data = {}
        self.output_data = {}
        self.created_at = datetime.now(timezone.utc)
        self.root_agent = None

    def get_data(self, key: str, recursive: bool = True):
        return self.data.get(key)

    def set_data(self, key: str, value, recursive: bool = True):
        self.data[key] = value

    def get_output_data(self, key: str, recursive: bool = True):
        return self.output_data.get(key)

    def set_output_data(self, key: str, value, recursive: bool = True):
        self.output_data[key] = value

    def is_running(self) -> bool:
        return False


class _FakeParentAgent:
    def __init__(self) -> None:
        self.number = 0
        self.agent_name = "E0"
        self.config = AgentConfig(mcp_servers="", profile="primary")
        self.context = _FakeContext()
        self.data = {}

    def get_data(self, key: str):
        return self.data.get(key)

    def set_data(self, key: str, value):
        self.data[key] = value

    def read_prompt(self, _file: str, **_kwargs) -> str:
        return ""


def test_hidden_default_profile_normalizes_to_primary() -> None:
    configured = settings.get_default_settings()
    configured["agent_profile"] = "default"

    assert settings.normalize_settings(configured)["agent_profile"] == "primary"


class _FakeSubAgent:
    DATA_NAME_SUPERIOR = "_superior"
    DATA_NAME_SUBORDINATE = "_subordinate"

    _counter = 0

    def __init__(self, number: int, config: AgentConfig, context=None) -> None:
        if context is None:
            self.__class__._counter += 1
            context = _FakeContext(f"child-{self.__class__._counter}")
        self.number = number
        self.agent_name = f"A{number}"
        self.config = config
        self.context = context
        self.context.root_agent = self
        self.data = {}
        self.history = SimpleNamespace(new_topic=lambda: None)
        self.messages = []

    def set_data(self, key: str, value):
        self.data[key] = value

    def get_data(self, key: str):
        return self.data.get(key)

    def hist_add_user_message(self, message):
        self.messages.append(message)

    async def monologue(self):
        return "delegated"


@pytest.mark.asyncio
async def test_call_subordinate_rejects_unknown_profile(monkeypatch) -> None:
    import tools.call_subordinate as call_subordinate

    monkeypatch.setattr(
        call_subordinate,
        "_subordinate_profile_labels",
        lambda _agent: {"developer": "Developer", "researcher": "Researcher"},
    )
    parent = _FakeParentAgent()
    tool = call_subordinate.Delegation(
        parent,  # type: ignore[arg-type]
        "call_subordinate",
        None,
        {"profile": "ghost", "message": "work"},
        "",
        None,
    )

    with pytest.raises(RepairableException, match="Agent profile 'ghost' not found"):
        await tool.execute(message="work", profile="ghost", reset=True)

    assert parent.data == {}


@pytest.mark.asyncio
async def test_call_subordinate_uses_valid_profile(monkeypatch) -> None:
    import tools.call_subordinate as call_subordinate

    monkeypatch.setattr(call_subordinate, "Agent", _FakeSubAgent)
    monkeypatch.setattr(
        call_subordinate,
        "_subordinate_profile_labels",
        lambda _agent: {"developer": "Developer"},
    )
    monkeypatch.setattr(
        call_subordinate,
        "initialize_agent",
        lambda override_settings=None: AgentConfig(
            mcp_servers="",
            profile=(override_settings or {}).get("agent_profile", "primary"),
        ),
    )
    monkeypatch.setattr(
        call_subordinate.message_queue, "log_user_message", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        call_subordinate.persist_chat, "save_tmp_chat", lambda _context: None
    )

    parent = _FakeParentAgent()
    tool = call_subordinate.Delegation(
        parent,  # type: ignore[arg-type]
        "call_subordinate",
        None,
        {"profile": "developer", "message": "work"},
        "",
        None,
    )

    response = await tool.execute(message="work", profile="developer", reset=True)
    children = parent.get_data(call_subordinate.SUBORDINATES_DATA_KEY)
    child = next(iter(children.values()))

    assert response.message == "delegated"
    assert response.additional == {"context_id": child.context.id}
    assert child.number == 1
    assert child.config.profile == "developer"
    assert child.messages[0].message == "work"


@pytest.mark.asyncio
async def test_call_subordinate_reset_false_reuses_numbered_child(monkeypatch) -> None:
    import tools.call_subordinate as call_subordinate

    monkeypatch.setattr(call_subordinate, "Agent", _FakeSubAgent)
    monkeypatch.setattr(
        call_subordinate, "_subordinate_profile_labels", lambda _agent: {}
    )
    monkeypatch.setattr(
        call_subordinate,
        "initialize_agent",
        lambda override_settings=None: AgentConfig(mcp_servers="", profile="primary"),
    )
    monkeypatch.setattr(
        call_subordinate.message_queue, "log_user_message", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        call_subordinate.persist_chat, "save_tmp_chat", lambda _context: None
    )

    parent = _FakeParentAgent()
    tool = call_subordinate.Delegation(
        parent,  # type: ignore[arg-type]
        "call_subordinate",
        None,
        {},
        "",
        None,
    )
    first = await tool.execute(message="first", reset=True)
    second = await tool.execute(
        message="continue",
        context_id=first.additional["context_id"],  # type: ignore[index]
        reset=False,
    )

    children = parent.get_data(call_subordinate.SUBORDINATES_DATA_KEY)
    child = next(iter(children.values()))
    assert len(children) == 1
    assert child.number == 1
    assert [message.message for message in child.messages] == ["first", "continue"]
    assert second.additional == first.additional


def test_subordinate_tree_numbers_each_generation(monkeypatch) -> None:
    import tools.call_subordinate as call_subordinate

    monkeypatch.setattr(call_subordinate, "Agent", _FakeSubAgent)
    monkeypatch.setattr(
        call_subordinate, "_subordinate_profile_labels", lambda _agent: {}
    )
    monkeypatch.setattr(
        call_subordinate,
        "initialize_agent",
        lambda override_settings=None: AgentConfig(mcp_servers="", profile="primary"),
    )

    parent = _FakeParentAgent()
    child = call_subordinate.get_or_create_subordinate(
        parent,  # type: ignore[arg-type]
        reset=True,
        message="E1 work",
    )
    grandchild = call_subordinate.get_or_create_subordinate(
        child,  # type: ignore[arg-type]
        reset=True,
        message="E2 work",
    )

    assert child.number == 1
    assert grandchild.number == 2
    assert child.context.get_output_data("parent_context_id") == parent.context.id
    assert grandchild.context.get_output_data("parent_context_id") == child.context.id
    assert grandchild.get_data(Agent.DATA_NAME_SUPERIOR) is child


def test_subordinate_context_id_is_scoped_to_its_parent(monkeypatch) -> None:
    import tools.call_subordinate as call_subordinate

    monkeypatch.setattr(call_subordinate, "Agent", _FakeSubAgent)
    monkeypatch.setattr(
        call_subordinate, "_subordinate_profile_labels", lambda _agent: {}
    )
    monkeypatch.setattr(
        call_subordinate,
        "initialize_agent",
        lambda override_settings=None: AgentConfig(mcp_servers="", profile="primary"),
    )

    owner = _FakeParentAgent()
    other = _FakeParentAgent()
    other.context = _FakeContext("other-parent")
    child = call_subordinate.get_or_create_subordinate(
        owner,  # type: ignore[arg-type]
        reset=True,
        message="private branch",
    )

    with pytest.raises(RepairableException, match="was not found under E0"):
        call_subordinate.get_or_create_subordinate(
            other,  # type: ignore[arg-type]
            context_id=child.context.id,
            reset=False,
        )


@pytest.mark.asyncio
async def test_call_subordinate_requires_reset_to_change_existing_profile(monkeypatch) -> None:
    import tools.call_subordinate as call_subordinate

    monkeypatch.setattr(
        call_subordinate,
        "_subordinate_profile_labels",
        lambda _agent: {"developer": "Developer", "researcher": "Researcher"},
    )

    parent = _FakeParentAgent()
    existing = SimpleNamespace(config=AgentConfig(mcp_servers="", profile="developer"))
    parent.set_data(_FakeSubAgent.DATA_NAME_SUBORDINATE, existing)
    tool = call_subordinate.Delegation(
        parent,  # type: ignore[arg-type]
        "call_subordinate",
        None,
        {"profile": "researcher", "message": "work"},
        "",
        None,
    )

    with pytest.raises(RepairableException, match="Set reset=true"):
        await tool.execute(message="work", profile="researcher", reset=False)


def test_persist_chat_roundtrip_preserves_each_agent_profile(monkeypatch) -> None:
    monkeypatch.setattr(
        persist_chat,
        "initialize_agent",
        lambda override_settings=None: AgentConfig(
            mcp_servers="",
            profile=(override_settings or {}).get("agent_profile", "primary"),
        ),
    )

    context_id = "ctx-subagent-profile"
    AgentContext.remove(context_id)
    context = AgentContext(
        config=AgentConfig(mcp_servers="", profile="primary"),
        id=context_id,
        set_current=False,
    )
    child = Agent(1, AgentConfig(mcp_servers="", profile="developer"), context)
    context.root_agent.set_data(Agent.DATA_NAME_SUBORDINATE, child)
    child.set_data(Agent.DATA_NAME_SUPERIOR, context.root_agent)

    try:
        serialized = persist_chat._serialize_context(context)
        assert serialized["agent_profile"] == "primary"
        assert serialized["agents"][0]["agent_profile"] == "primary"
        assert serialized["agents"][1]["agent_profile"] == "developer"

        AgentContext.remove(context_id)
        restored = persist_chat._deserialize_context(serialized)
        restored_child = restored.root_agent.get_data(Agent.DATA_NAME_SUBORDINATE)

        assert restored.config.profile == "primary"
        assert restored.root_agent.config.profile == "primary"
        assert restored_child.config.profile == "developer"
    finally:
        AgentContext.remove(context_id)


def test_persisted_numbered_child_is_reusable_after_reload(monkeypatch) -> None:
    import tools.call_subordinate as call_subordinate

    config_factory = lambda override_settings=None: AgentConfig(
        mcp_servers="",
        profile=(override_settings or {}).get("agent_profile", "primary"),
    )
    monkeypatch.setattr(
        persist_chat,
        "initialize_agent",
        config_factory,
    )
    monkeypatch.setattr(call_subordinate, "initialize_agent", config_factory)

    parent_id = "ctx-persisted-agent-tree-parent"
    AgentContext.remove(parent_id)
    parent = AgentContext(
        AgentConfig(mcp_servers="", profile="primary"),
        id=parent_id,
        set_current=False,
    )
    child = call_subordinate.get_or_create_subordinate(
        parent.root_agent,
        reset=True,
        message="persist me",
    )
    context_id = child.context.id
    try:
        assert len(persist_chat._serialize_context(parent)["agents"]) == 1
        serialized = persist_chat._serialize_context(child.context)
        AgentContext.remove(context_id)
        parent.root_agent.data.pop(call_subordinate.SUBORDINATES_DATA_KEY, None)
        restored = persist_chat._deserialize_context(serialized)
        resumed = call_subordinate.get_or_create_subordinate(
            parent.root_agent,
            context_id=context_id,
            reset=False,
        )

        assert restored.root_agent.number == 1
        assert restored.root_agent.agent_name == "E1"
        assert restored.get_output_data("parent_context_id") == parent.id
        assert restored.get_output_data("parent_agent_number") == 0
        assert resumed is restored.root_agent
        assert resumed.get_data(Agent.DATA_NAME_SUPERIOR) is parent.root_agent
    finally:
        AgentContext.remove(context_id)
        AgentContext.remove(parent_id)


@pytest.mark.parametrize("project_name", [None, "demo"], ids=["global", "project"])
@pytest.mark.asyncio
async def test_agent_profile_set_uses_scope_and_preserves_subagent_profile(
    monkeypatch, project_name
) -> None:
    import api.agent_profile_set as agent_profile_set

    requested_scopes = []
    monkeypatch.setattr(
        agent_profile_set.subagents,
        "get_agents_dict",
        lambda scope: requested_scopes.append(scope)
        or {"researcher": SimpleNamespace(title="Researcher", enabled=True)},
    )
    monkeypatch.setattr(
        agent_profile_set,
        "initialize_agent",
        lambda override_settings=None: AgentConfig(
            mcp_servers="",
            profile=(override_settings or {}).get("agent_profile", "primary"),
        ),
    )
    monkeypatch.setattr(agent_profile_set, "save_tmp_chat", lambda _context: None)
    monkeypatch.setattr(
        agent_profile_set,
        "mark_dirty_for_context",
        lambda *_args, **_kwargs: None,
    )

    context_id = "ctx-profile-switch"
    AgentContext.remove(context_id)
    # Inside a scope, because the handler checks who owns the conversation
    # before it touches it -- and a context built with no owner belongs to
    # nobody, which is the same refusal.
    owner = "profile-owner"
    with user_scope.user_scope(owner):
        context = AgentContext(
            config=AgentConfig(mcp_servers="", profile="primary"),
            id=context_id,
            set_current=False,
        )
    child = Agent(1, AgentConfig(mcp_servers="", profile="developer"), context)
    context.root_agent.set_data(Agent.DATA_NAME_SUBORDINATE, child)
    child.set_data(Agent.DATA_NAME_SUPERIOR, context.root_agent)
    if project_name:
        context.set_data(projects.CONTEXT_DATA_KEY_PROJECT, project_name)

    try:
        handler = agent_profile_set.SetAgentProfile.__new__(
            agent_profile_set.SetAgentProfile
        )
        with user_scope.user_scope(owner):
            response = await handler.process(
                {"context_id": context_id, "agent_profile": "researcher"},
                request=None,  # type: ignore[arg-type]
            )

        assert response["ok"] is True
        assert context.config.profile == "researcher"
        assert context.root_agent.config.profile == "researcher"
        assert child.config.profile == "developer"
        assert requested_scopes == [project_name]
    finally:
        AgentContext.remove(context_id)


def test_short_label_cuts_at_word_boundary() -> None:
    import tools.call_subordinate as call_subordinate

    assert call_subordinate._short_label("  short   text ") == "short text"
    out = call_subordinate._short_label("alpha beta gamma delta", 14)
    assert out == "alpha beta…"
    text = "word " * 40
    out = call_subordinate._short_label(text)
    assert len(out) <= 80 and out.endswith("word…")


@pytest.mark.parametrize(
    "name, expected",
    [("", "Developer: Research Cisco"), ("Custom name", "Custom name")],
)
def test_subordinate_conversation_name(monkeypatch, name, expected) -> None:
    import tools.call_subordinate as call_subordinate

    monkeypatch.setattr(call_subordinate, "Agent", _FakeSubAgent)
    monkeypatch.setattr(
        call_subordinate, "_subordinate_profile_labels", lambda _agent: {"developer": "Developer"}
    )
    monkeypatch.setattr(
        call_subordinate,
        "initialize_agent",
        lambda override_settings=None: AgentConfig(mcp_servers="", profile="developer"),
    )
    child = call_subordinate.get_or_create_subordinate(
        _FakeParentAgent(),  # type: ignore[arg-type]
        profile="developer",
        name=name,
        message="Research Cisco",
    )
    assert child.context.name == expected

    long = call_subordinate._default_name(
        _FakeParentAgent(), child, "developer", "lorem ipsum " * 20  # type: ignore[arg-type]
    )
    assert long.startswith("Developer: ") and len(long) <= 80


def test_a_profile_name_is_one_directory_name_and_the_endpoint_is_gated(tmp_path, monkeypatch):
    """`subagents` was open to every account and joined the name unchecked, so
    delete with ".." removed `usr/` and a save rewrote what every agent loads."""
    import pytest

    from helpers import api_policy, files, rbac, subagents

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr" / "agents").mkdir(parents=True)
    (tmp_path / "usr" / "keep.txt").write_text("x", encoding="utf-8")

    for name in ("..", "../..", "/etc", "a/b", ""):
        with pytest.raises(ValueError):
            subagents.delete_agent_data(name)
        with pytest.raises(ValueError):
            subagents.save_agent_data(name, subagents.SubAgent(name="x"))
        with pytest.raises(ValueError):
            subagents.load_agent_data(name)
    assert (tmp_path / "usr" / "keep.txt").exists()

    assert api_policy.required_permission_for("subagents") == rbac.PERM_PLUGINS_MANAGE
