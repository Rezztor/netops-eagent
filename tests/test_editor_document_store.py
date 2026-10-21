from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import types
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._editor.helpers import (
    document_store,
    markdown_sessions as editor_markdown_sessions,
    open_files_context,
)
from plugins._editor.api.editor_session import EditorSession


@pytest.fixture
def editor_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    backups = state / "backups"
    workdir = tmp_path / "workdir"
    documents = workdir / "documents"
    projects_parent = tmp_path / "projects"

    monkeypatch.setattr(document_store, "STATE_DIR", state)
    monkeypatch.setattr(document_store, "DB_PATH", state / "documents.sqlite3")
    monkeypatch.setattr(document_store, "BACKUP_DIR", backups)
    monkeypatch.setattr(document_store, "WORKDIR", workdir)
    monkeypatch.setattr(document_store, "DOCUMENTS_DIR", documents)
    settings_helpers = types.SimpleNamespace(get_settings=lambda: {"workdir_path": str(workdir)})
    project_helpers = types.SimpleNamespace(
        get_context_project_name=lambda context: None,
        get_project_folder=lambda name: str(projects_parent / name),
        get_projects_parent_folder=lambda: str(projects_parent),
    )
    monkeypatch.setattr(document_store, "_settings", lambda: settings_helpers)
    monkeypatch.setattr(document_store, "_projects", lambda: project_helpers)
    monkeypatch.setattr(
        editor_markdown_sessions,
        "_manager",
        editor_markdown_sessions.MarkdownSessionManager(),
        raising=False,
    )

    workdir.mkdir(parents=True, exist_ok=True)
    documents.mkdir(parents=True, exist_ok=True)
    projects_parent.mkdir(parents=True, exist_ok=True)
    document_store.ensure_dirs()
    return types.SimpleNamespace(
        state=state,
        backups=backups,
        workdir=workdir,
        documents=documents,
        projects_parent=projects_parent,
        project_helpers=project_helpers,
    )


def test_document_store_create_defaults_to_markdown(editor_state):
    doc = document_store.create_document("document", "Research Note", content="A precise note.")

    assert doc["extension"] == "md"
    assert Path(doc["path"]).parent == editor_state.workdir
    assert Path(doc["path"]).read_text(encoding="utf-8").startswith("# Research Note")


def test_a_request_cannot_open_the_application_directory(editor_state, monkeypatch):
    """`source: "file-browser"` was a body field, and it switched the whole base
    directory on: any signed-in account could open and then save every .md and
    .txt the application has -- the system prompts, the shared skills and
    knowledge, and other accounts' text files. The file browser is rooted at the
    caller's own directory now, so nothing it lists needs that."""
    runtime_root = editor_state.state.parent / "runtime-root"
    runtime_root.mkdir()
    path = runtime_root / "AGENTS.md"
    path.write_text("# Runtime Instructions\n", encoding="utf-8")
    monkeypatch.setattr(document_store.files, "get_base_dir", lambda: str(runtime_root))
    handler = EditorSession(app=None, thread_lock=None)
    request = types.SimpleNamespace(headers={}, host_url="http://localhost/")

    for body in ({}, {"source": "file-browser"}):
        blocked = asyncio.run(handler.process({"action": "open", "path": str(path), **body}, request))
        assert blocked["ok"] is False
        assert "active project or workdir" in blocked["error"]


@pytest.mark.parametrize(
    ("kind", "title", "fmt", "expected_name"),
    [
        ("document", "real-chat-canvas-smoke.md", "md", "real-chat-canvas-smoke.md"),
        ("document", "Board Memo.ODT", "odt", "Board Memo.odt"),
        ("spreadsheet", "Budget.ods", "ods", "Budget.ods"),
        ("presentation", "Roadmap.odp", "odp", "Roadmap.odp"),
    ],
)
def test_create_document_does_not_duplicate_matching_extension(editor_state, kind, title, fmt, expected_name):
    doc = document_store.create_document(kind, title, fmt, content="Smoke")

    assert Path(doc["path"]).name == expected_name


def test_blank_docx_includes_editable_body_paragraph(editor_state):
    doc = document_store.create_document("document", "Blank Memo", "docx", "")
    with zipfile.ZipFile(doc["path"]) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
        root = ET.fromstring(xml)

    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    assert len(list(root.iter(f"{{{word_ns}}}p"))) >= 2
    assert 'xml:space="preserve">&#160;</w:t>' in xml


def test_project_scoped_creation_uses_active_project_root(editor_state, monkeypatch):
    project_root = editor_state.projects_parent / "apollo"
    project_root.mkdir(parents=True, exist_ok=True)
    context = object()
    agent_module = types.SimpleNamespace(
        AgentContext=types.SimpleNamespace(get=staticmethod(lambda context_id: context))
    )

    monkeypatch.setitem(sys.modules, "agent", agent_module)
    monkeypatch.setattr(editor_state.project_helpers, "get_context_project_name", lambda active_context: "apollo")
    monkeypatch.setattr(editor_state.project_helpers, "get_project_folder", lambda name: str(project_root))

    markdown = document_store.create_document("document", "Project Note", "md", "Scoped.", context_id="ctx-project")
    odt = document_store.create_document("document", "Project Memo", "odt", "Scoped.", context_id="ctx-project")

    assert Path(markdown["path"]).parent == project_root
    assert Path(odt["path"]).parent == project_root / "documents"


