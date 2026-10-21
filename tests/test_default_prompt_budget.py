import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import AgentConfig, AgentContext, AgentContextType
from helpers import runtime, settings, tokens


def _iter_prompt_files():
    yield from (PROJECT_ROOT / "prompts").rglob("*.md")
    yield from (PROJECT_ROOT / "agents" / "primary" / "prompts").rglob("*.md")
    yield from (PROJECT_ROOT / "knowledge" / "main").rglob("*.md")
    for prompts_dir in (PROJECT_ROOT / "plugins").glob("*/prompts"):
        yield from prompts_dir.rglob("*.md")


async def _build_system_text(profile: str = "primary", rendered: bool = False) -> str:
    old_args = dict(runtime.args)
    runtime.args.clear()
    runtime.args["dockerized"] = "true"

    # Pin the settings the prompt is built from, for two reasons.
    #
    # Determinism: `workdir_show` splices a listing of the working directory
    # into the system prompt, so with it on, a *prompt budget* measurement
    # partly measures how many files happen to be sitting in usr/workdir. That
    # is not the thing under test.
    #
    # And correctness: the default workdir_path is the container path
    # /eagent/usr/workdir, which `dockerized=true` above means nothing rewrites.
    # The extension then tries to mkdir /eagent on the host and dies with
    # PermissionError -- but only when an earlier test in the same session has
    # not already populated the settings cache, so it reproduced in a full run
    # and not when this file was run alone.
    old_settings = settings._settings
    pinned = settings.get_default_settings()
    pinned["workdir_show"] = False
    settings._settings = pinned

    ctx = AgentContext(
        config=AgentConfig(
            profile=profile,
            knowledge_subdirs=["custom", "default"],
            mcp_servers='{"mcpServers": {}}',
        ),
        type=AgentContextType.USER,
        set_current=False,
    )
    try:
        if rendered:
            prompt = await ctx.root_agent.prepare_prompt(ctx.root_agent.loop_data)
            return str(prompt[0].content)
        system = await ctx.root_agent.get_system_prompt(ctx.root_agent.loop_data)
        return "\n\n".join(system)
    finally:
        AgentContext.remove(ctx.id)
        runtime.args.clear()
        runtime.args.update(old_args)
        settings._settings = old_settings


@pytest.mark.asyncio
async def test_default_primary_profile_prompt_budget_and_guardrails(database):
    system_text = await _build_system_text()
    rendered_system_text = await _build_system_text(rendered=True)
    communication_prompt = (
        PROJECT_ROOT / "prompts" / "agent.system.main.communication.md"
    ).read_text(encoding="utf-8")

    # The default prompt now intentionally includes the compact always-on tool
    # surface plus skill metadata. Keep the guardrail close to the observed
    # budget so prompt creep remains visible without pretending this surface is
    # a tiny single-tool prompt.
    #
    # 12500 against an observed 12028: this fork ships more bundled plugins than
    # upstream did, and each one's prompts/ joins the default profile's system
    # prompt. The ceiling was 10000 and the assertion was quarantined rather
    # than re-measured, which turned the guardrail off entirely. Raise it
    # deliberately when a plugin is added, and read a failure here as "the
    # prompt grew" rather than as noise.
    budget = tokens.approximate_tokens(system_text)
    assert budget <= 12500, f"default system prompt is {budget} tokens"
    assert "`tool_name` must be one listed tool name" in system_text
    assert "- tool_args: key value pairs tool arguments" in system_text
    assert '"tool_name": "call_subordinate"' in system_text
    assert '"tool_name": "parallel"' in system_text
    assert "Each `tool_calls` item is a normal tool request object" in system_text
    assert '"reset": true' in system_text
    assert '"tool_name": "code_execution_tool"' in system_text
    assert '"tool_name": "memory_load"' in system_text
    assert "informative but tight" in system_text
    assert "Your actual output starts with `{` and ends with `}`" in system_text
    assert "~~~json" in communication_prompt
    assert "~~~json" not in rendered_system_text
    assert "```json" not in rendered_system_text
    assert "# code_execution_remote tool" not in system_text
    assert "# text_editor_remote tool" not in system_text
    assert "### computer_use_remote" not in system_text
    assert '"tool_name": "code_execution_remote"' not in system_text
    assert '"tool_name": "text_editor_remote"' not in system_text
    assert '"tool_name": "computer_use_remote"' not in system_text
    assert "Computer Use enablement is scoped to the current CLI session" not in system_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        "primary",
        "default",
        "network-troubleshooter",
        "network-researcher",
        "automation-engineer",
    ],
)
async def test_rendered_profiles_strip_json_fences(profile: str, database):
    system_text = await _build_system_text(profile, rendered=True)

    assert "~~~json" not in system_text
    assert "```json" not in system_text


def test_remove_code_fences_can_target_json_only():
    from helpers import files

    prompt = """Before
~~~json
{"tool_name":"response","tool_args":{"text":"done"}}
~~~
~~~python
print("keep me fenced")
~~~
After
"""

    rendered = files.remove_code_fences(prompt, language="json")

    assert "~~~json" not in rendered
    assert '{"tool_name":"response"' in rendered
    assert '~~~python\nprint("keep me fenced")\n~~~' in rendered


def test_removed_small_profile_and_prompt_text_generic():
    # This was written as a concatenation of three fragments, purely to slip
    # past the brand grep. The check now has a way to declare the need, so the
    # name is written plainly and the reason is given.
    removed_profile = "a0_small"  # brand-leak-allowed: names an upstream directory we deleted

    assert not (PROJECT_ROOT / "agents" / removed_profile).exists()
    assert not (
        PROJECT_ROOT / "knowledge" / "main" / f"{removed_profile}_tool_call_examples.md"
    ).exists()
    assert not (PROJECT_ROOT / "knowledge" / "main" / "tool_call_reference_examples.md").exists()

    for path in _iter_prompt_files():
        assert removed_profile not in path.read_text(encoding="utf-8")


def test_prompt_token_estimate_omits_embedded_image_data_urls():
    embedded_png = "data:image/png;base64," + ("ABCDabcd0123+/==" * 20_000)
    prompt_text = f"user: please inspect this screenshot {embedded_png}"

    sanitized = tokens.sanitize_embedded_image_data_urls(prompt_text)

    assert "ABCDabcd0123+/==" not in sanitized
    assert "data:image/png;base64," in sanitized
    assert tokens.EMBEDDED_IMAGE_DATA_PLACEHOLDER in sanitized
    assert tokens.approximate_prompt_tokens(prompt_text) < 100
    assert tokens.approximate_prompt_tokens(prompt_text) < tokens.approximate_tokens(prompt_text) / 100
