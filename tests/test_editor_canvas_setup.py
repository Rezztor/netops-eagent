from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read(*parts: str) -> str:
    return (PROJECT_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_plugin_owned_runtime_state_paths_are_declared():
    editor_documents = read("plugins", "_editor", "helpers", "document_store.py")

    assert 'PLUGIN_NAME = "_editor"' in editor_documents
    assert 'STATE_DIR = Path(files.get_abs_path("usr", "plugins", PLUGIN_NAME, "documents"))' in editor_documents


def test_skill_catalog_boundaries_are_static_guarded():
    # The connector's skills_list/skills_delete assertions went with the
    # _ea_connector plugin; the helpers/skills.py guarantees they relied on
    # still hold and are still worth pinning.
    skills_py = read("helpers", "skills.py")

    assert "RENAMED_SKILLS" not in skills_py
    assert "RENAMED_SKILL_PATHS" not in skills_py
    assert "_migrate_skill_name" not in skills_py
    assert "_migrate_skill_path" not in skills_py
    assert "Built-in plugin skills cannot be deleted" in skills_py
