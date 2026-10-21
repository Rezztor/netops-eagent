import asyncio
import json
import re
import sys
import types
from pathlib import Path

import pytest

from helpers import rbac, tool_permissions
from plugins._ea_inventory.helpers import proposals, staging, store
from plugins._ea_inventory.tools.inventory_stage import InventoryStage

TOOLS = Path(__file__).resolve().parents[1] / "tools"
DOC = types.SimpleNamespace(text="sw-a FOC1 10.0.0.1 password [REDACTED:password]", source_id="h1")


@pytest.fixture
def fake_modules(monkeypatch):
    """Stand-ins for the reader and redactor, so these tests do not depend on file parsing."""
    seen = {}

    def read_source(path, *, root=None, sheet=None):
        seen.update(path=path, root=root)
        if path == "missing.csv":
            raise FileNotFoundError("missing.csv is not in your files")
        return DOC

    readers = types.SimpleNamespace(read_source=read_source)
    redact = types.SimpleNamespace(redact_text=lambda t: (t.replace("hunter2", "[REDACTED:password]"), {}))
    pkg = "plugins._ea_inventory.helpers"
    for name, mod in (("readers", readers), ("redact", redact)):
        monkeypatch.setitem(sys.modules, f"{pkg}.{name}", mod)
        monkeypatch.setattr(sys.modules[pkg], name, mod, raising=False)
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")
    return seen


def run(args):
    tool = InventoryStage.__new__(InventoryStage)
    tool.args = args
    return asyncio.new_event_loop().run_until_complete(tool.execute()).message


def records():
    return [{"fields": {"name": "sw-a", "serial": "foc1"}, "evidence": {"quote": "sw-a FOC1 10.0.0.1", "location": "row 1"}}]


def test_stage_reads_from_the_users_root_and_stages_pending(database, events, fake_modules):
    out = run({"file": "sales.csv", "records": records(), "note": "from hunter2"})
    head, body = out.split("\n\n", 1)
    result = json.loads(body)
    assert "1 to add" in head and "not devices" in head and result["rejected"] == []
    assert fake_modules == {"path": "sales.csv", "root": None}  # the reader's default: the user's file roots
    [p] = proposals.list_proposals(status="pending")[0]
    assert p["evidence"]["note"] == "from [REDACTED:password]" and p["evidence"]["file"] == "sales.csv"
    assert store.search_devices()[1] == 0


def test_errors_come_back_as_text(database, events, fake_modules):
    assert "is not in your files" in run({"file": "missing.csv", "records": records()})
    assert "At most 200" in run({"file": "a", "records": records() * 201})
    assert "non-empty" in run({"file": "a"})


def test_the_tool_needs_inventory_manage():
    assert tool_permissions.required_permission("inventory_stage") == rbac.PERM_INVENTORY_MANAGE
    assert tool_permissions.is_allowed("inventory_stage", rbac.ROLE_POWER)
    assert not tool_permissions.is_allowed("inventory_stage", rbac.ROLE_USER)


def test_the_tool_cannot_approve():
    text = (TOOLS / "inventory_stage.py").read_text()
    assert "decide" not in text and ".propose(" not in text and "APPROVE" not in text
    assert hasattr(staging, "stage")


def test_no_tool_creates_changes_or_deletes_a_place():
    for tool in TOOLS.glob("*.py"):
        assert not re.search(r"places\.(create|update|delete)\b|places import", tool.read_text()), tool.name


def test_stages_from_a_real_csv_in_the_users_root(database, events, tmp_path, monkeypatch):
    (tmp_path / "devices.csv").write_text(
        "name,serial,mgmt_ip,password\nsw-a,FOC1,10.0.0.1,hunter2\nsw-b,FOC2,10.0.0.2,s3cret\n"
    )
    monkeypatch.setattr("helpers.workdir.user_file_roots", lambda user_id=None: [str(tmp_path)])
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")
    quote = "sw-a\tFOC1\t10.0.0.1"
    out = run({
        "file": "devices.csv",
        "records": [
            {"fields": {"name": "sw-a", "serial": "FOC1"}, "evidence": {"quote": quote, "location": "row 1"}},
            {"fields": {"name": "sw-b"}, "evidence": {"quote": "sw-b,FOC2,10.0.0.2,s3cret", "location": "row 2"}},
        ],
    })
    result = json.loads(out.split("\n\n", 1)[1])
    assert result["created"] == 1 and len(result["rejected"]) == 1
    assert "hunter2" not in out and "s3cret" not in out
    assert run({"file": "../x.csv", "records": records()}).startswith("inventory_stage failed:")
