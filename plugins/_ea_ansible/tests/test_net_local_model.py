"""ADR 0052: the local-model switch. Pure logic plus the hook, no database."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from helpers.errors import RepairableException
from plugins._ea_ansible.extensions.python.tool_execute_before import _96_local_model_gate as gate
from plugins._ea_ansible.helpers import config, local_model
from plugins._model_config.helpers import model_config


@pytest.mark.parametrize(
    "base,local",
    [
        ("", False),
        ("https://api.openai.com/v1", False),
        ("https://llm.example.com", False),
        ("http://localhost:11434", True),
        ("http://ollama:11434", True),
        ("127.0.0.1:8000", True),
        ("http://10.1.2.3/v1", True),
        ("http://192.168.0.5", True),
        ("http://[::1]:8000", True),
        ("http://8.8.8.8", False),
        ("http://172.32.0.1", False),
    ],
)
def test_is_local_base(base, local):
    assert local_model.is_local_base(base) is local


def test_tool_names():
    for name in ("net_read", "net_plan", "inventory_ports", "solarwinds_lookup", "net_read:request",
                 "azure_inventory", "azure_pull", "azure_read"):
        assert local_model.returns_device_data(name)
    assert not local_model.returns_device_data("response")


def _run(monkeypatch, *, switch, chat, utility, tool="net_read", root_chat=None):
    monkeypatch.setattr(config, "get_config", lambda agent=None: {"require_local_model": switch})
    root = SimpleNamespace(name="root")
    agent = SimpleNamespace(context=SimpleNamespace(root_agent=root))
    cfgs = {id(agent): chat, id(root): root_chat or chat}
    monkeypatch.setattr(model_config, "get_chat_model_config", lambda a: cfgs[id(a)])
    monkeypatch.setattr(model_config, "get_utility_model_config", lambda a: utility)
    ext = gate.LocalModelGate.__new__(gate.LocalModelGate)
    ext.agent = agent
    asyncio.run(ext.execute(tool_name=tool))


LOCAL = {"provider": "other", "name": "m", "api_base": "http://ollama:11434"}
HOSTED = {"provider": "openai", "name": "gpt", "api_base": ""}


def test_gate(monkeypatch):
    _run(monkeypatch, switch=False, chat=HOSTED, utility=HOSTED)  # off: nothing refused
    _run(monkeypatch, switch=True, chat=LOCAL, utility=LOCAL)  # all local: runs
    _run(monkeypatch, switch=True, chat=HOSTED, utility=HOSTED, tool="response")  # not a device tool
    with pytest.raises(RepairableException, match="chat model"):
        _run(monkeypatch, switch=True, chat=HOSTED, utility=LOCAL)
    with pytest.raises(RepairableException, match="utility model"):
        _run(monkeypatch, switch=True, chat=LOCAL, utility=HOSTED, tool="inventory_lookup")
    with pytest.raises(RepairableException):  # the root agent's model counts too
        _run(monkeypatch, switch=True, chat=LOCAL, utility=LOCAL, root_chat=HOSTED)
    # kwargs.api_base wins over the field, as build_kwargs sends it
    with pytest.raises(RepairableException):
        _run(monkeypatch, switch=True, chat={**LOCAL, "kwargs": {"api_base": ""}}, utility=LOCAL)


def test_gate_fails_closed(monkeypatch):
    def boom(agent=None):
        raise RuntimeError("x")

    monkeypatch.setattr(config, "get_config", boom)
    ext = gate.LocalModelGate.__new__(gate.LocalModelGate)
    ext.agent = SimpleNamespace()
    with pytest.raises(RepairableException, match="not run"):
        asyncio.run(ext.execute(tool_name="net_read"))
