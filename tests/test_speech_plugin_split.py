from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from helpers import plugins, settings


def test_builtin_speech_plugins_are_discoverable_and_toggleable() -> None:
    discovered = {
        item.name: item
        for item in plugins.get_enhanced_plugins_list(
            custom=True,
            builtin=True,
            plugin_names=["_kokoro_tts", "_whisper_stt"],
        )
    }

    assert "_kokoro_tts" in discovered
    assert "_whisper_stt" in discovered

    assert discovered["_kokoro_tts"].always_enabled is False
    assert discovered["_whisper_stt"].always_enabled is False
    assert "agent" in discovered["_kokoro_tts"].settings_sections
    assert "agent" in discovered["_whisper_stt"].settings_sections


def test_legacy_core_speech_artifacts_are_removed() -> None:
    removed_paths = [
        "api/synthesize.py",
        "api/transcribe.py",
        "helpers/kokoro_tts.py",
        "helpers/whisper.py",
        "webui/components/chat/speech/speech-store.js",
        "webui/components/settings/agent/speech.html",
        "webui/components/settings/speech/microphone-setting-store.js",
        "webui/components/settings/speech/microphone.html",
        "webui/css/speech.css",
        "webui/js/speech_browser.js",
    ]

    for relative_path in removed_paths:
        assert not (PROJECT_ROOT / relative_path).exists(), relative_path


def test_core_settings_no_longer_expose_legacy_speech_keys() -> None:
    defaults = settings.get_default_settings()
    output = settings.convert_out(defaults)

    legacy_keys = {
        "tts_kokoro",
        "stt_model_size",
        "stt_language",
        "stt_silence_threshold",
        "stt_silence_duration",
        "stt_waiting_timeout",
    }

    assert legacy_keys.isdisjoint(defaults.keys())
    assert legacy_keys.isdisjoint(output["settings"].keys())
    assert "stt_models" not in output["additional"]


