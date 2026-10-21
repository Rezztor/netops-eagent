from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import subagents, tool_policy

ROOT = Path(__file__).resolve().parents[1]
TITLES = {
    "primary": "NetOps Operator",
    "network-troubleshooter": "Network Troubleshooter",
    "network-researcher": "Network Researcher",
    "automation-engineer": "Automation Engineer",
}
NET_CHANGE = "plugin:_ea_ansible:net_change"
NET_APPLY = "plugin:_ea_ansible:net_apply"
NET_PLAN = "plugin:_ea_ansible:net_plan"
NET_READ = "plugin:_ea_ansible:net_read"
CODE = "plugin:_code_execution:code_execution_tool"
INVENTORY = "plugin:_ea_inventory:inventory_lookup"
IMPORT_TOOLS = ("plugin:_ea_inventory:inventory_read_file", "plugin:_ea_inventory:inventory_stage",
                "plugin:_ea_inventory:inventory_refresh_facts")


class _Context:
    def get_data(self, key, recursive=True):
        return None


def _allowed(profile: str, tool_id: str) -> bool:
    agent = SimpleNamespace(config=SimpleNamespace(profile=profile), context=_Context())
    name = tool_id.rsplit(":", 1)[-1]
    return tool_policy.resolve_tool(agent, name, canonical_id=tool_id).allowed


def test_netops_profiles_are_discoverable_with_titles():
    labels = {i["key"]: i["label"] for i in subagents.get_all_agents_list()}
    for key, title in TITLES.items():
        assert labels[key] == title
    for gone in ("developer", "researcher", "tiny-local"):
        assert gone not in labels


@pytest.mark.parametrize("profile", TITLES)
def test_profile_prompt_is_short_and_states_the_contract(profile):
    text = (ROOT / "agents" / profile / "prompts" / "agent.system.main.specifics.md").read_text()
    assert len(text.splitlines()) <= 60
    assert "approves and is accountable" in text
    assert "credentials" in text


def test_troubleshooter_is_read_only():
    assert _allowed("network-troubleshooter", NET_READ)
    for blocked in (NET_CHANGE, NET_APPLY, NET_PLAN, CODE, "local:call_subordinate"):
        assert not _allowed("network-troubleshooter", blocked)


def test_operator_may_draft_and_apply_but_researcher_and_engineer_have_no_device_tools():
    assert _allowed("primary", NET_CHANGE) and _allowed("primary", NET_APPLY) and _allowed("primary", NET_PLAN)
    for profile in ("network-researcher", "automation-engineer"):
        for tool in (NET_READ, NET_CHANGE, NET_APPLY, NET_PLAN, "plugin:_ea_solarwinds:solarwinds_lookup"):
            assert not _allowed(profile, tool)
    assert not _allowed("network-researcher", CODE)
    # A subordinate takes its own profile's tools, so a researcher that may delegate reaches the device tools.
    assert not _allowed("network-researcher", "local:call_subordinate")
    assert _allowed("automation-engineer", CODE)
    assert _allowed("network-researcher", "local:search_engine")


@pytest.mark.parametrize("tool", (INVENTORY, "plugin:_ea_inventory:inventory_ports", "plugin:_ea_azure:azure_inventory",
                                  "plugin:_ea_azure:azure_advise", "plugin:_ea_azure:azure_cost"))
def test_inventory_lookup_is_for_the_operator_and_troubleshooter_only(tool):
    assert _allowed("primary", tool) and _allowed("network-troubleshooter", tool)
    for profile in ("network-researcher", "automation-engineer"):
        assert not _allowed(profile, tool)


def test_inventory_import_tools_are_for_the_operator_only():
    for tool in (*IMPORT_TOOLS, "plugin:_ea_azure:azure_pull"):
        assert _allowed("primary", tool)
        for profile in ("network-troubleshooter", "network-researcher", "automation-engineer"):
            assert not _allowed(profile, tool)


@pytest.mark.parametrize("profile", TITLES)
def test_response_tool_is_always_allowed(profile):
    assert _allowed(profile, "local:response")


