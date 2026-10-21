"""An approval lives in PostgreSQL and is used exactly once (ADR 0077).

The record file is signed, and a signature does not stop a copy: an approved file put back after the apply was still
a valid approval. These cover the register (`change_approval`) that every gate now reads instead.
"""

from __future__ import annotations

import asyncio
import importlib
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from helpers import db, files, user_scope
from helpers.errors import RepairableException

from plugins._ea_ansible.helpers import change_store, conditions
from plugins._ea_ansible.helpers.change_model import (
    APPLIED, APPLYING, APPROVED, AWAITING_APPROVAL, CANCELLED, EXPIRED, REJECTED, SUPERSEDED, ChangeRecord,
)

pytestmark = pytest.mark.usefixtures("database")


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
    return change_store.save(record)


def approved(**overrides) -> ChangeRecord:
    record = drafted(**overrides)
    assert change_store.approve(record, "asha")
    return record


def keep_copy(record: ChangeRecord, tmp_path) -> str:
    return shutil.copy(change_store._path(record.id), tmp_path / "copy.json")


def put_back(copy: str, record: ChangeRecord) -> ChangeRecord:
    shutil.copy(copy, change_store._path(record.id))
    return change_store.load(record.id)


def gate_refuses(change_id: str) -> str:
    gate = importlib.import_module("plugins._ea_ansible.extensions.python.tool_execute_before._95_change_gate")
    agent = type("Agent", (), {"number": 0})()
    with pytest.raises(RepairableException) as refusal:
        asyncio.run(gate.NetworkChangeGate(agent=agent).execute(tool_name="net_apply", tool_args={"change_id": change_id}))
    return str(refusal.value)


def test_an_approved_file_put_back_after_the_apply_is_refused(instance, tmp_path):
    record = approved()
    copy = keep_copy(record, tmp_path)  # signed, approved, unexpired
    assert change_store.take(record)
    record.status = APPLIED
    change_store.save(record)

    replayed = put_back(copy, record)

    assert replayed.status == APPLIED  # the register's answer, not the file's
    assert not change_store.take(replayed)
    assert "not 'approved'" in gate_refuses(record.id)
    assert instance.count("network.change.tampered") == 1


def test_of_two_applies_of_one_approval_one_is_refused(instance):
    record = approved()
    twins = [change_store.load(record.id), change_store.load(record.id)]  # both read it while it was approved

    def apply(twin):
        with user_scope.user_scope("asha"):
            return change_store.take(twin)

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(apply, twins)) == [False, True]
    assert change_store.load(record.id).status == APPLYING


def test_an_approval_for_one_plan_does_not_run_another(instance):
    record = approved()
    record.commands = ["no vlan 300"]  # what would run now is not what was approved

    assert conditions.first_failure(record, "asha")
    assert not change_store.take(record)
    assert change_store.load(record.id).status == APPROVED  # the approval of the plan that was read is untouched


def test_a_record_with_no_row_is_not_approved(instance):
    record = approved()
    with db.unscoped_transaction() as conn:  # as before this register existed, or a file put there by hand
        conn.execute("DELETE FROM change_approval WHERE id = %s", (record.id,))

    loaded = change_store.load(record.id)

    assert loaded.status == AWAITING_APPROVAL and loaded.approved_by == "" and loaded.approved_hash == ""
    assert not change_store.take(loaded)
    assert change_store.approve(loaded, "asha") and change_store.take(loaded)  # the engineer approves again


def test_another_user_cannot_see_or_use_the_row(instance):
    record = approved()
    shutil.copy(change_store._path(record.id), change_store._path(record.id, "ravi"))

    with user_scope.user_scope("ravi"):
        theirs = change_store.load(record.id)
        assert theirs.status == AWAITING_APPROVAL
        assert not change_store.take(theirs)
        assert not change_store.approve(theirs, "ravi")  # the id is asha's
    assert change_store.load(record.id, "ravi").status == AWAITING_APPROVAL
    with user_scope.user_scope(None):  # a background path that pushed no scope sees nothing
        assert change_store._rows(None, record.id) == {}
    assert change_store.load(record.id).status == APPROVED and change_store.take(record)


@pytest.mark.parametrize("ended", [CANCELLED, SUPERSEDED, REJECTED, EXPIRED])
def test_a_change_that_ended_cannot_be_run_from_an_old_file(instance, tmp_path, ended):
    record = approved()
    copy = keep_copy(record, tmp_path)
    record.status = ended
    change_store.save(record)

    replayed = put_back(copy, record)

    assert replayed.status == ended
    assert not change_store.take(replayed)
    assert not change_store.approve(replayed, "asha")  # and it is not approved again
    assert f"is '{ended}'" in gate_refuses(record.id)


