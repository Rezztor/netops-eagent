"""A partial settings save must not empty what it did not mention.

The stored document never holds the passwords or the secrets store -- those live
in `usr/.env` and `usr/secrets.env` -- so it reads them as empty.
`set_settings_delta` merged into that document and saved the result, which wrote
the empty values over the real ones: pressing "apply" on the MCP servers panel
cleared the SMTP, RFC and root passwords and every stored secret.
"""

from __future__ import annotations


def test_a_partial_save_keeps_the_passwords_and_the_secrets_store(database, tmp_path, monkeypatch):
    from helpers import dotenv, files, secrets, settings
    from testsupport import build_instance_root

    root = build_instance_root(tmp_path)
    monkeypatch.setattr(files, "_base_dir", root)
    env_file = tmp_path / "dotenv"
    monkeypatch.setattr(dotenv, "get_dotenv_file_path", lambda: str(env_file))
    monkeypatch.setattr(secrets.SecretsManager, "_instances", {})
    monkeypatch.setattr(settings, "_settings", None)
    monkeypatch.setattr(settings, "_apply_settings", lambda *_args, **_kwargs: None)
    for key in (dotenv.KEY_RFC_PASSWORD, dotenv.KEY_SMTP_PASSWORD, dotenv.KEY_ROOT_PASSWORD):
        monkeypatch.setenv(key, "kept")
    files.write_file(secrets.DEFAULT_SECRETS_FILE, "DEVICE_TOKEN=kept\n")

    settings.set_settings_delta({"mcp_servers": "[]"})

    assert dotenv.get_dotenv_value(dotenv.KEY_SMTP_PASSWORD) == "kept"
    assert dotenv.get_dotenv_value(dotenv.KEY_RFC_PASSWORD) == "kept"
    stored = secrets.SecretsManager.get_instance().parse_env_content(
        files.read_file(secrets.DEFAULT_SECRETS_FILE)
    )
    assert stored == {"DEVICE_TOKEN": "kept"}
    assert settings.get_settings()["mcp_servers"] == "[]"
