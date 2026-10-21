"""The change lifecycle, and the gate that stands in front of it.

These cover the parts that decide whether a change reaches a device: who may
approve, what approval binds to, and what happens when the devices move
underneath an approval that has already been given.
"""

from __future__ import annotations

import asyncio

import pytest

from helpers import files, user_scope
from helpers.errors import RepairableException

from plugins._ea_ansible.helpers import change_store, conditions
from plugins._ea_ansible.helpers.change_model import (
    APPLIED,
    APPLYING,
    APPROVED,
    AWAITING_APPROVAL,
    ChangeRecord,
)

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    with user_scope.user_scope("asha"):
        yield tmp_path


def drafted(**overrides) -> ChangeRecord:
    record = ChangeRecord(
        requested_by="asha",
        status=AWAITING_APPROVAL,
        playbook_name="changes/access_vlan",
        commit="c" * 40,
        variables={"vlan_id": 300},
        limit="access-switch-4",
        commands=["interface GigabitEthernet0/12", "switchport access vlan 300"],
        render_method="check_mode",
    )
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


# --- the store ---------------------------------------------------------------

def test_a_change_round_trips_through_the_store(instance):
    record = change_store.save(drafted())
    loaded = change_store.load(record.id)
    assert loaded.commands == record.commands
    assert loaded.current_hash == record.current_hash


def test_the_register_lists_newest_first(instance):
    first = change_store.save(drafted(created_at=1.0))
    second = change_store.save(drafted(created_at=2.0))
    assert [r.id for r in change_store.list_records()][:2] == [second.id, first.id]


def test_a_missing_change_is_not_found(instance):
    with pytest.raises(change_store.ChangeNotFound):
        change_store.load("deadbeef")


def test_a_change_id_cannot_walk_out_of_the_directory(instance):
    with pytest.raises(change_store.ChangeNotFound):
        change_store.load("../../etc/passwd")


# --- what approval binds to --------------------------------------------------

def test_approval_binds_to_the_commands_not_to_the_change(instance):
    record = drafted()
    record.status = APPROVED
    record.approved_by = "ravi"
    record.approved_hash = record.current_hash
    assert conditions.first_failure(record, "asha") is None

    # The device moved. The commands that would run are no longer the ones read.
    record.commands = ["interface GigabitEthernet0/12", "switchport access vlan 999"]
    refusal = conditions.first_failure(record, "asha")
    assert refusal and "superseded" in refusal


def test_the_requester_may_be_the_approver(instance):
    record = drafted(status=APPROVED, approved_by="asha")
    record.approved_hash = record.current_hash
    assert conditions.first_failure(record, "asha") is None


def test_an_unapproved_change_fails_the_second_condition(instance):
    record = drafted()
    refusal = conditions.first_failure(record, "asha")
    assert refusal and "approved the rendered commands" in refusal


# --- the gate ----------------------------------------------------------------

class FakeAgent:
    def __init__(self, number: int = 0):
        self.number = number


def gate():
    import importlib

    module = importlib.import_module(
        "plugins._ea_ansible.extensions.python.tool_execute_before._95_change_gate"
    )
    return module


def run_gate(agent, tool_name, tool_args):
    extension = gate().NetworkChangeGate(agent=agent)
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        extension.execute(tool_name=tool_name, tool_args=tool_args)
    )


def test_the_gate_ignores_tools_it_does_not_guard(instance):
    run_gate(FakeAgent(), "net_read", {"capability": "reads/device_facts"})


def test_the_gate_refuses_a_subordinate(instance):
    with pytest.raises(RepairableException, match="subordinate"):
        run_gate(FakeAgent(number=1), "net_apply", {"change_id": "x"})


def test_the_gate_refuses_a_call_with_no_change_id(instance):
    with pytest.raises(RepairableException, match="change_id"):
        run_gate(FakeAgent(), "net_apply", {})


def test_the_gate_refuses_an_unapproved_change(instance):
    record = change_store.save(drafted())
    with pytest.raises(RepairableException, match="not 'approved'"):
        run_gate(FakeAgent(), "net_apply", {"change_id": record.id})


def test_the_gate_lets_a_change_approved_by_its_requester_through(instance):
    record = drafted()
    assert change_store.approve(record, "asha")
    run_gate(FakeAgent(), "net_apply", {"change_id": record.id})


def test_the_gate_refuses_a_subordinate_even_for_a_self_approved_change(instance):
    record = drafted()
    assert change_store.approve(record, "asha")
    with pytest.raises(RepairableException, match="subordinate"):
        run_gate(FakeAgent(number=1), "net_apply", {"change_id": record.id})


def test_the_audit_line_carries_requester_and_approver(instance, monkeypatch):
    events = []
    monkeypatch.setattr(
        change_store, "audit", type("A", (), {"emit": staticmethod(lambda n, **kw: events.append((n, kw)))})
    )
    record = change_store.save(drafted())
    assert change_store.approve(record, "asha")
    name, fields = events[-1]
    assert name == "network.change.approved"
    assert fields["requested_by"] == "asha" and fields["approved_by"] == "asha"


def test_the_gate_lets_a_properly_approved_change_through(instance):
    record = drafted()
    assert change_store.approve(record, "ravi")
    run_gate(FakeAgent(), "net_apply", {"change_id": record.id})


