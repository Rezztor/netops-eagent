"""A run, a delegation chain and a wait each have an end (audit 6, M3)."""

from types import SimpleNamespace

import pytest

from agent import LoopData
from extensions.python.message_loop_start import _20_iteration_budget as budget
from helpers.errors import HandledException, RepairableException
from tools import call_subordinate, wait


class _Agent:
    def __init__(self):
        self.warnings = []
        self.context = SimpleNamespace(log=SimpleNamespace(log=lambda **kwargs: None))

    def hist_add_warning(self, message):
        self.warnings.append(message)
        return SimpleNamespace(id="w")


@pytest.mark.asyncio
async def test_a_run_is_warned_at_its_budget_and_stopped_soon_after(monkeypatch):
    monkeypatch.setattr(budget, "MAX_ITERATIONS", 3)
    agent = _Agent()
    hook = budget.IterationBudget(agent)
    for iteration in range(3):
        await hook.execute(loop_data=LoopData(iteration=iteration))
    assert agent.warnings == []
    await hook.execute(loop_data=LoopData(iteration=3))
    assert len(agent.warnings) == 1 and "final answer" in agent.warnings[0]
    await hook.execute(loop_data=LoopData(iteration=4))
    with pytest.raises(HandledException):
        await hook.execute(loop_data=LoopData(iteration=5))


def test_a_subordinate_past_the_depth_limit_is_not_created(monkeypatch):
    monkeypatch.setattr(call_subordinate, "MAX_SUBORDINATE_DEPTH", 2)
    monkeypatch.setattr(call_subordinate, "_validate_subordinate_profile", lambda parent, profile: "")
    monkeypatch.setattr(call_subordinate, "_find_subordinate", lambda *args: None)

    def never(*args, **kwargs):
        raise AssertionError("a subordinate was created")

    monkeypatch.setattr(call_subordinate, "Agent", never)
    parent = SimpleNamespace(number=2, agent_name="A2")
    with pytest.raises(RepairableException, match="limited to 2 levels"):
        call_subordinate.get_or_create_subordinate(parent, message="go deeper")


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{"days": 2}, {"hours": 25}, {"days": 10**12}])
async def test_a_wait_longer_than_the_cap_is_refused_without_waiting(monkeypatch, args):
    async def never(**kwargs):
        raise AssertionError("waited")

    monkeypatch.setattr(wait, "managed_wait", never)

    async def no_intervention():
        return None

    tool = wait.WaitTool(SimpleNamespace(handle_intervention=no_intervention), "wait", None, args, "", None)
    response = await tool.execute(**args)
    assert "at most" in response.message
