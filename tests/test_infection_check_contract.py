"""The infection check never sees resolved secrets, and says so when it could not run (audit 6, M5 and L9)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins._infection_check.helpers.checker import InfectionChecker

ROOT = Path(__file__).resolve().parents[1]


def test_the_tool_gate_runs_before_secrets_are_unmasked():
    # Hooks run in file-name order across folders; the gate sends tool arguments to a model with no masking of its own.
    unmask = "_10_unmask_secrets.py"
    assert (ROOT / "extensions/python/tool_execute_before" / unmask).is_file()
    gates = [p.name for p in (ROOT / "plugins/_infection_check/extensions/python/tool_execute_before").glob("*.py")]
    assert gates and all(name < unmask for name in gates), gates


@pytest.mark.asyncio
async def test_a_check_that_fails_leaves_a_warning_in_the_chat(monkeypatch):
    logged = []
    log = SimpleNamespace(set_progress=lambda text: None, log=lambda **kwargs: logged.append(kwargs))
    agent = SimpleNamespace(context=SimpleNamespace(log=log))
    checker = InfectionChecker({}, 0)

    async def broken(*args, **kwargs):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(checker, "_gate_inner", broken)
    await checker.gate(agent, tool_name="net_read", tool_args={})
    assert any(item["type"] == "warning" and "ran without it" in item["content"] for item in logged), logged
