"""A module is named by its `.py` path whether its source is shipped or not.

A delivered image carries the product's own modules compiled, with no source
beside them (stage 2 of the source-protection work, 2026-10-12).
`helpers.modules.find` is the one place that turns a `.py` name into the file
that holds the module, and every discovery site goes through it.
"""

import shutil
from pathlib import Path
from importlib.machinery import EXTENSION_SUFFIXES

import pytest

from helpers import files, modules, subagents

SUFFIX = EXTENSION_SUFFIXES[0]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    for name in ("api/a.py", f"api/b{SUFFIX}", "api/c.py", f"api/c{SUFFIX}", "api/notes.md",
                 f"api/v1/d{SUFFIX}", "usr/api/f.py", f"usr/api/e{SUFFIX}",
                 f"usr/plugins/p/g{SUFFIX}", f"tools/only_compiled{SUFFIX}"):  # fmt: skip
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).touch()
    return tmp_path


def test_a_py_name_finds_the_compiled_module_first_and_the_source_otherwise(root) -> None:
    assert modules.find("api/a.py") == str(root / "api/a.py")
    assert modules.find("api/b.py") == str(root / f"api/b{SUFFIX}")
    assert modules.find("api/c.py") == str(root / f"api/c{SUFFIX}")  # the order `import` uses
    assert modules.find("api/missing.py") == ""
    assert modules.find("api/notes.py") == ""
    assert modules.exists("api") and modules.exists("api/b.py") and not modules.exists("api/missing.py")


def test_only_source_counts_in_the_folder_written_at_run_time(root) -> None:
    assert modules.find("usr/api/f.py") == str(root / "usr/api/f.py")
    assert modules.find("usr/api/e.py") == ""
    assert modules.list_modules("usr/api") == [str(root / "usr/api/f.py")]
    # Reached through a link from the product's own tree, it is still that folder.
    (root / "plugins").mkdir()
    (root / "plugins/p").symlink_to(root / "usr/plugins/p")
    assert modules.find("plugins/p/g.py") == ""


def test_a_folder_lists_each_module_once_by_its_py_name(root) -> None:
    here = [str(root / "api" / name) for name in ("a.py", "b.py", "c.py")]
    assert modules.list_modules("api") == here
    assert modules.list_modules("api", "b*") == here[1:2]
    assert modules.list_modules("api", recursive=True) == here + [str(root / "api/v1/d.py")]
    assert modules.list_modules("no-such-folder") == []


def test_a_tool_with_no_source_is_still_found_by_name(root) -> None:
    found = subagents.get_paths(
        None, "tools", "only_compiled.py", include_project=False, include_user=False, include_plugins=False
    )
    assert found == [str(root / "tools/only_compiled.py")]


def test_a_compiled_module_loads_when_asked_for_by_its_py_name(root) -> None:
    # A real one, borrowed: nothing here may need a compiler.
    native = pytest.importorskip("markupsafe._speedups")
    shutil.copy(native.__file__, root / f"api/_speedups{SUFFIX}")
    loaded = modules.import_module(str(root / "api/_speedups.py"))
    assert loaded.__file__ == str(root / f"api/_speedups{SUFFIX}")
    assert loaded is not native and set(vars(native)) <= set(vars(loaded))
    # Once only: a compiled file loaded again would turn the first module's functions to the second's variables.
    assert modules.import_module(str(root / "api/_speedups.py")) is loaded


def test_a_discovered_module_with_an_extensible_function_is_never_imported_by_name() -> None:
    """An extension point's folder comes from its function's module name. A discovered module is named
    by the last part of its path when it is loaded as one, and by its dotted name when an `import`
    gets to a compiled one first; one that declares extension points must have a single name."""
    import re

    from helpers.files import get_base_dir

    base = Path(get_base_dir())
    discovered = [*base.glob("api/*.py"), *base.glob("tools/*.py"), *base.glob("extensions/python/**/*.py"),
                  *base.glob("plugins/*/api/**/*.py"), *base.glob("plugins/*/tools/*.py"),
                  *base.glob("plugins/*/extensions/python/**/*.py")]  # fmt: skip
    extensible = {path.relative_to(base).with_suffix("").as_posix().replace("/", ".")
                  for path in discovered if "extensible" in path.read_text(encoding="utf-8")}  # fmt: skip
    assert extensible, "the list of discovered folders no longer finds api/plugins.py"
    imported = set()
    for path in base.rglob("*.py"):
        if {".venv", "node_modules", "usr", "tmp", "tests"} & set(path.relative_to(base).parts):
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if found := re.match(r"\s*(?:from|import)\s+([\w.]+)", line):
                imported.add(found.group(1))
    assert not extensible & imported, sorted(extensible & imported)
