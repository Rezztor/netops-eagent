"""Four endpoints that changed something wrote no audit trail.

Same style as commit 5c56165 for `api/plugins.py`: who and what, never the
content or a secret. Shown by calling each handler with its side effects stubbed
and asserting the events that were missing before.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from flask import Flask


@pytest.fixture
def events(monkeypatch):
    from plugins._audit.helpers import audit

    recorded = []
    monkeypatch.setattr(audit, "emit", lambda event, **fields: recorded.append((event, fields)))
    return recorded


@pytest.mark.asyncio
async def test_backup_create_is_audited(events, monkeypatch, tmp_path):
    from api import backup_create
    from helpers.backup import BackupService

    zip_path = tmp_path / "nightly.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    async def create_backup(self, **kwargs):
        return str(zip_path)

    monkeypatch.setattr(BackupService, "create_backup", create_backup)
    monkeypatch.setattr(backup_create, "save_tmp_chats", lambda: None)

    app = Flask("test_backup_create_audit")
    with app.test_request_context("/api/backup_create", method="POST", json={}):
        from flask import request

        response = await backup_create.BackupCreate(app, threading.Lock()).process(
            {"backup_name": "nightly", "passphrase": "a passphrase of some length", "include_patterns": ["usr/**"], "exclude_patterns": []},
            request,
        )
        # Drain the streamed response so the open handle is not left hanging.
        if hasattr(response, "response"):
            b"".join(response.response)
            response.close()

    assert ("backup.create", {
        "backup_name": "nightly",
        "include_count": 1,
        "exclude_count": 0,
        "include_hidden": True,
    }) in events


@pytest.mark.asyncio
async def test_project_writes_are_audited(events, monkeypatch):
    from api import projects as api
    from helpers import login

    monkeypatch.setattr(login, "has_permission", lambda permission: True)
    monkeypatch.setattr(api.projects, "create_project", lambda name, data: name)
    monkeypatch.setattr(
        api.projects,
        "load_edit_project_data",
        lambda name: {"name": name, "title": name},
    )
    monkeypatch.setattr(api.projects, "delete_project", lambda name: None)
    monkeypatch.setattr(
        api.projects,
        "update_project",
        lambda name, data: {"name": name},
    )
    monkeypatch.setattr(
        api.projects,
        "BasicProjectData",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        api.projects,
        "EditProjectData",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    handler = api.Projects(Flask("test_projects_audit"), threading.Lock())
    await handler.process({"action": "create", "project": {"name": "atlas", "title": "Atlas"}}, None)
    await handler.process({"action": "update", "project": {"name": "atlas", "title": "Atlas 2"}}, None)
    await handler.process({"action": "delete", "name": "atlas"}, None)
    await handler.process({"action": "list"}, None)  # a read is not an event

    assert ("project.create", {"project": "atlas"}) in events
    assert ("project.update", {"project": "atlas"}) in events
    assert ("project.delete", {"project": "atlas"}) in events
    assert not any(event == "project.list" for event, _ in events)


@pytest.mark.asyncio
async def test_chat_export_is_audited_without_the_transcript(events, monkeypatch):
    from api import chat_export

    class Handler(chat_export.ExportChat):
        def use_context(self, ctxid, create_if_not_exists=False):
            return SimpleNamespace(id=ctxid)

    monkeypatch.setattr(
        chat_export.persist_chat,
        "export_json_chat",
        lambda context: '{"secret":"do-not-audit"}',
    )
    handler = Handler(Flask("test_chat_export_audit"), threading.Lock())
    result = await handler.process({"ctxid": "ctx-1"}, None)

    assert result["content"] == '{"secret":"do-not-audit"}'
    assert events == [("chat.export", {"context_id": "ctx-1"})]
    assert "secret" not in str(events)


@pytest.mark.asyncio
async def test_ansible_config_changes_are_audited_without_tokens(events, monkeypatch):
    from plugins._ea_ansible.api import ansible_config as api

    monkeypatch.setattr(api.plugin_helpers, "save_plugin_config", lambda *a, **kw: None)
    monkeypatch.setattr(api.dotenv, "save_dotenv_value", lambda *a, **kw: None)
    monkeypatch.setattr(
        api.plugin_config,
        "get_config",
        lambda: {
            "executor_token_key": "REZZTOR_ANSIBLE_TOKEN",
            "admin_token_key": "REZZTOR_ANSIBLE_APP_ADMIN_TOKEN",
            "read_approval": "every_time",
        },
    )
    monkeypatch.setattr(api.plugin_config, "public_config", lambda: {"executor_url": "http://executor"})
    monkeypatch.setattr(api.plugin_helpers, "get_toggle_state", lambda name: "enabled")

    handler = api.AnsibleConfig(Flask("test_ansible_audit"), threading.Lock())
    await handler.process(
        {
            "action": "save",
            "config": {"executor_url": "http://elsewhere", "job_timeout_seconds": 60},
            "executor_token": "s3cret-job",
            "admin_token": "s3cret-admin",
        },
        None,
    )
    await handler.process({"action": "clear_token"}, None)
    await handler.process({"action": "get"}, None)

    assert (
        "ansible.config.save",
        {
            "changed_keys": ["executor_url", "job_timeout_seconds"],
            "executor_token_updated": True,
            "admin_token_updated": True,
        },
    ) in events
    assert ("ansible.config.clear_token", {}) in events
    assert "s3cret" not in str(events)
    assert not any(event == "ansible.config.get" for event, _ in events)
