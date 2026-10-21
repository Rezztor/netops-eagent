"""Test helpers shared by tests/ and plugins/*/tests/.

An importable module rather than conftest.py: with several conftest.py files,
`from conftest import ...` resolves to whichever one pytest imported last.
"""

import os


def build_instance_root(tmp_path) -> str:
    """A base directory the application can actually run against.

    `files._base_dir` redirects *everything*, not just `usr/` -- prompts, conf
    and the rest resolve through it too, so a bare tmp_path fails on
    `conf/workdir.gitignore` or `fw.initial_message.md` the first time anything
    real is built. The root is therefore symlinks back to the repository with
    only `usr/` real.

    Here rather than in each test file because three of them had written their
    own copy by the time this was noticed.
    """
    repo = os.path.dirname(os.path.abspath(__file__))
    for entry in os.listdir(repo):
        if entry != "usr":
            link = os.path.join(str(tmp_path), entry)
            if not os.path.lexists(link):
                os.symlink(os.path.join(repo, entry), link)
    os.makedirs(os.path.join(str(tmp_path), "usr"), exist_ok=True)
    return str(tmp_path)
