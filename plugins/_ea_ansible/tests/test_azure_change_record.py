"""The three Azure fields on the shared change record (build contract, "The change record").

One ledger serves device and Azure changes, so the first thing to prove is that adding the fields moved no hash
an engineer already approved.
"""

from __future__ import annotations

import pytest

from helpers import files, user_scope

from plugins._ea_ansible.helpers import change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL, ChangeRecord, plan_hash

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    with user_scope.user_scope("asha"):
        yield tmp_path


def _device() -> ChangeRecord:
    return ChangeRecord(
        requested_by="asha", status=AWAITING_APPROVAL, playbook_name="changes/access_vlan", commit="c" * 40,
        variables={"vlan_id": 300}, limit="sw4", commands=["interface Gi0/12", "switchport access vlan 300"],
        rollback_commands=["switchport access vlan 1"],
    )


def test_an_old_device_record_hashes_as_it_did_before_the_azure_fields():
    record = _device()
    # The formula as it stood before plan_digest existed: these exact parts and nothing more.
    before = plan_hash(
        playbook_name="changes/access_vlan", commit="c" * 40, variables={"vlan_id": 300}, limit="sw4",
        commands=["interface Gi0/12", "switchport access vlan 300"], rollback_commands=["switchport access vlan 1"],
    )
    assert record.current_hash == before


def test_the_plan_digest_is_part_of_the_hash_but_the_reference_and_documents_are_not():
    record = ChangeRecord(kind="azure_change", playbook_name="vnet", commands=["name=x"])
    base = record.current_hash
    record.reference = "CR-12345"
    record.documents = [{"version": "issued", "format": "pdf", "sha256": "a" * 64}]
    assert record.current_hash == base
    record.plan_digest = "d" * 64
    with_digest = record.current_hash
    assert with_digest != base
    record.plan_digest = "e" * 64
    assert record.current_hash != with_digest


def test_the_new_fields_round_trip_and_an_old_file_still_loads(instance):
    record = ChangeRecord(
        requested_by="asha", kind="azure_change", plan_digest="d" * 64, reference="SR-0001234",
        documents=[{"version": "issued", "format": "docx", "sha256": "b" * 64}],
    )
    change_store.save(record)
    loaded = change_store.load(record.id)
    assert (loaded.plan_digest, loaded.reference, loaded.documents) == (record.plan_digest, "SR-0001234", record.documents)
    old = _device().to_dict()
    for key in ("plan_digest", "reference", "documents"):
        old.pop(key)
    assert ChangeRecord.from_dict(old).reference == ""


def test_the_register_carries_the_reference(instance):
    record = change_store.save(ChangeRecord(requested_by="asha", kind="azure_change", reference="CR-1234"))
    assert change_store.summarise(record)["reference"] == "CR-1234"


def _capture(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        change_store, "audit", type("Stub", (), {"emit": staticmethod(lambda n, **f: events.append((n, f)))}),
    )
    return events


def test_an_azure_transition_is_audited_with_reference_digest_and_document_hash_only(instance, monkeypatch):
    events = _capture(monkeypatch)
    record = ChangeRecord(
        requested_by="asha", kind="azure_change", status=AWAITING_APPROVAL, plan_digest="d" * 64,
        commands=["secret-looking line"], documents=[
            {"version": "issued", "format": "docx", "sha256": "w" * 64},
            {"version": "issued", "format": "pdf", "sha256": "p" * 64},
        ],
    )
    change_store.save(record)
    assert change_store.approve(record, "asha", reference="CR-12345")
    assert [name for name, _ in events] == ["azure.change.drafted", "azure.change.approved"]
    line = events[1][1]
    assert (line["reference"], line["plan_digest"], line["document_sha256"]) == ("CR-12345", "d" * 64, "p" * 64)
    assert "secret-looking line" not in str(events)


def test_actions_and_suggestions_have_their_own_event_names_and_a_device_record_keeps_its_own(instance, monkeypatch):
    events = _capture(monkeypatch)
    change_store.save(ChangeRecord(kind="azure_action", status="applied"))
    change_store.save(ChangeRecord(kind="azure_suggestion", status="closed"))
    change_store.save(_device())
    assert [name for name, _ in events] == [
        "azure.action.applied", "azure.suggestion.written", "network.change.awaiting_approval",
    ]
