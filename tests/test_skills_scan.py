import importlib.util
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_skills_import_module(tmp_path):
    module_name = "test_skills_import_module"
    # Snapshot every helpers.* entry, not just the ones stubbed below. The stub
    # package has an empty __path__, so anything importing helpers.<other>
    # while it is installed caches an unimportable entry that would otherwise
    # survive into later tests.
    originals = {
        name: module
        for name, module in sys.modules.items()
        if name == "helpers" or name.startswith("helpers.")
    }

    helpers_pkg = types.ModuleType("helpers")
    helpers_pkg.__path__ = []

    files = types.ModuleType("helpers.files")
    files.get_abs_path = lambda *parts: str(tmp_path.joinpath(*parts))

    skills = types.ModuleType("helpers.skills")
    skills.discover_skill_md_files = lambda root: sorted(Path(root).rglob("SKILL.md"))

    helpers_pkg.files = files
    helpers_pkg.skills = skills
    sys.modules["helpers"] = helpers_pkg
    sys.modules["helpers.files"] = files
    sys.modules["helpers.skills"] = skills

    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            PROJECT_ROOT / "helpers" / "skills_import.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name in [
            n for n in sys.modules
            if n == "helpers" or n.startswith("helpers.")
        ]:
            if name not in originals:
                sys.modules.pop(name, None)
        sys.modules.update(originals)


def test_extract_skills_zip_rejects_path_traversal(monkeypatch, tmp_path):
    skills_import = _load_skills_import_module(tmp_path)

    archive_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.txt", "nope")

    with pytest.raises(ValueError, match="Unsafe zip entry path"):
        skills_import.extract_skills_zip(archive_path)

    assert not (tmp_path / "escape.txt").exists()


def test_extract_skills_zip_returns_single_top_level_root(monkeypatch, tmp_path):
    skills_import = _load_skills_import_module(tmp_path)

    archive_path = tmp_path / "skills.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("pack/example/SKILL.md", "---\nname: Example\n---\nBody\n")

    source_root, cleanup_root = skills_import.extract_skills_zip(archive_path)

    assert source_root.name == "pack"
    assert cleanup_root.is_dir()
    assert (source_root / "example" / "SKILL.md").is_file()


