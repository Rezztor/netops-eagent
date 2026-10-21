import sys
import threading
import types
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.modules.setdefault("giturlparse", types.SimpleNamespace(parse=lambda *args, **kwargs: None))
sys.modules.setdefault("whisper", types.SimpleNamespace(load_model=lambda *args, **kwargs: None))

from plugins._model_config.helpers.ctx_length import apply_auto_ctx_lengths


def test_auto_ctx_fills_detected_window_and_skips_embedding(monkeypatch):
    monkeypatch.setattr(
        "plugins._model_config.helpers.ctx_length.resolve_ctx_length",
        lambda _provider, name, **_kwargs: 1_000_000 if name == "glm-5.3" else None,
    )

    config = {
        "chat_model": {"provider": "ollama_cloud", "name": "glm-5.3"},
        "utility_model": {"provider": "ollama_cloud", "name": "glm-5.3"},
        "embedding_model": {
            "provider": "huggingface",
            "name": "sentence-transformers/all-MiniLM-L6-v2",
            "ctx_length": 512,
        },
    }

    apply_auto_ctx_lengths(config)

    assert config["chat_model"]["ctx_length"] == 1_000_000
    assert "ctx_length_manual" not in config["chat_model"]
    assert config["utility_model"]["ctx_length"] == 1_000_000
    assert "ctx_length" not in config["embedding_model"]
    assert "ctx_length_manual" not in config["embedding_model"]


def test_manual_ctx_keeps_typed_value(monkeypatch):
    monkeypatch.setattr(
        "plugins._model_config.helpers.ctx_length.resolve_ctx_length",
        lambda *_args, **_kwargs: 1_000_000,
    )

    config = {
        "chat_model": {
            "provider": "ollama_cloud",
            "name": "glm-5.3",
            "ctx_length": 200000,
            "ctx_length_manual": True,
        }
    }

    apply_auto_ctx_lengths(config)

    assert config["chat_model"]["ctx_length"] == 200000
    assert config["chat_model"]["ctx_length_manual"] is True


def test_auto_ctx_keeps_previous_window_when_detect_fails(monkeypatch):
    monkeypatch.setattr(
        "plugins._model_config.helpers.ctx_length.resolve_ctx_length",
        lambda *_args, **_kwargs: None,
    )

    config = {"chat_model": {"provider": "ollama_cloud", "name": "mystery"}}
    previous = {"chat_model": {"provider": "ollama_cloud", "name": "old", "ctx_length": 200000}}

    apply_auto_ctx_lengths(config, previous)

    assert config["chat_model"]["ctx_length"] == 200000


def test_auto_ctx_replaces_stale_saved_window(monkeypatch):
    monkeypatch.setattr(
        "plugins._model_config.helpers.ctx_length.resolve_ctx_length",
        lambda *_args, **_kwargs: 1_000_000,
    )

    config = {
        "chat_model": {
            "provider": "ollama_cloud",
            "name": "glm-5.3",
            "ctx_length": 200000,
        }
    }

    apply_auto_ctx_lengths(config)

    assert config["chat_model"]["ctx_length"] == 1_000_000
    assert "ctx_length_manual" not in config["chat_model"]


def test_auto_ctx_keeps_catalogue_window_when_litellm_misses(monkeypatch):
    monkeypatch.setattr(
        "plugins._model_config.helpers.ctx_length.resolve_ctx_length",
        lambda *_args, **_kwargs: None,
    )

    config = {
        "chat_model": {
            "provider": "vllm",
            "name": "local-llama",
            "ctx_length": 131072,
        }
    }
    previous = {"chat_model": {"provider": "vllm", "name": "old", "ctx_length": 200000}}

    apply_auto_ctx_lengths(config, previous)

    assert config["chat_model"]["ctx_length"] == 131072


def _prepare_presets(monkeypatch, tmp_path: Path) -> None:
    from helpers import cache, files, modules, plugins

    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(
        plugins,
        "call_plugin_hook",
        lambda plugin_name, hook_name, default=None, **kwargs: default,
    )
    plugin_dir = tmp_path / "plugins" / "_model_config"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text("name: _model_config\n", encoding="utf-8")
    (plugin_dir / "mode_presets_fallback.yaml").write_text(
        """
- name: Default
  chat:
    provider: openrouter
    name: default-chat
    ctx_length: 200000
  utility:
    provider: openrouter
    name: default-utility
  embedding:
    provider: huggingface
    name: default-embedding
""".lstrip(),
        encoding="utf-8",
    )
    (plugin_dir / "default_config.yaml").write_text("model_preset: Default\n", encoding="utf-8")
    (tmp_path / "usr" / "plugins").mkdir(parents=True)
    cache.clear("*(extensions)*")
    cache.clear("*(plugins)*")
    modules.purge_namespace("usr.plugins")


@pytest.mark.asyncio
async def test_model_config_set_persists_auto_detected_ctx(monkeypatch, tmp_path):
    _prepare_presets(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "plugins._model_config.helpers.ctx_length.resolve_ctx_length",
        lambda *_args, **_kwargs: 1_000_000,
    )

    from plugins._model_config.api.model_config_set import ModelConfigSet
    from plugins._model_config.helpers import model_config

    handler = ModelConfigSet(Flask(__name__), threading.Lock())
    result = await handler.process(
        {
            "config": {
                "chat_model": {"provider": "ollama_cloud", "name": "glm-5.3"},
                "utility_model": {"provider": "ollama_cloud", "name": "glm-5.3"},
                "embedding_model": {
                    "provider": "huggingface",
                    "name": "sentence-transformers/all-MiniLM-L6-v2",
                },
            }
        },
        None,
    )

    assert result["ok"] is True
    preset = model_config.resolve_preset("Default")
    assert preset["chat"]["ctx_length"] == 1_000_000
    assert "ctx_length_manual" not in preset["chat"]
    assert preset["utility"]["ctx_length"] == 1_000_000


@pytest.mark.asyncio
async def test_model_config_set_persists_manual_ctx_flag(monkeypatch, tmp_path):
    _prepare_presets(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "plugins._model_config.helpers.ctx_length.resolve_ctx_length",
        lambda *_args, **_kwargs: 1_000_000,
    )

    from plugins._model_config.api.model_config_set import ModelConfigSet
    from plugins._model_config.helpers import model_config

    handler = ModelConfigSet(Flask(__name__), threading.Lock())
    result = await handler.process(
        {
            "config": {
                "chat_model": {
                    "provider": "ollama_cloud",
                    "name": "glm-5.3",
                    "ctx_length": 256000,
                    "ctx_length_manual": True,
                },
                "utility_model": {"provider": "ollama_cloud", "name": "glm-5.3"},
                "embedding_model": {
                    "provider": "huggingface",
                    "name": "sentence-transformers/all-MiniLM-L6-v2",
                },
            }
        },
        None,
    )

    assert result["ok"] is True
    preset = model_config.resolve_preset("Default")
    assert preset["chat"]["ctx_length"] == 256000
    assert preset["chat"]["ctx_length_manual"] is True
    resolved = model_config.get_config()
    assert resolved["chat_model"]["ctx_length"] == 256000
    assert resolved["chat_model"]["ctx_length_manual"] is True
