"""`azure_pull`: numbers only, refusals as sentences, one permission. `collect` is faked: no network, no pull."""

import ast
import asyncio
from pathlib import Path

import pytest

from helpers import rbac, tool_permissions
from plugins._ea_azure.helpers import collect, config
from plugins._ea_azure.tools import azure_pull
from plugins._ea_azure.tools.azure_pull import AzurePull

TOOLS = Path(azure_pull.__file__).parent
RUN = "run-1"
DONE = {"id": RUN, "status": "partial", "error": "", "counts": {
    "trigger": "chat", "subscriptions": {"listed": 3, "read": 2, "failed": 1, "skipped": 0}, "resources": 1290,
    "added": 6, "changed": 2, "removed": 0, "returned": 1, "duration_s": 41,
    "per_subscription": [{"id": "s1", "name": "Production", "status": "failed", "resources": 0, "error": "x"}],
    "unknown_types": ["microsoft.secret/thing"]}}


def run_tool():
    tool = AzurePull.__new__(AzurePull)
    tool.args = {}
    return asyncio.new_event_loop().run_until_complete(tool.execute()).message


@pytest.fixture
def fake(monkeypatch):
    seen = {"started": []}
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    monkeypatch.setattr(config, "is_configured", lambda: True)
    monkeypatch.setattr(config, "get_config", lambda: {"min_pull_interval_minutes": 15})
    monkeypatch.setattr(azure_pull.proposals, "current_actor", lambda: "alice")
    monkeypatch.setattr(collect, "start", lambda trigger, actor: seen["started"].append((trigger, actor)) or RUN)
    monkeypatch.setattr(collect, "status", lambda: {"running": None, "last_run": DONE})
    monkeypatch.setattr(azure_pull, "POLL_SECONDS", 0.01)
    return seen


def test_it_starts_a_chat_pull_as_the_asking_engineer_and_returns_numbers_only(fake):
    out = run_tool()
    assert fake["started"] == [("chat", "alice")]
    assert out == ("The Azure pull partial: 2 of 3 subscriptions read (1 failed, 0 skipped), 1290 resources found; "
                   "6 added, 2 changed, 0 removed, 1 returned since the one before. It took 41 seconds.")
    assert "Production" not in out and "microsoft.secret" not in out


def test_a_failed_run_says_why_in_the_plugins_own_words(fake, monkeypatch):
    monkeypatch.setattr(collect, "status", lambda: {"running": None, "last_run": {**DONE, "status": "failed", "error": "Sign-in failed."}})
    assert run_tool().startswith("The Azure pull failed:") and run_tool().endswith("Sign-in failed.")


def test_it_waits_while_the_run_is_running_then_reports_it(fake, monkeypatch):
    answers = iter([{"running": {"id": RUN}, "last_run": None}, {"running": {"id": RUN}, "last_run": {"id": "older"}},
                    {"running": None, "last_run": DONE}])
    monkeypatch.setattr(collect, "status", lambda: next(answers))
    assert run_tool().startswith("The Azure pull partial")


def test_it_gives_up_after_the_wait_and_says_it_is_still_running(fake, monkeypatch):
    monkeypatch.setattr(collect, "status", lambda: {"running": {"id": RUN}, "last_run": None})
    monkeypatch.setattr(azure_pull, "WAIT_SECONDS", 0.05)
    assert run_tool() == "The pull is still running after 0.05 seconds. Ask again in a minute."


def test_refusals_are_plain_sentences_and_start_nothing(fake, monkeypatch):
    monkeypatch.setattr(config, "is_enabled", lambda: False)
    assert run_tool() == "The Azure plugin is turned off."
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    monkeypatch.setattr(config, "is_configured", lambda: False)
    assert run_tool().startswith("Azure is not set up yet")
    assert fake["started"] == []
    monkeypatch.setattr(config, "is_configured", lambda: True)
    for error, text in ((collect.Busy, "A pull is already running."), (collect.TooSoon, "less than 15 minutes ago")):
        def refuse(trigger, actor, error=error):
            raise error("x")
        monkeypatch.setattr(collect, "start", refuse)
        assert text in run_tool()


def test_it_needs_inventory_manage_while_the_lookup_needs_only_view():
    assert tool_permissions.required_permission("azure_pull") == rbac.PERM_INVENTORY_MANAGE
    assert tool_permissions.required_permission("azure_inventory") == rbac.PERM_INVENTORY_VIEW
    assert tool_permissions.is_allowed("azure_pull", "admin") and not tool_permissions.is_allowed("azure_pull", "user")


def test_no_tool_imports_the_credential_or_the_client():
    found = {f.name for f in TOOLS.glob("*.py")}
    assert {"azure_inventory.py", "azure_pull.py"} <= found
    for path in TOOLS.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [f"{node.module}.{a.name}" for a in node.names] if isinstance(node, ast.ImportFrom) else [])
            assert not any(m.split(".")[-1] in ("credential", "client", "requests") for m in mods), (path.name, mods)
