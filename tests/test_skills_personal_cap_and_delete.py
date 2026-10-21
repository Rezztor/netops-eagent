"""The personal library is capped where it is read, and delete takes one skill (audit 6, L4, L6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import runtime, skills


def _skill(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\nBody\n", encoding="utf-8")
    return folder


def test_discovery_reads_no_more_personal_skills_than_the_cap(tmp_path, monkeypatch):
    from plugins._ea_auto_skills.helpers import config as auto_skills_config

    personal, shared = tmp_path / "personal", tmp_path / "shared"
    for n in range(5):
        _skill(personal, f"mine-{n}")
    _skill(shared, "team-skill")
    monkeypatch.setattr(skills, "get_personal_skills_root", lambda: str(personal))
    monkeypatch.setattr(skills, "get_skill_roots", lambda agent=None: [str(personal), str(shared)])
    monkeypatch.setattr(auto_skills_config, "get_config", lambda agent=None: {"max_personal_skills": 2})

    names = [s.name for s in skills.list_skills()]

    assert names == ["mine-0", "mine-1", "team-skill"]


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "usr" / "skills"
    monkeypatch.setattr(runtime, "is_development", lambda: False)
    monkeypatch.setattr(skills, "get_skill_roots", lambda agent=None: [str(root)])
    return root


def test_delete_refuses_the_root_itself(library):
    _skill(library, "one")
    with pytest.raises(ValueError):
        skills.delete_skill(str(library))
    assert (library / "one" / "SKILL.md").exists()


def test_delete_refuses_a_folder_that_is_not_a_skill(library):
    _skill(library / "custom", "two")
    with pytest.raises(ValueError):
        skills.delete_skill(str(library / "custom"))
    assert (library / "custom" / "two" / "SKILL.md").exists()


def test_delete_removes_one_skill(library):
    keep, gone = _skill(library, "keep"), _skill(library, "gone")
    skills.delete_skill(str(gone))
    assert not gone.exists() and keep.exists()
