import asyncio
import json
from pathlib import Path

import pytest

from helpers import api_policy, rbac, tool_permissions
from plugins._ea_ansible.helpers import executor_client
from plugins._ea_inventory.api.inventory_refresh_facts import InventoryRefreshFacts
from plugins._ea_inventory.helpers import proposals
from plugins._ea_inventory.helpers.facts import collect
from plugins._ea_inventory.tools.inventory_refresh_facts import InventoryRefreshFacts as RefreshTool

PLUGIN = Path(__file__).resolve().parents[1]

FACTS = {
    "ansible_net_hostname": "sw1", "ansible_net_model": "C9300-48P",
    "ansible_net_serialnum": "FOC1234ABCD", "ansible_net_version": "17.3.4",
}


def line(host, facts, platform="ios", escaped=False):
    text = f"EAGENT_FACTS {host} " + json.dumps({"platform": platform, "facts": facts}, separators=(",", ":"))
    return json.dumps({"msg": text})[1:-1].join(['"msg": "', '"']) if escaped else text


class Executor:
    def __init__(self):
        self.facts, self.calls, self.fail = {"sw1": FACTS}, [], {}

    def health(self, agent=None):
        return {"catalogue_commit": "abc"}

    def run(self, agent, playbook, commit, variables, limit, check_mode, wait_seconds=None):
        self.calls.append((playbook, commit, variables, limit, check_mode))
        status = self.fail.get(limit, "succeeded")
        output = line(limit, self.facts[limit], escaped=True) if limit in self.facts else "ok: [x]"
        return executor_client.JobResult(f"job-{limit}", status, 0 if status == "succeeded" else 2, (), output)


@pytest.fixture
def ex(database, events, monkeypatch):
    e = Executor()
    monkeypatch.setattr(executor_client, "health", e.health)
    monkeypatch.setattr(executor_client, "run", e.run)
    return e


@pytest.fixture(autouse=True)
def who(monkeypatch):
    monkeypatch.setattr(proposals, "current_actor", lambda: "alice")


def call(payload):
    handler = InventoryRefreshFacts(app=None, thread_lock=None)
    return asyncio.new_event_loop().run_until_complete(handler.process(payload, None))


def tool(args):
    t = RefreshTool.__new__(RefreshTool)
    t.args, t.agent = args, None
    return asyncio.new_event_loop().run_until_complete(t.execute()).message


def test_route_and_tool_need_inventory_manage():
    assert api_policy.required_permission_for("plugins/_ea_inventory/inventory_refresh_facts") == rbac.PERM_INVENTORY_MANAGE
    assert tool_permissions.required_permission("inventory_refresh_facts") == rbac.PERM_INVENTORY_MANAGE
    assert tool_permissions.is_allowed("inventory_refresh_facts", rbac.ROLE_POWER)
    assert not tool_permissions.is_allowed("inventory_refresh_facts", rbac.ROLE_USER)


def test_tool_cannot_approve_or_stage_directly():
    text = (PLUGIN / "tools" / "inventory_refresh_facts.py").read_text()
    assert "decide" not in text and "propose" not in text and "APPROVE" not in text
    assert (PLUGIN / "prompts" / "agent.system.tool.inventory_refresh_facts.md").exists()


def test_api_shape(ex, make):
    make("sw1", status="active", platform="ios")
    out = call({"devices": ["sw1"]})
    assert out["ok"] and set(out) == {"ok", "results", "batch_id", "counts"}
    assert out["results"][0]["status"] == "ok" and out["batch_id"] and out["counts"]["proposals"] == 1
    json.dumps(out)


def test_api_dry_run_and_all_devices(ex, make):
    make("sw1", status="active", platform="ios")
    out = call({"dry_run": True})
    assert out["batch_id"] is None and out["counts"]["proposals"] == 1
    assert not proposals.list_proposals(status="pending")[0]


def test_api_rejects_bad_devices_and_reports_executor_errors(ex, make, monkeypatch):
    assert not call({"devices": "sw1"})["ok"]
    make("sw1", status="active", platform="ios")

    def down(agent=None):
        raise executor_client.ExecutorUnreachable("The Ansible executor did not answer")

    monkeypatch.setattr(executor_client, "health", down)
    out = call({"devices": ["sw1"]})
    assert out["ok"] is False and "did not answer" in out["error"]


def test_tool_accepts_a_name_string_and_summarises(ex, make):
    make("sw1", status="active", platform="ios")
    text = tool({"devices": "sw1"})
    head, body = text.split("\n\n", 1)
    assert "1 refreshed" in head and "1 pending requests" in head and "nothing on a device changed" in head
    assert json.loads(body)["results"][0]["device"] == "sw1"
    assert "Dry run" in tool({"devices": ["sw1"], "dry_run": True})


def test_tool_reports_errors_as_text(ex, monkeypatch):
    monkeypatch.setattr(collect, "refresh_facts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert "inventory_refresh_facts failed: boom" in tool({})
