import asyncio
import json

import pytest

from helpers import tool_permissions, user_scope, workdir
from plugins._ea_inventory.tools import inventory_read_file as tool_mod
from plugins._ea_inventory.tools.inventory_read_file import InventoryReadFile

CFG = "hostname r1\nusername a secret 9 $9$hash123\nsnmp-server community Pr1vate RW\n" + "".join(
    f"interface Gi0/{i}\n" for i in range(30)
)


@pytest.fixture
def root(tmp_path, monkeypatch):
    mine = tmp_path / "users" / "me"
    (mine / "uploads").mkdir(parents=True)
    (tmp_path / "users" / "other").mkdir()
    (tmp_path / "users" / "other" / "x.csv").write_text("name\nsecretly-theirs\n")
    (mine / "uploads" / "inv.csv").write_text(
        "name,ip,password\n" + "".join(f"sw{i},10.0.0.{i},pw{i}secret\n" for i in range(1, 8))
    )
    (mine / "uploads" / "r.cfg").write_text(CFG)
    (mine / "workdir").mkdir()
    (mine / "network_changes").mkdir()
    (mine / "network_changes" / "c1.json").write_text('{"seal": "abc"}')
    (mine / "chats").mkdir()
    (mine / "chats" / "t.json").write_text('{"said": "private"}')
    monkeypatch.setattr(workdir, "user_file_roots", lambda user_id=None: [str(mine / "workdir"), str(mine / "uploads")])
    return mine


def run(**args) -> tuple[str, dict]:
    tool = InventoryReadFile.__new__(InventoryReadFile)
    tool.args = args
    msg = asyncio.new_event_loop().run_until_complete(tool.execute()).message
    body, _, meta = msg.rpartition("\n\n")
    try:
        return body, json.loads(meta)
    except ValueError:
        return msg, {}


def test_table_view_pages_and_numbers_rows(root):
    body, meta = run(path="inv.csv", limit=3)
    lines = body.split("\n")
    assert lines[0] == "row\tname\tip\tpassword"
    assert lines[1] == "1\tsw1\t10.0.0.1\t[REDACTED:secret]" and len(lines) == 4
    assert meta["kind"] == "csv" and meta["total_rows"] == 7 and meta["next_offset"] == 3
    assert meta["redactions"] == {"secret": 7} and len(meta["source_id"]) == 64
    body, meta = run(path="inv.csv", offset=3, limit="100")
    assert body.split("\n")[1].startswith("4\tsw4") and meta["next_offset"] is None
    assert "pw1secret" not in body


def test_text_view_is_redacted_and_paged_by_lines(root):
    body, meta = run(path=str(root / "uploads" / "r.cfg"), limit=3)
    assert body == "hostname r1\nusername a secret 9 [REDACTED:password]\nsnmp-server community [REDACTED:community] RW"
    assert meta["next_offset"] == 3 and meta["total_lines"] == 34 and meta["kind"] == "text"
    assert "hash123" not in body and "Pr1vate" not in body


def test_output_is_capped(root, monkeypatch):
    monkeypatch.setattr(tool_mod, "MAX_CHARS", 60)
    body, meta = run(path="r.cfg", limit=500)
    assert len(body) <= 130 and 0 < meta["next_offset"] < 34


def test_errors_are_messages_not_raises_and_never_cross_users(root):
    for path in ("missing.csv", "../../other/x.csv", "/etc/passwd", "", "r.exe"):
        msg, _ = run(path=path)
        assert msg.startswith("Could not read the file:"), msg
        assert "secretly-theirs" not in msg


def test_the_rest_of_the_users_tree_is_not_readable(root):
    """Change records (with their seals) and chat transcripts live beside the uploads; the tool reads only the folders
    a model-named path may point into (audit 3, L8)."""
    for path in ("network_changes/c1.json", "chats/t.json", str(root / "network_changes" / "c1.json"), "../chats/t.json"):
        msg, _ = run(path=path)
        assert msg.startswith("Could not read the file:"), path
        assert "abc" not in msg and "private" not in msg


def test_no_user_context_is_refused(monkeypatch):
    monkeypatch.setattr(user_scope, "get_current_user_id", lambda: None)
    msg, _ = run(path="inv.csv")
    assert msg.startswith("Could not read the file:")


def test_requires_inventory_manage():
    assert tool_permissions.required_permission("inventory_read_file") == "inventory.manage"
    assert tool_permissions.is_allowed("inventory_read_file", "power")
    assert tool_permissions.is_allowed("inventory_read_file", "admin")
    assert not tool_permissions.is_allowed("inventory_read_file", "user")


def test_tool_has_no_write_path():
    src = tool_mod.__loader__.get_source(tool_mod.__name__)
    assert "propose" not in src and "decide" not in src