def test_non_project_creation_uses_configured_workdir(editor_state):
    markdown = document_store.create_document("document", "Workdir Note", content="Plain.")
    spreadsheet = document_store.create_document("spreadsheet", "Workdir Sheet", "ods", "Name,Value")

    assert markdown["extension"] == "md"
    assert Path(markdown["path"]).parent == editor_state.workdir
    assert Path(spreadsheet["path"]).parent == editor_state.documents


def test_editor_open_files_are_scoped_to_active_context(editor_state):
    first = document_store.create_document("document", "First Editor Note", "md", "First private body.")
    second = document_store.create_document("document", "Second Editor Note", "md", "Second private body.")
    manager = editor_markdown_sessions.get_manager()

    first_session = manager.open(first, context_id="ctx-a")
    second_session = manager.open(second, context_id="ctx-b")
    manager.input(first_session["session_id"], text="Unsaved ctx-a text")
    reopened_first = manager.open(first, context_id="ctx-a")

    ctx_a_files = manager.list_open("ctx-a")
    ctx_b_files = manager.list_open("ctx-b")
    prompt_context = open_files_context.build_context("ctx-a")

    assert reopened_first["session_id"] == first_session["session_id"]
    assert reopened_first["text"] == "Unsaved ctx-a text"
    assert [item["file_id"] for item in ctx_a_files] == [first["file_id"]]
    assert [item["file_id"] for item in ctx_b_files] == [second["file_id"]]
    assert ctx_a_files[0]["dirty"] is True
    assert ctx_a_files[0]["active"] is True
    assert ctx_a_files[0]["open_sessions"] == 1
    assert "First Editor Note.md" in prompt_context
    assert "Second Editor Note.md" not in prompt_context
    assert "First private body" not in prompt_context
    assert "Unsaved ctx-a text" not in prompt_context


def test_markdown_save_tracks_version_history(editor_state):
    doc = document_store.create_document("document", "Versioned", "md", "First")
    updated = document_store.write_markdown(doc["file_id"], "# Versioned\n\nSecond\n")
    history = document_store.version_history(doc["file_id"])

    assert updated["version"] == 2
    assert history
    assert Path(updated["path"]).read_text(encoding="utf-8").endswith("Second\n")


def test_document_path_update_preserves_file_id_after_rename(editor_state):
    doc = document_store.create_document("document", "Rename Me", "md", "Body")
    original = Path(doc["path"])
    renamed = original.with_name("Renamed.md")
    original.rename(renamed)

    updated = document_store.update_document_path(doc["file_id"], renamed)

    assert updated["file_id"] == doc["file_id"]
    assert updated["basename"] == "Renamed.md"
    assert updated["path"] == str(renamed)
    assert document_store.get_document(doc["file_id"])["path"] == str(renamed)


def test_document_rename_materializes_missing_markdown_with_editor_text(editor_state):
    doc = document_store.create_document("document", "Unsaved Draft", "md", "Seed")
    original = Path(doc["path"])
    original.unlink()
    renamed = original.with_name("Renamed Draft.md")

    updated = document_store.rename_document(
        doc["file_id"],
        renamed,
        content="# Renamed Draft\n\nCanvas text",
    )

    assert updated["file_id"] == doc["file_id"]
    assert updated["basename"] == "Renamed Draft.md"
    assert updated["path"] == str(renamed)
    assert renamed.read_text(encoding="utf-8") == "# Renamed Draft\n\nCanvas text"


def test_document_rename_saves_dirty_markdown_and_removes_original(editor_state):
    doc = document_store.create_document("document", "Dirty Rename", "md", "Old")
    original = Path(doc["path"])
    renamed = original.with_name("Clean Rename.md")

    updated = document_store.rename_document(
        doc["file_id"],
        renamed,
        content="# Clean Rename\n\nFresh text",
    )

    assert updated["version"] == 2
    assert not original.exists()
    assert renamed.read_text(encoding="utf-8") == "# Clean Rename\n\nFresh text"


def test_text_session_save_as_creates_new_file_without_mutating_original(editor_state):
    manager = editor_markdown_sessions.MarkdownSessionManager()
    doc = document_store.create_document("document", "Original Note", "md", "# Original Note\n")
    original = Path(doc["path"])
    session = manager.open(doc, context_id="ctx-a")
    target = editor_state.workdir / "notes" / "Saved Copy.txt"

    result = manager.save_as(
        session["session_id"],
        str(target),
        text="Saved Copy\n\nExact body\n",
    )

    saved_session = manager._sessions[session["session_id"]]
    assert result["ok"] is True
    assert result["document"]["file_id"] != doc["file_id"]
    assert result["previous_file_id"] == doc["file_id"]
    assert saved_session.file_id == result["document"]["file_id"]
    assert saved_session.path == str(target)
    assert saved_session.extension == "txt"
    assert saved_session.dirty is False
    assert original.read_text(encoding="utf-8") == "# Original Note"
    assert target.read_text(encoding="utf-8") == "Saved Copy\n\nExact body\n"


