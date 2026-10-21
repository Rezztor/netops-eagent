"""An approval is believed only when this server signed it.

A change record is a file under the user's directory, so something other than
`change_store` can rewrite it. These cover what the gates trust: a record that
says `approved` without the server's seal is put back to `awaiting_approval`.
"""

from __future__ import annotations

import json
import shutil
import time

import pytest

from helpers import files, user_scope

from plugins._ea_ansible.helpers import change_store, conditions
from plugins._ea_ansible.helpers.change_model import APPROVED, AWAITING_APPROVAL, ChangeRecord

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setenv(change_store.ENV_KEY, "a-server-secret")
    events = []
    monkeypatch.setattr(change_store, "audit", type("A", (), {"emit": staticmethod(lambda name, **f: events.append(name))}))
    with user_scope.user_scope("asha"):
        yield events


def drafted(**overrides) -> ChangeRecord:
    record = ChangeRecord(
        requested_by="asha", status=AWAITING_APPROVAL, playbook_name="changes/ios_vlan_name", commit="c" * 40,
        variables={"vlan_id": 300, "vlan_name": "users"}, limit="sw1", commands=["vlan 300", "name users"],
        expires_at=time.time() + 600,
    )
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


def approved(**overrides) -> ChangeRecord:
    record = drafted(**overrides)
    assert change_store.approve(record, "asha")
    return record


def rewrite(change_id: str, user_id: str | None = None, **fields) -> None:
    """What a file tool does: edit the JSON in place, without `change_store`."""
    path = change_store._path(change_id, user_id)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    data.update(fields)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def test_an_approval_the_server_recorded_is_still_approved(instance):
    record = approved()
    loaded = change_store.load(record.id)
    assert loaded.status == APPROVED
    assert conditions.first_failure(loaded, "asha") is None
    assert "network.change.tampered" not in instance


def test_a_record_rewritten_to_approved_is_not_approved(instance):
    record = change_store.save(drafted())
    rewrite(record.id, status="approved", approved_by="asha", approved_hash=record.current_hash)

    loaded = change_store.load(record.id)

    assert loaded.status == AWAITING_APPROVAL and loaded.approved_by == "" and loaded.approved_hash == ""
    assert conditions.first_failure(loaded, "asha")
    assert instance.count("network.change.tampered") == 1
    change_store.load(record.id)  # the file was put right, so it is reported once
    assert instance.count("network.change.tampered") == 1
    assert [r.status for r in change_store.list_records()] == [AWAITING_APPROVAL]


def test_a_record_with_no_seal_is_not_approved(instance):
    """A record approved before the seal existed: the engineer approves again."""
    record = approved()
    rewrite(record.id, seal="")
    assert change_store.load(record.id).status == AWAITING_APPROVAL


def test_an_approval_copied_onto_another_change_is_refused(instance):
    good, other = approved(), change_store.save(drafted(commands=["no vlan 1"]))
    with open(change_store._path(good.id), encoding="utf-8") as handle:
        signed = json.load(handle)
    rewrite(other.id, status="approved", approved_by="asha", approved_hash=other.current_hash, seal=signed["seal"])
    assert change_store.load(other.id).status == AWAITING_APPROVAL


def test_an_approval_copied_from_another_user_is_refused(instance):
    with user_scope.user_scope("ravi"):
        theirs = approved(requested_by="ravi")
    shutil.copy(change_store._path(theirs.id, "ravi"), change_store._path(theirs.id, "asha"))
    assert change_store.load(theirs.id, "ravi").status == APPROVED
    assert change_store.load(theirs.id, "asha").status == AWAITING_APPROVAL


def test_an_approved_record_cannot_be_given_more_time_or_another_reference(instance):
    for fields in ({"expires_at": time.time() + 99999}, {"reference": "CR-1"}, {"documents": [{"version": "issued"}]}):
        record = approved()
        rewrite(record.id, **fields)
        assert change_store.load(record.id).status == AWAITING_APPROVAL


def test_an_applied_record_cannot_be_put_back_to_approved(instance):
    record = approved()
    record.status = "applied"
    change_store.save(record)
    rewrite(record.id, status="approved")
    loaded = change_store.load(record.id)
    assert loaded.status == "applied" and not change_store.take(loaded)  # the register's answer (ADR 0077), not the file's


def test_the_file_carries_no_ready_made_hash_and_the_api_no_seal(instance):
    record = approved()
    with open(change_store._path(record.id), encoding="utf-8") as handle:
        stored = json.load(handle)
    assert "current_hash" not in stored and stored["seal"]
    assert "seal" not in record.to_dict() and record.to_dict()["current_hash"]


def test_the_gate_refuses_a_record_rewritten_to_approved(instance):
    import asyncio
    import importlib

    from helpers.errors import RepairableException

    gate = importlib.import_module("plugins._ea_ansible.extensions.python.tool_execute_before._95_change_gate")
    record = change_store.save(drafted())
    rewrite(record.id, status="approved", approved_by="asha", approved_hash=record.current_hash)
    agent = type("Agent", (), {"number": 0})()
    with pytest.raises(RepairableException, match="not 'approved'"):
        asyncio.run(gate.NetworkChangeGate(agent=agent).execute(tool_name="net_apply", tool_args={"change_id": record.id}))
