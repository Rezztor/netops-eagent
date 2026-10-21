"""The e-mail dispatcher runs on its own context, never on a chat that merely has its name.

Any account can rename one of its chats to "Email Dispatcher"; the poll loop looked the dispatcher up by that name, so
from the next poll every inbound mail was routed by that user's model (audit 3, L9).
"""

from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace

from plugins._email_integration.helpers import handler


def test_a_chat_named_email_dispatcher_is_not_borrowed(monkeypatch):
    ids = itertools.count()

    class Context:
        _contexts: dict = {}

        def __init__(self, config=None, name="", type=None, owner="nobody"):
            self.id, self.name, self.type = f"ctx{next(ids)}", name, type
            self.root_agent = SimpleNamespace(owner=owner)
            Context._contexts[self.id] = self

        @staticmethod
        def get(context_id):
            return Context._contexts.get(context_id)

    Context(name="Email Dispatcher", owner="mallory")  # a user's chat, renamed
    used = []

    async def dispatch(agent, cfg, msg):
        used.append(agent.owner)

    monkeypatch.setattr(handler, "AgentContext", Context)
    monkeypatch.setattr(handler, "initialize_agent", lambda: None)
    monkeypatch.setattr(handler, "_dispatcher_ctx", None)
    monkeypatch.setattr(handler, "_dispatch_message", dispatch)

    mail = SimpleNamespace(sender="someone@example.com")
    asyncio.run(handler._dispatch_all({"username": "inbox@example.com"}, [mail]))
    asyncio.run(handler._dispatch_all({"username": "inbox@example.com"}, [mail]))

    assert used == ["nobody", "nobody"]
    assert len(Context._contexts) == 2, "the dispatcher's own context is made once and kept"