def test_editor_session_opens_and_saves_txt_documents(editor_state):
    manager = editor_markdown_sessions.MarkdownSessionManager()
    doc = document_store.create_document("document", "Plain Note", "txt", "First line")
    session = manager.open(doc, context_id="ctx-a")

    assert session["extension"] == "txt"
    assert session["text"] == "First line"

    result = manager.save(session["session_id"], text="Second line\n")

    assert result["ok"] is True
    assert result["document"]["extension"] == "txt"
    assert Path(result["document"]["path"]).read_text(encoding="utf-8") == "Second line\n"


def test_refresh_open_markdown_session_reloads_external_file_edits(editor_state):
    manager = editor_markdown_sessions.MarkdownSessionManager()
    doc = document_store.create_document("document", "External Refresh", "md", "First")
    session = manager.open(doc, context_id="ctx-a")

    Path(doc["path"]).write_text("# External Refresh\n\nSecond\n", encoding="utf-8")
    refreshed = manager.open(doc, context_id="ctx-a", refresh=True)

    assert refreshed["session_id"] == session["session_id"]
    assert refreshed["text"] == "# External Refresh\n\nSecond\n"
    assert manager._sessions[session["session_id"]].dirty is False


def test_refresh_open_markdown_session_preserves_dirty_editor_text(editor_state):
    manager = editor_markdown_sessions.MarkdownSessionManager()
    doc = document_store.create_document("document", "Dirty External Refresh", "md", "First")
    session = manager.open(doc, context_id="ctx-a")
    manager.input(session["session_id"], text="Unsaved editor text")

    Path(doc["path"]).write_text("External disk text\n", encoding="utf-8")
    refreshed = manager.open(doc, context_id="ctx-a", refresh=True)

    assert refreshed["text"] == "Unsaved editor text"
    assert manager._sessions[session["session_id"]].dirty is True


def test_external_text_editor_mutation_refreshes_clean_open_markdown_session(editor_state):
    manager = editor_markdown_sessions.MarkdownSessionManager()
    doc = document_store.create_document("document", "Synced External Edit", "md", "First")
    session = manager.open(doc, context_id="ctx-a")

    Path(doc["path"]).write_text("# Synced External Edit\n\nSecond\n", encoding="utf-8")
    result = manager.sync_external_file_mutations([doc["path"]])

    assert result["matched"] == 1
    assert manager._sessions[session["session_id"]].text == "# Synced External Edit\n\nSecond\n"
    assert manager._sessions[session["session_id"]].dirty is False
    assert manager._sessions[session["session_id"]].external_modified is False


def test_external_text_editor_mutation_marks_dirty_markdown_session_pending(editor_state):
    manager = editor_markdown_sessions.MarkdownSessionManager()
    doc = document_store.create_document("document", "Dirty Synced External Edit", "md", "First")
    session = manager.open(doc, context_id="ctx-a")
    manager.input(session["session_id"], text="Unsaved editor text")

    Path(doc["path"]).write_text("External disk text\n", encoding="utf-8")
    result = manager.sync_external_file_mutations([doc["path"]])

    dirty_session = manager._sessions[session["session_id"]]
    assert result["matched"] == 1
    assert dirty_session.text == "Unsaved editor text"
    assert dirty_session.dirty is True
    assert dirty_session.external_modified is True


def test_markdown_editor_save_rejects_stale_canvas_overwrite(editor_state):
    manager = editor_markdown_sessions.MarkdownSessionManager()
    doc = document_store.create_document("document", "Stale Save Guard", "md", "First")
    session = manager.open(doc, context_id="ctx-a")

    Path(doc["path"]).write_text("# Stale Save Guard\n\nExternal newer text\n", encoding="utf-8")
    result = manager.save(session["session_id"], text="# Stale Save Guard\n\nOlder canvas text\n")

    assert result["ok"] is False
    assert result["code"] == "external_change_conflict"
    assert "External newer text" in Path(doc["path"]).read_text(encoding="utf-8")
    assert "Older canvas text" not in Path(doc["path"]).read_text(encoding="utf-8")


def test_markdown_session_rejects_office_binaries(editor_state):
    manager = editor_markdown_sessions.MarkdownSessionManager()
    doc = document_store.create_document("document", "Desktop Only", "odt", "Native text")

    with pytest.raises(ValueError, match=r"\.odt is not supported"):
        manager.open(doc)


def test_run_entrypoint_launches_the_app_directly():
    """run_eagent.sh must not reintroduce a self-updating bootstrap."""
    script = (PROJECT_ROOT / "docker" / "run" / "fs" / "exe" / "run_eagent.sh").read_text(
        encoding="utf-8"
    )
    # Ignore comments: the script explains what it replaced, so the token appears
    # in prose. What matters is that no executable line invokes it.
    executable = "\n".join(
        line for line in script.splitlines() if not line.strip().startswith("#")
    )
    assert "self_update_manager" not in executable
    assert "prepare.py --dockerized=true" in executable
    assert "run_ui.py" in executable
