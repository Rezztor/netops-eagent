"""What the model configuration tells an account that does not administer it.

Every account needs the list of presets to pick one for a chat. The endpoint
answered every account with the whole configuration: each model's `api_base`
and its extra parameters, which is where an administrator puts a gateway's
key or a header (`extra_headers`), and where a URL may carry a password.
`model_config_set`, `model_presets` and `api_keys` are administrator-only for
that reason; the read was open.
"""

import asyncio
import threading

import pytest
from flask import Flask

from helpers import login, rbac
from plugins._model_config.api import model_config_get
from plugins._model_config.helpers import model_config

SLOT = {
    "provider": "openai",
    "name": "gpt-x",
    "ctx_length": 128000,
    "vision": True,
    "api_base": "https://user:hunter2@gateway.example/v1",
    "kwargs": {"extra_headers": {"X-Gateway-Key": "gw-secret"}, "temperature": 0},
}
CONFIG = {"chat_model": dict(SLOT), "utility_model": dict(SLOT), "embedding_model": dict(SLOT), "model_preset": "Fast"}
PRESETS = [{"name": "Fast", "chat": dict(SLOT), "utility": dict(SLOT), "embedding": dict(SLOT)}]


@pytest.fixture
def answer(monkeypatch):
    monkeypatch.setattr(model_config, "get_config", lambda **kwargs: {k: dict(v) if isinstance(v, dict) else v for k, v in CONFIG.items()})
    monkeypatch.setattr(model_config, "get_presets", lambda *a, **k: [{k: dict(v) if isinstance(v, dict) else v for k, v in p.items()} for p in PRESETS])
    monkeypatch.setattr(model_config, "get_configured_preset_name", lambda **kwargs: "Fast")
    monkeypatch.setattr(model_config, "get_chat_providers", lambda: [])
    monkeypatch.setattr(model_config, "get_embedding_providers", lambda: [])
    monkeypatch.setattr(model_config_get, "get_raw_providers", lambda kind: [])

    def ask(administrator: bool):
        monkeypatch.setattr(login, "has_permission", lambda permission: administrator and permission == rbac.PERM_SETTINGS_MANAGE)
        handler = model_config_get.ModelConfigGet(Flask("model_config_get_roles"), threading.Lock())
        return asyncio.run(handler.process({}, None))

    return ask


def test_an_ordinary_account_gets_the_presets_without_their_connections(answer):
    told = answer(administrator=False)
    text = repr(told)

    assert "hunter2" not in text and "gw-secret" not in text and "gateway.example" not in text
    # What the chat toolbar reads is still there.
    assert told["presets"][0]["name"] == "Fast"
    assert told["presets"][0]["chat"]["name"] == "gpt-x"
    assert told["config"]["chat_model"]["ctx_length"] == 128000
    assert told["config"]["chat_model"]["vision"] is True
    assert told["selected_preset"] == "Fast"


def test_an_administrator_still_gets_the_whole_configuration(answer):
    told = answer(administrator=True)

    assert told["config"]["chat_model"]["api_base"] == SLOT["api_base"]
    assert told["presets"][0]["chat"]["kwargs"] == SLOT["kwargs"]
