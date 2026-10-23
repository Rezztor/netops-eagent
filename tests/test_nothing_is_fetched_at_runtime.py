"""Nothing is fetched at runtime.

That is a rule of this product (CLAUDE.md, CONTRIBUTING.md: no fetching code or
configuration at runtime, no outside service), and an appliance on a bank's
network usually cannot reach the internet anyway. Two places did.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_application_container_does_not_ask_a_package_mirror_for_anything_when_it_starts():
    """`initialize.sh` ran `apt-get update` in the background at every start,
    as root: a call to the distribution's mirrors from every installation,
    each time the container came up."""
    script = (ROOT / "docker/run/fs/exe/initialize.sh").read_text(encoding="utf-8")
    commands = [line.split("#", 1)[0] for line in script.splitlines()]
    for tool in ("apt-get", "apt ", "pip ", "curl ", "wget ", "npm "):
        assert not any(tool in line for line in commands), f"{tool.strip()} runs when the container starts"


def test_a_missing_parser_is_reported_and_not_installed(monkeypatch):
    """The document plugin checks at startup that its parser can be imported.
    When it could not, it ran `uv pip install` against the package index and
    loaded what came back into the web server."""
    from plugins._document_query import hooks

    monkeypatch.setattr(hooks, "has_liteparse", lambda: False)
    monkeypatch.setattr(hooks, "_CHECKED", False)

    assert hooks.ensure_dependencies(raise_on_error=False) is False
    with pytest.raises(RuntimeError, match="requirements.txt"):
        hooks.ensure_dependencies(raise_on_error=True)
    # It has nothing to run an installer with, and names none.
    source = (ROOT / "plugins/_document_query/hooks.py").read_text(encoding="utf-8")
    assert not hasattr(hooks, "subprocess") and "check_call" not in source and "import os" not in source


def test_a_parser_that_is_there_is_all_it_takes(monkeypatch):
    from plugins._document_query import hooks

    monkeypatch.setattr(hooks, "has_liteparse", lambda: True)
    monkeypatch.setattr(hooks, "_CHECKED", False)
    assert hooks.ensure_dependencies() is True


def _whisper(monkeypatch, tmp_path, *, offline):
    """The speech-to-text runtime with a model folder of our own; returns (runtime, loads)."""
    from plugins._whisper_stt.helpers import runtime

    loads = []
    monkeypatch.setattr(runtime.files, "get_abs_path", lambda *parts: str(tmp_path))
    # Set here, not taken from the library: another test file stands a stub in for it.
    monkeypatch.setattr(runtime.whisper, "_MODELS", {"base": "https://models.example/abc/base.pt"}, raising=False)
    monkeypatch.setattr(
        runtime.whisper, "load_model", lambda name, download_root: loads.append(name) or object(), raising=False
    )
    monkeypatch.setattr(runtime, "_model", None)
    monkeypatch.setattr(runtime, "_model_name", "")
    monkeypatch.setattr(runtime.NotificationManager, "send_notification", staticmethod(lambda *a, **k: None))
    if offline is None:
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    else:
        monkeypatch.setenv("HF_HUB_OFFLINE", offline)
    return runtime, loads


@pytest.mark.parametrize("offline", ["1", None])
def test_a_speech_model_that_is_not_there_is_not_downloaded(monkeypatch, tmp_path, offline):
    """The library downloads a model it does not find, from its publisher's
    servers, and it does not know the deployment's switch for that
    (`HF_HUB_OFFLINE`, on unless a deployment turns it off). The model is not in
    the image, and `transcribe` is open to every account: the first request
    from anybody sent the appliance to the internet for 140 MB."""
    import asyncio

    runtime, loads = _whisper(monkeypatch, tmp_path, offline=offline)

    with pytest.raises(RuntimeError, match="nothing is fetched at runtime"):
        asyncio.run(runtime._preload("base"))
    assert loads == []


def test_a_speech_model_that_is_there_is_loaded(monkeypatch, tmp_path):
    import asyncio

    runtime, loads = _whisper(monkeypatch, tmp_path, offline="1")
    (tmp_path / "base.pt").write_bytes(b"model")

    asyncio.run(runtime._preload("base"))
    assert loads == ["base"]


def test_a_deployment_that_chose_to_pull_models_still_can(monkeypatch, tmp_path):
    import asyncio

    runtime, loads = _whisper(monkeypatch, tmp_path, offline="0")

    asyncio.run(runtime._preload("base"))
    assert loads == ["base"]


def _kokoro(monkeypatch, *, offline, config_path: str | None):
    """The text-to-speech runtime; returns (runtime, pipeline_calls)."""
    import sys
    import types

    import huggingface_hub

    from plugins._kokoro_tts.helpers import runtime

    pipelines = []

    class FakePipeline:
        def __init__(self, *args, **kwargs):
            pipelines.append(kwargs)

    kokoro = types.ModuleType("kokoro")
    kokoro.KPipeline = FakePipeline
    monkeypatch.setitem(sys.modules, "kokoro", kokoro)
    monkeypatch.setattr(runtime, "_pipeline", None)
    monkeypatch.setattr(runtime.NotificationManager, "send_notification", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(
        huggingface_hub,
        "try_to_load_from_cache",
        lambda repo_id, filename: config_path,
    )
    if offline is None:
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    else:
        monkeypatch.setenv("HF_HUB_OFFLINE", offline)
    return runtime, pipelines


@pytest.mark.parametrize("offline", ["1", None])
def test_a_speech_synthesis_model_that_is_not_there_is_not_downloaded(monkeypatch, offline):
    """Kokoro's pipeline downloads config and weights through the Hugging Face
    hub on first use. The deployment switch is on unless turned off; refuse
    before constructing the pipeline when the model is not already cached."""
    import asyncio

    runtime, pipelines = _kokoro(monkeypatch, offline=offline, config_path=None)

    with pytest.raises(RuntimeError, match="nothing is fetched at runtime"):
        asyncio.run(runtime._preload())
    assert pipelines == []


def test_a_speech_synthesis_model_that_is_there_is_loaded(monkeypatch, tmp_path):
    import asyncio

    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    runtime, pipelines = _kokoro(monkeypatch, offline="1", config_path=str(config))

    asyncio.run(runtime._preload())
    assert pipelines == [{"lang_code": "a", "repo_id": runtime.DEFAULT_REPO_ID}]


def test_a_deployment_that_chose_to_pull_speech_synthesis_models_still_can(monkeypatch):
    import asyncio

    runtime, pipelines = _kokoro(monkeypatch, offline="0", config_path=None)

    asyncio.run(runtime._preload())
    assert pipelines == [{"lang_code": "a", "repo_id": runtime.DEFAULT_REPO_ID}]


def test_the_document_parser_has_its_data_in_the_image_and_does_not_fetch_it():
    """Seen in the built image with no network, 2026-10-11: an HTML document could not be parsed, and the parser
    had first asked raw.githubusercontent.com for two NLTK data sets. With a network it would have had them from
    there, on every installation, at the first document. Since unstructured 0.24 it is one spaCy model, which
    the parser installs from github.com when it is missing: the one it asks for is a requirement, so it is in
    the image and in this environment, and the image build loads it."""
    spacy = pytest.importorskip("spacy")
    tokenize = pytest.importorskip("unstructured.nlp.tokenize")
    wheel = f"{tokenize._SPACY_MODEL_NAME}-{tokenize._SPACY_MODEL_VERSION}-py3-none-any.whl"
    assert wheel in (ROOT / "requirements.txt").read_text(encoding="utf-8"), f"{wheel} is not a requirement"
    assert spacy.load(tokenize._SPACY_MODEL_NAME).meta["version"] == tokenize._SPACY_MODEL_VERSION
    install = (ROOT / "docker/run/fs/ins/install_eagent.sh").read_text(encoding="utf-8")
    assert "spacy.load(tokenize._SPACY_MODEL_NAME)" in install


@pytest.mark.parametrize("module", ["models", "helpers.litellm_transport"])
def test_litellm_is_told_to_use_its_bundled_price_list_wherever_it_is_first_imported(module):
    """`python run_ui.py` without Compose has no environment line to set this,
    and LiteLLM reads it while it is being imported: the default is a download
    from GitHub."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}
    # LiteLLM reads the variable as it is imported, so record it at that moment.
    code = (
        "import os, sys\n"
        "class Spy:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'litellm':\n"
        "            print(os.environ.get('LITELLM_LOCAL_MODEL_COST_MAP'))\n"
        "sys.meta_path.insert(0, Spy())\n"
        f"import {module}\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().splitlines()[:1] == ["True"], out.stderr[-500:]
