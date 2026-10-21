import threading
from pathlib import Path

import pytest
from flask import Response

from agent import AgentContext
from api.stop import Stop


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Log:
    def __init__(self) -> None:
        self.progress_calls = []
        self.entries = []

    def set_progress(self, progress: str, *, active: bool) -> None:
        self.progress_calls.append((progress, active))

    def log(self, **kwargs) -> None:
        self.entries.append(kwargs)


class _Context:
    id = "chat-1"

    def __init__(self) -> None:
        self.paused = True
        self.killed = False
        self.log = _Log()

    def is_running(self) -> bool:
        return True

    def kill_process(self) -> None:
        self.killed = True


@pytest.mark.asyncio
async def test_stop_endpoint_cancels_without_replacing_the_run(monkeypatch) -> None:
    context = _Context()
    handler = Stop(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]

    def use_context(ctxid: str):
        assert ctxid == context.id
        return context

    monkeypatch.setattr(AgentContext, "use", use_context)

    result = await handler.process(
        {"context": context.id}, request=None  # type: ignore[arg-type]
    )

    assert context.killed is True
    assert context.paused is False
    assert context.log.progress_calls == [("", False)]
    assert context.log.entries == [
        {
            "type": "info",
            "content": "Agent process stopped.",
            "finished": True,
        }
    ]
    assert result == {
        "message": "Agent process stopped.",
        "context": context.id,
        "stopped": True,
    }


@pytest.mark.asyncio
async def test_stop_endpoint_requires_an_explicit_context() -> None:
    handler = Stop(app=None, thread_lock=threading.RLock())  # type: ignore[arg-type]

    result = await handler.process({}, request=None)  # type: ignore[arg-type]

    assert isinstance(result, Response)
    assert result.status_code == 400
    assert Stop.requires_auth() is True
    assert Stop.requires_csrf() is True


