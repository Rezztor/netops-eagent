"""Two partial settings saves at once must both land.

`set_settings_delta` locked the row to read and merge, then wrote in a second
transaction after the lock was gone. A second save could read the row in that
gap, merge into the stale document and write it back over the first save.
"""

from __future__ import annotations

import threading


def test_concurrent_partial_saves_keep_both_changes(database, tmp_path, monkeypatch):
    from helpers import dotenv, files, secrets, settings
    from testsupport import build_instance_root

    root = build_instance_root(tmp_path)
    monkeypatch.setattr(files, "_base_dir", root)
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(tmp_path / "dotenv"))
    monkeypatch.setattr(secrets.SecretsManager, "_instances", {})
    monkeypatch.setattr(settings, "_settings", None)
    monkeypatch.setattr(settings, "_apply_settings", lambda *_args, **_kwargs: None)
    settings.set_settings_delta({"mcp_client_init_timeout": 10})

    # The first save waits before writing until the second has read the row (or
    # a second has passed, which is what happens when the lock holds it back).
    first_locked, second_read = threading.Event(), threading.Event()
    real_write = settings._write_settings_file
    real_load = settings._load_sensitive_settings

    def write(document, *args, **kwargs):
        if document.get("mcp_client_init_timeout") == 11:
            second_read.wait(1.0)
        return real_write(document, *args, **kwargs)

    def load(document):
        {"first": first_locked, "second": second_read}.get(threading.current_thread().name, threading.Event()).set()
        return real_load(document)

    monkeypatch.setattr(settings, "_write_settings_file", write)
    monkeypatch.setattr(settings, "_load_sensitive_settings", load)

    first = threading.Thread(target=settings.set_settings_delta, args=({"mcp_client_init_timeout": 11},), name="first")
    second = threading.Thread(target=settings.set_settings_delta, args=({"mcp_servers": "[]"},), name="second")
    first.start()
    assert first_locked.wait(10)  # the first holds the row lock
    second.start()
    first.join(10)
    second.join(10)

    stored = settings.reload_settings()
    assert stored["mcp_client_init_timeout"] == 11
    assert stored["mcp_servers"] == "[]"
