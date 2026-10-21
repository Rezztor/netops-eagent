#!/usr/bin/env python3
"""Compile the product's Python into native modules and remove the source.

    python compile_python.py ROOT

Run in a build stage, with the interpreter the image will run and Nuitka
installed beside it: a compiled module serves one Python version. Each
`name.py` under ROOT becomes `name<extension suffix>` in the same folder, so
every import and every `__file__` keeps its place, and helpers.modules.find
gives the discovered ones (handlers, tools, extensions, hooks) to their callers.
Docstrings are left out of what is compiled.

What stays as source is decided in `stays`, each kind for its reason; the
bundle build looks into the images for any other `.py` of ours
(scripts/build_offline_bundle.sh).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path

SUFFIX = EXTENSION_SUFFIXES[0]

# Run by path (`python run_ui.py`), which a compiled module cannot be: the code
# is compiled and these lines take its place.
LAUNCHERS = {"run_ui.py": "from run_ui import main\n\nmain()\n"}

# Not ours to compile, or not code: the customer's own tree, run-time state, the built frontend.
SKIPPED = ("usr", "tmp", "frontend")


def stays(relative: Path) -> str:
    """Why a file is left as source, or "" when it is compiled."""
    if relative.name == "__init__.py":
        return "a package's own file cannot be compiled apart from the package; it holds imports"
    if relative.as_posix() == "prepare.py":
        return "run by path at start; a few lines that call into compiled code"
    if "playbooks" in relative.parts:
        return "loaded by Ansible, which reads source"
    if relative.parent.name == "commands":
        return "a command's script is shown as text in the Commands manager and copied from there"
    return ""


def compile_one(source: Path) -> str:
    """Compile one file and put the module where the source was. Returns "" or what went wrong."""
    # A module's name is one identifier, and `prompts/agent.system.main.tips.py`
    # is loaded under its last part, so that is the name it is compiled as.
    name = source.name[: -len(".py")].rsplit(".", 1)[-1]
    with tempfile.TemporaryDirectory() as work:
        copy = Path(work) / f"{name}.py"
        shutil.copyfile(source, copy)
        done = subprocess.run(
            [sys.executable, "-m", "nuitka", "--mode=module", str(copy), f"--output-dir={work}",
             "--no-pyi-file", "--remove-output", "--quiet", "--jobs=1", "--python-flag=no_docstrings",
             # Without it every module carries the whole of Nuitka's runtime: 880 KB each
             # where this gives 320 KB, and the link is quicker too (Debian's gcc 14, 2026-10-12).
             "--lto=yes"],  # fmt: skip
            capture_output=True, text=True,
        )  # fmt: skip
        built = Path(work) / f"{name}{SUFFIX}"
        if done.returncode or not built.is_file():
            return f"{source}:\n{done.stdout[-2000:]}{done.stderr[-2000:]}"
        shutil.move(built, source.with_name(source.name[: -len(".py")] + SUFFIX))
    source.unlink()
    return ""


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    sources, kept = [], 0
    for folder, folders, names in os.walk(root):
        if Path(folder) == root:
            folders[:] = [name for name in folders if name not in SKIPPED]
        for name in names:
            path = Path(folder) / name
            if name.endswith(".py"):
                if stays(path.relative_to(root)):
                    kept += 1
                else:
                    sources.append(path)
    with ThreadPoolExecutor(os.cpu_count()) as pool:
        problems = [problem for problem in pool.map(compile_one, sources) if problem]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        print(f"compile_python: {len(problems)} of {len(sources)} files did not compile", file=sys.stderr)
        return 1
    for relative, text in LAUNCHERS.items():
        if (root / relative).with_name(Path(relative).stem + SUFFIX).is_file():
            (root / relative).write_text(text)
            kept += 1
    print(f"compile_python: {len(sources)} modules compiled under {root}, {kept} files left as source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
