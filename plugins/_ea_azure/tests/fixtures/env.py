"""One place that gives a test a private instance: a temp base directory for the plugin's config, an in-memory
`usr/.env`, and the plugin toggle. No network, no database, no real credential."""

from __future__ import annotations

from helpers import dotenv, files
from helpers import plugins as plugin_helpers


def install(monkeypatch, tmp_path, enabled: bool = True) -> dict:
    """Returns the in-memory `usr/.env` as a dict, so a test can read what was stored."""
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    for sub in ("usr", "plugins/_ea_azure", "usr/plugins/_ea_azure"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(plugin_helpers, "get_toggle_state", lambda name: "enabled" if enabled else "disabled")
    env: dict = {}
    monkeypatch.setattr(dotenv, "get_dotenv_value", lambda key, default=None: env.get(key) or default)
    monkeypatch.setattr(dotenv, "save_dotenv_value", lambda key, value, reload_env=True: env.__setitem__(key, value))
    return env