def test_the_gate_refuses_a_change_that_drifted_since_approval(instance):
    record = drafted()
    assert change_store.approve(record, "ravi")
    record.commands = ["switchport access vlan 999"]
    change_store.save(record)
    with pytest.raises(RepairableException, match="superseded"):
        run_gate(FakeAgent(), "net_apply", {"change_id": record.id})


def test_the_gate_refuses_when_it_cannot_complete_its_checks(instance, monkeypatch):
    """Fails closed. A broken gate that lets changes through only looks like one."""
    def explode(*_args, **_kwargs):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(change_store, "load", explode)
    with pytest.raises(RepairableException, match="could not complete"):
        run_gate(FakeAgent(), "net_apply", {"change_id": "x"})


def test_the_gate_does_not_stand_in_front_of_verify_or_close(instance):
    run_gate(FakeAgent(), "net_apply", {"action": "close", "change_id": "x"})


# --- after the change: verify, roll back, and the audit line -----------------

def test_every_status_transition_is_audited(instance, monkeypatch):
    """Audited from change_store.save rather than from each caller. There are
    five callers already and there will be more; a change that reached a device
    with no audit line because someone added a sixth is what this prevents."""
    emitted: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        change_store, "audit",
        type("Stub", (), {"emit": staticmethod(lambda event, **f: emitted.append((event, f)))}),
    )
    record = drafted()
    change_store.save(record)
    assert change_store.approve(record, "asha")

    assert [event for event, _ in emitted] == [
        "network.change.awaiting_approval",
        "network.change.approved",
    ]
    assert emitted[1][1]["previous_status"] == "awaiting_approval"
    assert emitted[1][1]["change_id"] == record.id


def test_an_unchanged_save_is_not_audited_twice(instance, monkeypatch):
    """Saving the same status again is a write, not a transition. An audit line
    per write would turn the trail into noise nobody reads."""
    emitted: list[str] = []
    monkeypatch.setattr(
        change_store, "audit",
        type("Stub", (), {"emit": staticmethod(lambda event, **f: emitted.append(event))}),
    )
    record = drafted()
    change_store.save(record)
    change_store.save(record)
    assert emitted == ["network.change.awaiting_approval"]


def test_the_audit_line_carries_the_hash_and_not_the_commands(instance, monkeypatch):
    """The hash ties the line to a text an approver read. The text itself lives
    in the change record, which is where a reader should have to go: an audit
    stream carrying device configuration is a different artefact with different
    handling, and ADR 0005 ships this one off-box."""
    emitted: list[dict] = []
    monkeypatch.setattr(
        change_store, "audit",
        type("Stub", (), {"emit": staticmethod(lambda event, **f: emitted.append(f))}),
    )
    record = drafted()
    record.commands = ["interface Gi0/1", "description finance uplink"]
    change_store.save(record)
    line = emitted[0]
    assert line["command_count"] == 2
    assert "description finance uplink" not in str(line)


# --- verify and rollback -----------------------------------------------------
#
# Both run through NetApply. They are tested at the tool rather than through the
# gate because the gate has already let the call through by then: what these
# check is what the tool refuses on its own, which is the part that stops a
# rollback being a way to reach a device without an approval.

def run_apply(agent, args):
    from plugins._ea_ansible.tools.net_apply import NetApply

    tool = NetApply(agent=agent, name="net_apply", method=None, args=args, message="", loop_data=None)
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(tool.execute())


@pytest.fixture
def configured(monkeypatch):
    from plugins._ea_ansible.helpers import config as plugin_config

    monkeypatch.setattr(plugin_config, "is_configured", lambda agent=None: True)


@pytest.mark.parametrize("action", ["verify", "rollback"])
@pytest.mark.parametrize("status", [AWAITING_APPROVAL, APPROVED])
def test_neither_runs_before_the_change_has_been_applied(instance, configured, action, status):
    """A rollback before an apply would push the undo of something that never
    happened, and a verify would report on a change nobody made."""
    record = change_store.save(drafted())
    assert status != APPROVED or change_store.approve(record, "asha")
    response = run_apply(FakeAgent(), {"action": action, "change_id": record.id})
    assert "nothing" in response.message.lower()


def test_a_rollback_needs_an_entry_the_approver_read(instance, configured):
    """No rollback entry means no reviewed way back. Inventing one is not the
    agent's to do, and the message says so rather than offering alternatives."""
    record = change_store.save(drafted(status=APPLIED, rollback_name=""))
    response = run_apply(FakeAgent(), {"action": "rollback", "change_id": record.id})
    assert "no rollback" in response.message.lower()
    assert "by hand" in response.message.lower()


def test_an_unknown_action_names_what_is_available(instance, configured):
    record = change_store.save(drafted(status=APPLIED))
    response = run_apply(FakeAgent(), {"action": "improvise", "change_id": record.id})
    for known in ("apply", "verify", "rollback", "close"):
        assert known in response.message


def test_a_mistyped_id_is_answered_with_the_changes_that_are_waiting(instance):
    # Seen on c3725-lab: the model copied the id with one character wrong and got only "there is no change".
    record = drafted()
    assert change_store.approve(record, "asha")
    wrong = record.id[:-1] + ("0" if record.id[-1] != "0" else "1")
    with pytest.raises(RepairableException) as refusal:
        run_gate(FakeAgent(), "net_apply", {"change_id": wrong})
    assert record.id in str(refusal.value) and "Copy the id exactly" in str(refusal.value)
