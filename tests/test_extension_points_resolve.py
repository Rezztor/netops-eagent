"""Extension points are directory names, so renaming one moves what it loads.

`@extension.extensible` builds the folder path from `func.__module__` and
`func.__qualname__`. Rename the function and the loader starts looking somewhere
else -- and finding nothing there is not an error, it is silence. Two watchdogs
register through this point, and a missed directory would simply stop them
registering with nothing in the log to say so.

That is why extension points were once listed as "deliberately
not renamed". The reasoning held while external plugins were expected; every
consumer is in this repository today, so the rename was made -- and this test is
what makes it safe to have made.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _point_directories(name: str) -> list[Path]:
    """Every `_functions/**/<name>` directory, wherever it lives."""
    return sorted(
        p for p in ROOT.rglob(f"_functions/**/{name}")
        if p.is_dir() and ".venv" not in p.parts
    )


def test_the_startup_extension_point_still_has_extensions_to_load():
    """The point run_ui calls. If this ever returns an empty list the rename
    broke, and nothing else would have told you."""
    directories = _point_directories("init_components")
    assert directories, "no init_components extension directory exists"

    loaded = [f for d in directories for f in d.rglob("_*.py")]
    assert loaded, f"extension directories exist but hold no extensions: {directories}"


def test_the_watchdogs_are_among_them():
    """Named specifically, because 'some files exist' would pass while the two
    things that actually register were missing."""
    files = {f.name for d in _point_directories("init_components") for f in d.rglob("_*.py")}
    assert "_10_register_watchdogs.py" in files
    assert "_30_register_watchdog.py" in files


def test_the_function_and_the_directories_agree():
    """The directory name is derived from the function name, so a rename of one
    without the other is the failure this guards. Read from the source rather
    than imported: importing run_ui starts configuring a process."""
    source = (ROOT / "run_ui.py").read_text(encoding="utf-8")
    assert "def init_components():" in source
    # Both lines below name the old point on purpose; the marker has to sit on
    # the offending line itself, because the check reads line by line.
    assert "init_a0" not in source, "the old name is back"  # brand-leak-allowed: asserts absence
    assert not _point_directories("init_a0"), "old dir survived"  # brand-leak-allowed: asserts absence
