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

from plugins._model_config.api.model_search import ModelSearch


def _handler():
    return ModelSearch(Flask(__name__), threading.Lock())


def test_model_search_parses_openai_style_data():
    handler = _handler()

    assert handler._parse({"data": [{"id": "gpt-4.1"}, {"id": "gpt-4o-mini"}]}, "openai") == [
        {"name": "gpt-4.1"},
        {"name": "gpt-4o-mini"},
    ]


def test_model_search_parses_google_models_and_strips_prefix():
    handler = _handler()

    assert handler._parse({"models": [{"name": "models/gemini-pro"}]}, "google") == [
        {"name": "gemini-pro"}
    ]


def test_model_search_parses_ollama_models():
    handler = _handler()

    assert handler._parse({"models": [{"name": "llama3.2"}]}, "ollama") == [{"name": "llama3.2"}]


def test_model_search_builds_ollama_running_models_url():
    handler = _handler()

    assert handler._ollama_ps_url("http://host.docker.internal:11434/api/tags") == (
        "http://host.docker.internal:11434/api/ps"
    )


def test_model_search_parses_list_style_dicts_and_strings():
    handler = _handler()

    assert handler._parse([{"id": "mistral-large"}, "mistral-small"], "openai") == [
        {"name": "mistral-large"},
        {"name": "mistral-small"},
    ]


def test_model_search_parses_openai_context_length():
    handler = _handler()

    assert handler._parse(
        {"data": [{"id": "glm-5.3", "context_length": 1_000_000}]},
        "openai",
    ) == [{"name": "glm-5.3", "ctx_length": 1_000_000}]


def test_model_search_parses_vllm_max_model_len():
    handler = _handler()

    assert handler._parse(
        {"data": [{"id": "local-llama", "max_model_len": 131072}]},
        "openai",
    ) == [{"name": "local-llama", "ctx_length": 131072}]


def test_model_search_parses_google_input_token_limit():
    handler = _handler()

    assert handler._parse(
        {"models": [{"name": "models/gemini-pro", "inputTokenLimit": 1048576}]},
        "google",
    ) == [{"name": "gemini-pro", "ctx_length": 1048576}]


def test_model_search_parses_ollama_details_and_model_info_context():
    handler = _handler()

    assert handler._parse(
        {"models": [{"name": "llama3.2", "details": {"context_length": 131072}}]},
        "ollama",
    ) == [{"name": "llama3.2", "ctx_length": 131072}]
    assert handler._parse(
        {
            "models": [
                {"name": "qwen3", "model_info": {"qwen3.context_length": 32768}}
            ]
        },
        "ollama",
    ) == [{"name": "qwen3", "ctx_length": 32768}]


def test_model_search_resolves_v1_base_without_duplicate_v1():
    handler = _handler()

    url, fmt = handler._resolve_url({"endpoint_url": "/v1/models"}, "http://host.docker.internal:1234/v1")

    assert url == "http://host.docker.internal:1234/v1/models"
    assert fmt == "openai"


def test_model_search_omits_auth_header_for_omlx_placeholder_key():
    handler = _handler()

    assert handler._build_headers("omlx", "omlx", {}) == {}


def test_model_search_omits_auth_header_for_local_placeholder_keys():
    handler = _handler()

    assert handler._build_headers("llama_cpp", "llama-cpp", {}) == {}
    assert handler._build_headers("vllm", "vllm", {}) == {}
    assert handler._build_headers("llama_cpp", "real-local-key", {}) == {
        "Authorization": "Bearer real-local-key"
    }


def test_model_search_filters_non_chat_models():
    handler = _handler()

    assert handler._filter_models(
        [{"name": "gpt-4.1"}, {"name": "text-embedding-3-small"}, {"name": "gpt-image-1"}],
        "chat",
    ) == [{"name": "gpt-4.1"}]


def test_model_search_falls_back_to_litellm_registry(monkeypatch):
    handler = _handler()
    fake_litellm = types.SimpleNamespace(
        models_by_provider={"openai": {"openai/gpt-4.1", "text-embedding-3-small"}}
    )
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    assert handler._litellm_fallback("openai", {"litellm_provider": "openai"}) == [
        {"name": "gpt-4.1"}
    ]


def test_resolve_ctx_length_prefers_payload_over_litellm(monkeypatch):
    from plugins._model_config.helpers.ctx_length import resolve_ctx_length

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        types.SimpleNamespace(model_cost={"openai/glm-5.3": {"max_input_tokens": 1_000_000}}),
    )

    assert resolve_ctx_length(
        "ollama_cloud",
        "glm-5.3",
        litellm_provider="openai",
        payload_ctx=8192,
    ) == 8192


