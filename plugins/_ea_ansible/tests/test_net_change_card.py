"""A change on its card: pre-check, tier, typed confirmation, verify, and the rollback card (ADR 0050)."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from types import SimpleNamespace

import pytest

from helpers import files, user_scope
from helpers.errors import RepairableException

from plugins._ea_ansible.api.change_approve import ChangeApprove
from plugins._ea_ansible.helpers import catalogue, change_store, executor_client
from plugins._ea_ansible.helpers.change_model import (
    APPLIED, APPROVED, AWAITING_APPROVAL, ROLLED_BACK, VERIFY_FAILED, ChangeRecord,
)
from plugins._ea_ansible.tools import net_apply, net_change

THE_CLIENTS_OWN_RUN = executor_client.run  # the `world` fixture replaces it

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)

ENTRIES = catalogue.load()
ADD = ENTRIES["changes/sonic_community_vlan_add"]
DEVICES = {
    n: {"name": n, "mgmt_ip": f"10.0.0.{i}", "platform": "sonic_community", "status": "active"}
    for i, n in enumerate(["lab-a", "lab-b", "lab-c", "lab-d"], 1)
}
ROW = "|    100 |"  # what `show vlan brief` prints for VLAN 100


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(net_change.plugin_config, "is_configured", lambda agent=None: True)
    monkeypatch.setattr(
        net_change, "_resolve",
        lambda names: ([DEVICES[n] for n in names if n in DEVICES], "") if all(n in DEVICES for n in names)
        else ([], f"`{names[0]}` is not in Inventory"),
    )
    state = SimpleNamespace(ok=True, cards=[], jobs=[], show=ROW, events=[])
    monkeypatch.setattr(
        net_change.preflight, "run_preflight",
        lambda a, devices, e: {"ok": state.ok, "checks": [{"label": "reachable", "ok": state.ok, "detail": "no"}], "devices": devices},
    )
    monkeypatch.setattr(net_change.executor_client, "health", lambda agent=None: {"catalogue_commit": "c" * 40})

    def card(agent, record, title, consequence):
        state.cards.append(net_change.change_card(record, title, consequence))

    monkeypatch.setattr(net_change, "show_card", card)
    monkeypatch.setattr(net_apply, "show_card", card)

    def run(agent, **kw):
        state.jobs.append(kw)
        out = state.show if kw["playbook_name"] == "reads/show" else "changed"
        return executor_client.JobResult("j1", "succeeded", 0, (), out)

    monkeypatch.setattr(net_apply.executor_client, "run", run)
    monkeypatch.setattr(change_store, "audit", SimpleNamespace(emit=lambda name, **kw: state.events.append(name)))
    with user_scope.user_scope("asha"):
        yield state


def tool(cls, name, **args):
    t = cls(agent=object(), name=name, method=None, args=args, message="", loop_data=None)
    return asyncio.run(t.execute()).message


def draft(devices="lab-a", **kw):
    msg = tool(net_change.NetChange, "net_change", action="draft", capability=ADD.name, devices=devices, vlan_id=100, **kw)
    return msg, change_store.list_records()


def approve(record, **body):
    return asyncio.run(ChangeApprove(None, None).process(
        {"decision": "approve", "change_id": record.id, "expected_hash": record.current_hash, **body}, None))


def approved(record):
    assert change_store.approve(record, "asha")
    return record


def use_entry(monkeypatch, **changes):
    entries = {**ENTRIES, ADD.name: dataclasses.replace(ADD, **changes)}
    monkeypatch.setattr(net_change.catalogue, "load", lambda: entries)
    monkeypatch.setattr(net_apply.catalogue, "load", lambda: entries)


# --- draft -------------------------------------------------------------------

def test_a_draft_resolves_prechecks_fills_placeholders_and_shows_the_whole_card(world):
    _, (record,) = draft()
    assert record.kind == "change" and record.status == AWAITING_APPROVAL
    assert record.devices_resolved[0]["name"] == "lab-a"
    assert 0 < record.expires_at - time.time() <= net_change.EXPIRY_SECONDS
    assert record.commands == ["sudo config vlan add 100"]  # `<vlan_id>` filled, so the card shows the real text
    assert record.rollback_commands == ["sudo config vlan del 100"]
    assert (record.tier, record.confirm) == ("change-low", "")
    (card,) = world.cards
    assert card["tier"] == "change-low" and "confirm" not in card
    assert '"sudo config vlan del 100"' in card["rollback"] and "sudo config vlan add 100" in card["commands"]
    assert float(card["expires_at"]) == record.expires_at and "reachable" in card["checks"]


def test_an_unknown_device_or_a_failed_precheck_leaves_no_record_and_no_card(world):
    assert "not in Inventory" in draft("nope")[0] and change_store.list_records() == []
    world.ok = False
    assert "cannot be changed right now" in draft()[0]
    assert change_store.list_records() == [] and world.cards == []


# --- tier --------------------------------------------------------------------

def test_the_tier_is_decided_by_the_entry_and_the_device_count(world, monkeypatch):
    _, (low,) = draft("lab-a,lab-b,lab-c")
    assert (low.tier, low.confirm) == ("change-low", "")
    for r in change_store.list_records():
        change_store.save(dataclasses.replace(r, status="cancelled"))
    draft("lab-a,lab-b,lab-c,lab-d")
    four = next(r for r in change_store.list_records() if r.status == AWAITING_APPROVAL)
    assert (four.tier, four.confirm) == ("change-high", "apply to 4 devices")
    use_entry(monkeypatch, risk="high")  # an entry that does not say `low` is high even on one device
    draft("lab-b")
    one = next(r for r in change_store.list_records() if r.limit == "lab-b" and r.status == AWAITING_APPROVAL)
    assert (one.tier, one.confirm) == ("change-high", "lab-b")
    assert world.cards[-1]["confirm"] == "lab-b"


def test_a_high_tier_approval_needs_the_typed_text_and_the_hash(world, monkeypatch):
    use_entry(monkeypatch, risk="high")
    _, (record,) = draft("lab-b")
    for body in ({}, {"confirmation": "lab-a"}, {"confirmation": ""}):
        assert approve(record, **body)["ok"] is False
    assert approve(record, confirmation=" lab-b ", expected_hash="stale")["ok"] is False
    assert change_store.load(record.id).status == AWAITING_APPROVAL
    assert approve(record, confirmation="lab-b")["ok"] and change_store.load(record.id).status == APPROVED


def test_a_low_tier_approval_needs_no_typing_but_does_need_the_hash(world):
    _, (record,) = draft()
    assert not asyncio.run(ChangeApprove(None, None).process({"decision": "approve", "change_id": record.id}, None))["ok"]
    assert approve(record)["ok"]


# --- verify and rollback ------------------------------------------------------

def apply_it(record):
    return tool(net_apply.NetApply, "net_apply", action="apply", change_id=record.id)


def test_a_verified_apply_offers_a_rollback_card_that_runs_through_the_same_gate(world):
    _, (record,) = draft()
    message = apply_it(approved(record))
    loaded = change_store.load(record.id)
    assert (loaded.status, loaded.verified) == (APPLIED, True) and ROW in loaded.output_excerpt
    assert [j["playbook_name"] for j in world.jobs] == [ADD.name, "reads/show"] and world.jobs[1]["check_mode"] is True
    undo = change_store.load(loaded.rollback_id)
    assert (undo.kind, undo.status, undo.rollback_of) == ("rollback", AWAITING_APPROVAL, record.id)
    assert undo.commands == ["sudo config vlan del 100"] and undo.playbook_name == "changes/sonic_community_vlan_del"
    assert undo.rollback_id == "" and "Roll back" in world.cards[-1]["question"] and undo.id in message

    # Not approved: the gate refuses. Approved: it runs, verifies absence and marks the change rolled back.
    from plugins._ea_ansible.extensions.python.tool_execute_before._95_change_gate import NetworkChangeGate
    gate = NetworkChangeGate(agent=SimpleNamespace(number=0))
    run_gate = lambda: asyncio.run(gate.execute(tool_name="net_apply", tool_args={"change_id": undo.id}))  # noqa: E731
    with pytest.raises(RepairableException, match="not 'approved'"):
        run_gate()
    assert approve(undo)["ok"]
    world.jobs_before_undo = len(world.jobs)
    run_gate()
    world.show = "no vlans"
    # Handing the undo's own id to `rollback` runs nothing and says how to run it (through the gate).
    wrong = tool(net_apply.NetApply, "net_apply", action="rollback", change_id=undo.id)
    assert "is the rollback itself" in wrong and world.jobs_before_undo == len(world.jobs)
    tool(net_apply.NetApply, "net_apply", action="apply", change_id=undo.id)
    assert change_store.load(record.id).status == ROLLED_BACK and "network.change.rolled_back" in world.events
    assert world.jobs[-2]["playbook_name"] == "changes/sonic_community_vlan_del"


def test_a_failed_verify_is_its_own_status_and_still_offers_the_rollback(world):
    world.show = "| 7 |"
    _, (record,) = draft()
    message = apply_it(approved(record))
    loaded = change_store.load(record.id)
    assert (loaded.status, loaded.verified) == (VERIFY_FAILED, False) and "did not verify" in message
    assert change_store.load(loaded.rollback_id).status == AWAITING_APPROVAL


def test_an_entry_with_no_verify_records_none(world, monkeypatch):
    use_entry(monkeypatch, verify=None)
    _, (record,) = draft()
    apply_it(approved(record))
    loaded = change_store.load(record.id)
    assert (loaded.status, loaded.verified) == (APPLIED, None) and len(world.jobs) == 1


def test_a_rollback_that_does_not_verify_leaves_the_change_applied(world):
    _, (record,) = draft()
    apply_it(approved(record))
    undo = approved(change_store.load(change_store.load(record.id).rollback_id))
    apply_it(undo)  # the VLAN row is still there
    assert change_store.load(undo.id).status == VERIFY_FAILED and change_store.load(record.id).status == APPLIED


# --- the catalogue -----------------------------------------------------------

def entry(**extra):
    return {"name": "changes/x", "playbook": "playbooks/changes/sonic_community_vlan_add.yml", "kind": "change",
            "check_mode": "unsupported", "reviewed_cli": ["x"], "rollback": "changes/x_undo", "platforms": ["ios"],
            "variables": {"n": {"type": "integer", "minimum": 1, "maximum": 9}}, **extra}


UNDO = {"name": "changes/x_undo", "playbook": "playbooks/changes/sonic_community_vlan_del.yml", "kind": "rollback",
        "check_mode": "unsupported", "reviewed_cli": ["y"]}

@pytest.mark.parametrize("extra,match", [
    ({"risk": "medium"}, "risk"),
    ({"verify": {"commands": {"ios": ["show version | include x"]}, "expect": "x"}}, "verify"),
    ({"verify": {"commands": {"ios": ["show version"]}, "expect": "<nope>"}}, "not a variable"),
    ({"verify": {"commands": {"ios": ["show version"]}, "expect": "("}}, "regex"),
    ({"verify": {"commands": {"eos": ["show version"]}, "expect": "x"}}, "platform"),
])
def test_risk_and_verify_are_validated_at_load(tmp_path, extra, match):
    import yaml

    show = next(e for e in yaml.safe_load(open(catalogue.CATALOGUE_FILE))["entries"] if e["name"] == "reads/show")
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"entries": [show, entry(**extra), UNDO]}))
    with pytest.raises(catalogue.CatalogueError, match=match):
        catalogue.load(str(path))


def test_the_changes_that_exist_are_listed_with_platforms_and_variables():
    """What an unknown capability is answered with; without it the agent says a change cannot be made."""
    listed = net_change._changes()
    assert "- changes/sonic_community_vlan_add:" in listed and "Platforms: sonic_community. Needs: vlan_id." in listed
    assert "reads/" not in listed and "_vlan_del" not in listed  # reads and rollbacks are not drafted


def test_a_failed_check_mode_render_repeats_the_playbooks_own_sentence(monkeypatch):
    """A switch module that cannot take `ios_vlan_name` says so in the job output; the card path must show it."""
    import pytest

    from plugins._ea_ansible.helpers import catalogue, executor_client, render

    out = 'fatal: [sw]: FAILED! => {"changed": false, "msg": "sw cannot take this change: no VLAN resource. Nothing was changed."}\nPLAY RECAP'
    monkeypatch.setattr(executor_client, "run", lambda *a, **k: executor_client.JobResult("j", "failed", 2, (), out))
    entry = catalogue.load()["changes/ios_vlan_name"]
    with pytest.raises(render.RenderFailed, match="It said: sw cannot take this change: no VLAN resource"):
        render.render(None, entry, {"vlan_id": 40, "vlan_name": "x"}, "sw", "abc")


def test_an_apply_this_side_stopped_waiting_for_is_not_reported_as_not_run(world, monkeypatch):
    """The job is still on the executor and may yet reach the device: say so, and offer the way back."""
    _, (record,) = draft()
    approved(record)

    def run(agent, **kw):
        raise executor_client.JobStillRunning("Job j9 was still running when this request gave up waiting.")

    monkeypatch.setattr(net_apply.executor_client, "run", run)
    message = tool(net_apply.NetApply, "net_apply", change_id=record.id)

    assert "did not run" not in message and "unknown" in message and "may still be applied" in message
    assert len(world.cards) == 2  # the change's card, then the rollback's
    assert any(r.kind == "rollback" and r.rollback_of == record.id for r in change_store.list_records())


def test_an_apply_whose_job_was_sent_and_then_could_not_be_read_is_not_reported_as_not_run(world, monkeypatch):
    """The executor took the job and then stopped answering (a restart, a dropped connection). The playbook may be
    running against the device at that moment, so "did not run" is the one thing that must not be said."""
    _, (record,) = draft()
    approved(record)

    def poll(agent, job_id):
        raise executor_client.ExecutorUnreachable("The Ansible executor did not answer: connection reset")

    monkeypatch.setattr(executor_client, "run", THE_CLIENTS_OWN_RUN)  # `world` replaced it
    monkeypatch.setattr(executor_client, "submit", lambda *a, **k: "j9")
    monkeypatch.setattr(executor_client, "poll", poll)
    monkeypatch.setattr(executor_client.plugin_config, "get_config", lambda agent=None: {"job_timeout_seconds": 5})
    message = tool(net_apply.NetApply, "net_apply", change_id=record.id)

    assert "did not run" not in message and "unknown" in message and "j9" in message


def test_an_apply_records_its_executor_job(world):
    _, (record,) = draft()
    approved(record)
    tool(net_apply.NetApply, "net_apply", change_id=record.id)
    assert change_store.load(record.id).job_id == "j1"


def test_an_azure_record_is_not_decided_by_the_device_endpoint(world):
    """Its own endpoint demands the requester, a CR or SR number and the change document; this one asks for none."""
    record = change_store.save(ChangeRecord(kind="azure_change", requested_by="asha", status=AWAITING_APPROVAL, commands=["x"]))
    for decision in ("approve", "reject"):
        out = asyncio.run(ChangeApprove(None, None).process(
            {"decision": decision, "change_id": record.id, "expected_hash": record.current_hash}, None))
        assert not out["ok"] and "cannot be decided here" in out["error"]
    assert change_store.load(record.id).status == AWAITING_APPROVAL


def test_a_record_file_put_back_after_its_apply_does_not_run_again(world, tmp_path, monkeypatch):
    """ADR 0077: the approval is a row that was used, so the signed, approved, unexpired file is no approval any more."""
    import shutil

    _, (record,) = draft()
    approved(record)
    copy = shutil.copy(change_store._path(record.id), tmp_path / "approved.json")
    stale = change_store.load(record.id)  # what a second call read while the change was still approved
    apply_it(record)
    pushed = [j for j in world.jobs if not j["check_mode"]]

    shutil.copy(copy, change_store._path(record.id))
    assert "Only an approved change" in tool(net_apply.NetApply, "net_apply", change_id=record.id)
    with monkeypatch.context() as patched:
        patched.setattr(net_apply.change_store, "load", lambda *a, **k: stale)
        assert "no approval left" in tool(net_apply.NetApply, "net_apply", change_id=record.id)
    assert [j for j in world.jobs if not j["check_mode"]] == pushed and len(pushed) == 1
