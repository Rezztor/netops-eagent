"""Importing a skills pack: what a preview may touch, where a pack may land, what is left behind."""

import zipfile
from pathlib import Path

import pytest

from helpers import files, skills_import


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    return tmp_path


def _pack(folder: Path, body: str = "new") -> Path:
    pack = folder / "pack.zip"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("greeter/SKILL.md", f"---\nname: greeter\ndescription: {body}\n---\n{body}\n")
    return pack


def test_a_preview_deletes_nothing(instance):
    """`dry_run` skipped the copy and not the delete: previewing a pack with
    "overwrite" chosen removed the installed skill and put nothing back."""
    # A pack that is one folder lands as the namespace folder itself.
    installed = instance / "usr/skills/pack"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text("the installed one", encoding="utf-8")

    result = skills_import.import_skills(str(_pack(instance)), namespace="pack", conflict="overwrite", dry_run=True)

    assert (installed / "SKILL.md").read_text(encoding="utf-8") == "the installed one"
    assert result.imported == [installed]


def test_overwrite_still_replaces_when_it_is_not_a_preview(instance):
    # A pack that is one folder lands as the namespace folder itself.
    installed = instance / "usr/skills/pack"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text("the installed one", encoding="utf-8")

    skills_import.import_skills(str(_pack(instance)), namespace="pack", conflict="overwrite")

    assert "new" in (installed / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("namespace", ["../../escaped", "{absolute}", "a/../../b", ".."])
def test_a_namespace_cannot_leave_the_skills_folder(instance, namespace):
    """`dest_root / namespace`: an absolute name replaces the root and `..` climbs
    out of it, and with "overwrite" the folder found there is deleted first."""
    namespace = namespace.format(absolute=instance / "elsewhere")
    with pytest.raises(ValueError):
        skills_import.import_skills(str(_pack(instance)), namespace=namespace, conflict="overwrite")
    assert not (instance / "escaped").exists() and not (instance / "elsewhere").exists()


@pytest.mark.parametrize("profile", ["../../escaped", "{absolute}", ".."])
def test_an_agent_profile_cannot_leave_the_agents_folder(instance, profile):
    profile = profile.format(absolute=instance / "elsewhere")
    with pytest.raises(ValueError):
        skills_import.import_skills(str(_pack(instance)), namespace="pack", agent_profile=profile)
    assert not (instance / "elsewhere").exists()


def test_the_unpacked_pack_does_not_stay_in_tmp(instance):
    skills_import.import_skills(str(_pack(instance)), namespace="pack")
    skills_import.import_skills(str(_pack(instance)), namespace="pack", dry_run=True)

    left = list((instance / "tmp" / "skill_imports").iterdir())
    assert left == [], f"every import and every preview left its unpacked copy behind: {left}"
