"""A tool name the model writes is never turned into a file that is imported.

Agent.get_tool used to look the name up on disk and import what it found before
it asked whether the name was allowed, so `/eagent/usr/users/<id>/uploads/evil`
ran an uploaded `.py` in the web-server process (audit 6, C1).
"""

from types import SimpleNamespace

import pytest

from agent import Agent
from helpers import files, modules, subagents
from tools.refused import Refused

EVIL = "open({marker!r}, 'w').write('ran')\n"


def _fake_agent():
    log = SimpleNamespace(log=lambda **kwargs: None)
    return SimpleNamespace(context=SimpleNamespace(owner_id=None, id="ctx", log=log))


@pytest.mark.parametrize("shape", ["absolute", "relative"])
def test_a_path_shaped_tool_name_is_refused_before_anything_is_imported(tmp_path, monkeypatch, shape):
    marker = tmp_path / "marker"
    (tmp_path / "evil.py").write_text(EVIL.format(marker=str(marker)))
    looked_up = []
    real_get_paths = subagents.get_paths

    def get_paths(agent, *subpaths, **kwargs):
        if subpaths[:1] == ("tools",):
            looked_up.append(subpaths)
        return real_get_paths(None, *subpaths, **kwargs)

    monkeypatch.setattr(subagents, "get_paths", get_paths)
    name = str(tmp_path / "evil")
    if shape == "relative":
        name = "../" * 12 + name.lstrip("/")

    tool = Agent.get_tool(_fake_agent(), name=name, method=None, args={}, message="", loop_data=None)

    assert isinstance(tool, Refused)
    assert not marker.exists(), "the module was imported"
    assert looked_up == [], "the name reached the filesystem lookup"
    assert "not a tool name" in tool.refusal_message


def test_nothing_is_imported_from_a_users_files(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    marker = tmp_path / "marker"
    upload = tmp_path / "usr/users/u1/uploads/evil.py"
    upload.parent.mkdir(parents=True)
    upload.write_text(EVIL.format(marker=str(marker)))
    # Reached through a link from elsewhere it is still a user's file.
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/linked.py").symlink_to(upload)

    for path in (str(upload), "tools/linked.py"):
        with pytest.raises(ImportError):
            modules.import_module(path)
    assert not marker.exists()