def test_resolve_ctx_length_reads_litellm_max_input_tokens(monkeypatch):
    from plugins._model_config.helpers.ctx_length import resolve_ctx_length

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        types.SimpleNamespace(
            model_cost={"openai/glm-5.3": {"max_input_tokens": 1_000_000}},
        ),
    )

    assert resolve_ctx_length(
        "ollama_cloud",
        "glm-5.3",
        litellm_provider="openai",
    ) == 1_000_000


def test_resolve_ctx_length_matches_litellm_key_suffix(monkeypatch):
    from plugins._model_config.helpers.ctx_length import resolve_ctx_length

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        types.SimpleNamespace(
            model_cost={"zai/glm-5.3": {"max_input_tokens": 1_000_000}},
        ),
    )

    assert resolve_ctx_length(
        "ollama_cloud",
        "glm-5.3",
        litellm_provider="openai",
    ) == 1_000_000


@pytest.mark.asyncio
async def test_model_search_returns_named_objects_and_overlays_litellm_ctx(monkeypatch):
    handler = _handler()

    async def fake_fetch(*_args, **_kwargs):
        return [{"name": "other"}, {"name": "glm-5.3"}], "provider_endpoint", ""

    monkeypatch.setattr(handler, "_fetch_models", fake_fetch)
    monkeypatch.setattr(
        "plugins._model_config.api.model_search.resolve_ctx_length",
        lambda _provider, name, **_kwargs: 1_000_000 if name == "glm-5.3" else None,
    )

    result = await handler.process({"provider": "ollama_cloud"}, None)

    assert result["models"] == [
        {"name": "glm-5.3", "ctx_length": 1_000_000},
        {"name": "other"},
    ]
    assert result["source"] == "provider_endpoint"


@pytest.mark.asyncio
async def test_model_search_empty_query_returns_full_catalogue(monkeypatch):
    handler = _handler()

    async def fake_fetch(*_args, **_kwargs):
        return [{"name": "glm-5.3"}, {"name": "llama4"}], "provider_endpoint", ""

    monkeypatch.setattr(handler, "_fetch_models", fake_fetch)
    monkeypatch.setattr(
        "plugins._model_config.api.model_search.resolve_ctx_length",
        lambda *_args, **_kwargs: None,
    )

    full = await handler.process({"provider": "ollama_cloud", "query": ""}, None)
    filtered = await handler.process({"provider": "ollama_cloud", "query": "glm"}, None)

    assert [entry["name"] for entry in full["models"]] == ["glm-5.3", "llama4"]
    assert [entry["name"] for entry in filtered["models"]] == ["glm-5.3"]


@pytest.mark.asyncio
async def test_model_search_asks_ollama_cloud_for_the_window_its_list_leaves_out():
    asked = []

    class Reply:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body

        def json(self):
            return self._body

    class Client:
        async def post(self, url, headers=None, json=None):
            asked.append((url, headers, json))
            if json["model"] == "gone":
                return Reply(404, {})
            if json["model"] == "broken":
                raise RuntimeError("no route")
            return Reply(200, {"model_info": {"deepseek_v41.context_length": 1_048_576}})

    entries = [
        {"name": "deepseek-v4.1-flash"},
        {"name": "known", "ctx_length": 8192},
        {"name": "gone"},
        {"name": "broken"},
    ]
    await ModelSearch._add_ollama_show_ctx(
        Client(), "https://ollama.com/v1/models", {"Authorization": "Bearer k"}, entries
    )

    assert entries == [
        {"name": "deepseek-v4.1-flash", "ctx_length": 1_048_576},
        {"name": "known", "ctx_length": 8192},
        {"name": "gone"},
        {"name": "broken"},
    ]
    assert {url for url, _headers, _json in asked} == {"https://ollama.com/api/show"}
    assert [body["model"] for _url, _headers, body in asked] == [
        "deepseek-v4.1-flash",
        "gone",
        "broken",
    ]
    assert all(headers == {"Authorization": "Bearer k"} for _url, headers, _json in asked)


@pytest.mark.asyncio
async def test_model_search_leaves_a_base_without_v1_alone():
    class Client:
        async def post(self, *_args, **_kwargs):
            raise AssertionError("no request expected")

    entries = [{"name": "m"}]
    await ModelSearch._add_ollama_show_ctx(Client(), "https://proxy.example/models", {}, entries)

    assert entries == [{"name": "m"}]
