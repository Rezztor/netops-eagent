"""Tool-level permission enforcement.

helpers/rbac.py declared tools.code_execution from the start,
and the Roles screen told administrators that a standard user could not execute
code. Nothing checked. These tests exist so that statement stays true.
"""

import pytest

from helpers import rbac, tool_permissions


# --- the map ----------------------------------------------------------------

def test_dangerous_tools_require_a_permission():
    for tool in (
        "code_execution_tool",
        "code_execution_remote",
        "input",
        "text_editor",
        "call_subordinate",
        "scheduler",
        "net_apply",
        "net_plan",
    ):
        assert tool_permissions.required_permission(tool) is not None, tool


def test_ordinary_tools_need_nothing_beyond_being_signed_in():
    for tool in ("response", "wait", "memory_load", "search_engine", "notify_user"):
        assert tool_permissions.required_permission(tool) is None, tool


def test_every_referenced_permission_actually_exists():
    """A typo here would silently grant, since an unknown permission is simply
    absent from every role's set."""
    for tool, permission in tool_permissions.TOOL_PERMISSIONS.items():
        assert permission in rbac.ALL_PERMISSIONS, f"{tool} -> {permission}"


# --- role outcomes ----------------------------------------------------------

def test_standard_user_cannot_execute_code():
    for tool in ("code_execution_tool", "code_execution_remote", "input"):
        assert not tool_permissions.is_allowed(tool, rbac.ROLE_USER), tool


def test_standard_user_can_still_converse_and_recall():
    for tool in ("response", "memory_load", "memory_save", "search_engine"):
        assert tool_permissions.is_allowed(tool, rbac.ROLE_USER), tool


def test_power_user_gets_the_tools_and_administrator_gets_everything():
    for tool in ("code_execution_tool", "call_subordinate", "net_read"):
        assert tool_permissions.is_allowed(tool, rbac.ROLE_POWER), tool
        assert tool_permissions.is_allowed(tool, rbac.ROLE_ADMIN), tool


def test_an_unknown_role_is_refused_the_dangerous_tools():
    """Unknown roles collapse to least privilege, so a corrupted record cannot
    widen access."""
    assert not tool_permissions.is_allowed("code_execution_tool", "wizard")
    assert not tool_permissions.is_allowed("code_execution_tool", None)


# --- enforcement point ------------------------------------------------------

def test_enforcement_lives_where_tools_are_resolved():
    """Inside each tool would mean a tool that forgets to check is the hole.
    Agent.get_tool is the single place a tool is produced from a name."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "agent.py"
    ).read_text(encoding="utf-8")
    resolver = source[source.index("    def get_tool("):]
    resolver = resolver[: resolver.index("\ndef re_search_session_id")]
    assert "tool_permissions.is_allowed" in resolver
    assert "from tools.refused import Refused" in resolver


def test_refusal_does_not_advertise_the_rest_of_the_toolbox():
    """Unknown replies with the full tool list; a refusal must not, or it
    invites the model to hunt for a substitute."""
    refused = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "tools" / "refused.py"
    ).read_text(encoding="utf-8")
    assert "build_tools_prompt" not in refused
    assert "tools_prompt" not in refused


def test_refusal_tells_the_model_to_stop_rather_than_route_around_it():
    message = tool_permissions.refusal_message("code_execution_tool", rbac.ROLE_USER)
    assert "not available" in message
    assert "Do not retry" in message
    assert "tools.code_execution" in message


def test_permission_is_taken_from_the_conversation_owner():
    """An agent runs on a background thread long after its request ended, so the
    check must not depend on a request session being present."""
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "helpers" / "tool_permissions.py"
    ).read_text(encoding="utf-8")
    assert 'getattr(context, "owner_id", None)' in source


# --- container execution guard ----------------------------------------------

def test_local_execution_can_be_forbidden_outright(monkeypatch):
    """The local backend runs inside the web server's own container. Whether it
    is used comes down to a config value that is easy to get wrong silently."""
    import importlib.util
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "plugins" / "_code_execution" / "tools" / "code_execution_tool.py"
    )
    spec = importlib.util.spec_from_file_location("cet_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.delenv("REZZTOR_REQUIRE_REMOTE_EXECUTION", raising=False)
    assert module._require_remote_execution() is False

    for value in ("true", "1", "yes", "on", "TRUE"):
        monkeypatch.setenv("REZZTOR_REQUIRE_REMOTE_EXECUTION", value)
        assert module._require_remote_execution() is True, value


def test_the_guard_refuses_rather_than_falling_back():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "plugins" / "_code_execution" / "tools" / "code_execution_tool.py"
    ).read_text(encoding="utf-8")
    assert "_require_remote_execution() and not ssh_enabled" in source
    assert "Refusing to run code in the application container" in source


def test_a_path_shaped_name_is_refused_for_every_role():
    # Agent.get_tool finds a tool by file name, so "./code_execution_tool" loads the real tool while matching no
    # entry in the map. It must be refused before it resolves, administrators included.
    for name in ("./code_execution_tool", "x/../net_apply", "/eagent/tools/scheduler", "..\\code_execution_tool"):
        for role in (rbac.ROLE_USER, rbac.ROLE_POWER, rbac.ROLE_ADMIN, None):
            assert not tool_permissions.is_allowed(name, role), (name, role)


def test_a_standard_user_cannot_read_the_network_through_solarwinds():
    """The role says "no network device access". `solarwinds_lookup` reads live
    infrastructure and, as `device_config`, archived running configurations, and
    it was the one network tool the table did not name."""
    assert tool_permissions.required_permission("solarwinds_lookup") == rbac.PERM_TOOL_NETWORK
    assert not tool_permissions.is_allowed("solarwinds_lookup", rbac.ROLE_USER)
    assert tool_permissions.is_allowed("solarwinds_lookup", rbac.ROLE_POWER)


def test_every_network_tool_a_plugin_ships_is_in_the_table():
    """A tool is open to every role unless it is named, so a new one has to be."""
    from pathlib import Path

    plugins = Path(__file__).resolve().parents[1] / "plugins"
    shipped = {
        tool.stem
        for name in ("_ea_ansible", "_ea_inventory", "_ea_solarwinds", "_ea_azure", "_ea_azure_change")
        for tool in (plugins / name / "tools").glob("*.py")
        if tool.stem != "__init__"
    }
    unnamed = sorted(shipped - set(tool_permissions.TOOL_PERMISSIONS))
    assert not unnamed, f"network tools any signed-in account may call: {unnamed}"


def test_attaching_a_file_from_the_runner_takes_the_right_to_use_the_runner():
    """`attach_file` fetches a file agent code made on the runner, and to do so
    it claims the account a runner slot and runs commands there as that slot.
    It was open to every role, so an account that may not execute code still
    took one of the pool's slots -- for good, since a slot is freed only when
    its account is deleted -- and had a command line on the runner to aim at."""
    assert tool_permissions.required_permission("attach_file") == rbac.PERM_TOOL_CODE_EXECUTION
    assert not tool_permissions.is_allowed("attach_file", rbac.ROLE_USER)
    assert tool_permissions.is_allowed("attach_file", rbac.ROLE_POWER)