def test_an_expired_approval_is_not_taken(instance):
    record = approved(expires_at=time.time() - 1)
    assert not change_store.take(record)


def test_a_file_cannot_give_an_approval_more_time(instance, tmp_path):
    record = approved(expires_at=time.time() + 600)
    until = change_store.load(record.id).expires_at
    record.expires_at = time.time() + 99999
    change_store.save(record)  # saved while approved: the register is not written
    assert change_store.load(record.id).expires_at == until


def test_saving_a_record_as_approved_does_not_approve_it(instance):
    record = drafted()
    record.status, record.approved_by, record.approved_hash = APPROVED, "asha", record.current_hash
    with pytest.raises(change_store.NotApproved):
        change_store.save(record)
    record.status = APPLYING
    with pytest.raises(change_store.NotApproved):
        change_store.save(record)
    assert change_store.load(record.id).status == AWAITING_APPROVAL


def test_a_step_of_a_set_is_taken_once_and_in_order(instance, tmp_path):
    record = approved()
    assert not change_store.take(record, step=2)  # step 1 has not been taken
    assert change_store.take(record, step=1)
    copy = keep_copy(record, tmp_path)
    assert change_store.take(record, step=2)
    replayed = put_back(copy, record)  # a file from when step 1 was the last one taken
    assert not change_store.take(replayed, step=1) and not change_store.take(replayed, step=2)
    change_store.give_back(record, step=2)  # the executor turned step 2 away: it can be taken again, once
    assert change_store.take(record, step=2) and not change_store.take(record, step=2)


def test_an_approval_given_back_can_be_used_once(instance):
    record = approved()
    assert change_store.take(record)
    change_store.give_back(record)
    assert change_store.load(record.id).status == APPROVED
    assert change_store.take(record) and not change_store.take(record)


def test_the_card_endpoints_report_the_register(instance, tmp_path):
    from plugins._ea_ansible.api.changes_list import ChangesList
    from plugins._ea_ansible.api.changes_review import ChangesReview

    record = approved()
    copy = keep_copy(record, tmp_path)
    assert change_store.take(record)
    record.status = APPLIED
    change_store.save(record)
    put_back(copy, record)
    shutil.copy(copy, change_store._path(record.id))  # and again, so the endpoints read the copied file themselves

    one = asyncio.run(ChangesList(None, None).process({"change_id": record.id}, None))["change"]
    listed = asyncio.run(ChangesList(None, None).process({}, None))["changes"]
    review = asyncio.run(ChangesReview(None, None).process({"change_id": record.id}, None))["change"]
    assert one["status"] == review["status"] == APPLIED
    assert [c["status"] for c in listed] == [APPLIED]


def test_a_record_loaded_before_the_approval_cannot_undo_it(instance):
    """A handler that loaded the record while it awaited approval and saves it after the approve endpoint ran must not
    write `awaiting_approval` and empty approval columns over the row (audit 3, L4)."""
    record = drafted(reference="")
    stale = change_store.load(record.id)
    assert change_store.approve(record, "asha", reference="CR-1")

    change_store.save(stale)

    now = change_store.load(record.id)
    assert (now.status, now.approved_by, now.reference) == (APPROVED, "asha", "CR-1")
    assert change_store.take(now)


def test_an_applied_change_is_not_reopened_by_a_stale_save(instance):
    record = approved()
    stale = change_store.load(record.id)
    stale.status = AWAITING_APPROVAL
    assert change_store.take(record)
    record.status = APPLIED
    change_store.save(record)

    change_store.save(stale)

    assert change_store.load(record.id).status == APPLIED
    assert not change_store.approve(change_store.load(record.id), "asha")


def test_the_approval_keeps_the_registers_expiry_not_the_records(instance):
    """The expiry is the register's, written when the change was drafted; a record carrying another one (a rewritten
    file, a bug) does not extend the approval (audit 3, L5)."""
    record = drafted()
    drafted_expiry = record.expires_at
    record.expires_at = time.time() + 365 * 24 * 3600
    assert change_store.approve(record, "asha")
    assert change_store.load(record.id).expires_at == pytest.approx(drafted_expiry)


def test_document_bookkeeping_in_an_unsigned_file_is_dropped(instance):
    import json

    record = drafted()
    path = change_store._path(record.id)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    data["documents"] = [{"version": "issued", "format": "pdf", "sha256": "x", "downloaded_at": 1.0}]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)

    assert change_store.load(record.id).documents == []
    assert "network.change.tampered" in instance
