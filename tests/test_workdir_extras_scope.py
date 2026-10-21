"""The prompt's working-directory tree is the account's own folder, not the shared base (audit 6, M4)."""

from types import SimpleNamespace

import pytest

from agent import LoopData
from extensions.python.message_loop_prompts_after import _75_include_workdir_extras as extras
from helpers import files, projects, runtime, settings


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(runtime, "is_development", lambda: True)
    monkeypatch.setattr(projects, "get_context_project_name", lambda context: None)
    configured = {"workdir_show": True, "workdir_max_depth": 5, "workdir_max_files": 50, "workdir_max_folders": 50,
                  "workdir_max_lines": 200, "workdir_gitignore": "", "workdir_path": "/eagent/usr/workdir"}  # fmt: skip
    monkeypatch.setattr(settings, "get_settings", lambda: configured)
    for owner, name in (("alice", "alice_notes.txt"), ("bob", "bob_secret_plan.txt")):
        (tmp_path / "usr/workdir" / owner).mkdir(parents=True)
        (tmp_path / "usr/workdir" / owner / name).write_text("x")
    return tmp_path


def _agent(owner_id):
    return SimpleNamespace(
        context=SimpleNamespace(owner_id=owner_id),
        read_prompt=lambda name, **kwargs: f"{kwargs['folder']}\n{kwargs['file_structure']}",
    )


@pytest.mark.asyncio
async def test_the_tree_shows_only_the_owners_folder(base):
    loop_data = LoopData()
    await extras.IncludeWorkdirExtras(_agent("alice")).execute(loop_data=loop_data)
    prompt = loop_data.extras_temporary["project_file_structure"]
    assert "alice_notes.txt" in prompt
    assert "bob" not in prompt
    assert prompt.splitlines()[0] == "/eagent/usr/workdir/alice"


@pytest.mark.asyncio
async def test_no_owner_means_no_tree(base):
    loop_data = LoopData()
    await extras.IncludeWorkdirExtras(_agent(None)).execute(loop_data=loop_data)
    assert "project_file_structure" not in loop_data.extras_temporary
