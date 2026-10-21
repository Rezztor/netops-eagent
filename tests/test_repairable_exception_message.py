"""A repairable error reaches the model and the chat as its own sentence, not as a traceback."""

import asyncio
import importlib.util
from pathlib import Path

from helpers.errors import RepairableException

PATH = Path(__file__).resolve().parents[1] / (
    "extensions/python/_functions/agent/Agent/handle_exception/end/_50_handle_repairable_exception.py"
)


class _Log:
    def __init__(self):
        self.entries = []

    def log(self, **fields):
        self.entries.append(fields)


class _Agent:
    def __init__(self):
        self.warnings = []
        self.context = type("Context", (), {"log": _Log()})()

    def hist_add_warning(self, message):
        self.warnings.append(message)
        return type("Message", (), {"id": "m1"})()


def test_the_model_and_the_chat_get_the_sentence_without_the_traceback(monkeypatch):
    spec = importlib.util.spec_from_file_location("handle_repairable", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def no_extensions(*args, **kwargs):
        return None

    monkeypatch.setattr(module.extension, "call_extensions_async", no_extensions)
    agent = _Agent()
    hook = module.HandleRepairableException.__new__(module.HandleRepairableException)
    hook.agent = agent
    try:
        raise RepairableException("There is no change 'abc'.")
    except RepairableException as error:
        data = {"exception": error}
    asyncio.run(hook.execute(data=data))

    assert agent.warnings == ["There is no change 'abc'."]
    assert agent.context.log.entries[0]["content"] == "There is no change 'abc'."
    assert "Traceback" not in agent.warnings[0]
    assert data["exception"] is None