# --- the shipped NetOps skills -------------------------------------------------------------------------------
# A skill is prompt text, so nothing else notices when it names a tool that is gone or a command the card would
# refuse. These checks read the files; they never touch a device.

NETOPS_SKILLS = sorted((ROOT / "skills").glob("netops-*/SKILL.md"))
SPECIFICS = {p: (ROOT / "agents" / p / "prompts" / "agent.system.main.specifics.md").read_text() for p in TITLES}
CHANGE_SKILLS = ("netops-change-checklist", "netops-change-runbook", "netops-device-onboarding", "netops-inventory-import")


def _show_pattern() -> "re.Pattern[str]":
    import re

    import yaml

    entries = yaml.safe_load((ROOT / "plugins/_ea_ansible/catalogue.yaml").read_text())["entries"]
    show = next(e for e in entries if e["name"] == "reads/show")
    return re.compile(show["variables"]["command_1"]["pattern"])


def _table_commands(text: str):
    """(column header, command) for every backticked cell in a markdown table."""
    import re

    header: list[str] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            header = []
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not header:
            header = cells
        elif not set(line) <= set("|- "):
            for col, cell in zip(header, cells):
                for cmd in re.findall(r"`([^`]+)`", cell):
                    yield col, cmd


def test_there_are_netops_skills():
    assert len(NETOPS_SKILLS) >= 3


@pytest.mark.parametrize("path", NETOPS_SKILLS, ids=lambda p: p.parent.name)
def test_netops_skill_is_valid_and_short(path):
    from helpers import skills

    assert skills.validate_skill_md(path) == []
    skill = skills.skill_from_markdown(path)
    assert skill.name == path.parent.name
    assert len(path.read_text().splitlines()) <= 75


@pytest.mark.parametrize("path", NETOPS_SKILLS, ids=lambda p: p.parent.name)
def test_netops_skill_commands_pass_the_show_pattern(path):
    import re

    pattern = _show_pattern()
    for col, cmd in _table_commands(path.read_text()):
        if "engineer runs" in col or not cmd.startswith("show"):
            # the agent never runs these: a person on a firewall does
            assert "engineer runs" in col, f"{path.parent.name}: {cmd!r} is not a show command"
            continue
        assert pattern.fullmatch(re.sub(r"<[a-z_-]+>", "x1", cmd)), f"{path.parent.name}: {cmd!r} would be refused"


@pytest.mark.parametrize("path", NETOPS_SKILLS, ids=lambda p: p.parent.name)
def test_netops_skill_names_only_real_tools_and_skills(path):
    import re

    text = path.read_text()
    tools = {p.stem for p in (ROOT / "plugins").glob("*/tools/*.py")}
    for name in re.findall(r"`((?:net|inventory|solarwinds)_[a-z_]+)`", text):
        assert name in tools, f"{path.parent.name}: no such tool {name}"
    skills = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}
    for name in re.findall(r"\b(netops-[a-z-]+)\b", text):
        assert name in skills, f"{path.parent.name}: no such skill {name}"


@pytest.mark.parametrize("path", NETOPS_SKILLS, ids=lambda p: p.parent.name)
def test_netops_skill_is_wired_into_a_profile(path):
    assert any(path.parent.name in text for text in SPECIFICS.values())


def test_read_only_profiles_are_not_pointed_at_change_skills():
    for profile in ("network-troubleshooter", "network-researcher", "automation-engineer"):
        for skill in CHANGE_SKILLS:
            assert skill not in SPECIFICS[profile]


def test_every_bundled_skill_has_valid_front_matter():
    # An unquoted colon in a description is invalid YAML and the loader skips the skill without a word; it hid two
    # NetOps skills from the agent until this check existed.
    from helpers import skills

    paths = [*(ROOT / "skills").glob("*/SKILL.md"), *(ROOT / "plugins").glob("*/skills/*/SKILL.md")]
    assert paths
    for path in paths:
        assert skills.validate_skill_md(path) == [], path
