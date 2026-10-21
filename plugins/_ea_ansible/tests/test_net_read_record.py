"""A read is a ChangeRecord: its expiry, its approval, its preflight, its failures."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from helpers import files, user_scope

from plugins._ea_ansible.api.change_approve import ChangeApprove
from plugins._ea_ansible.api.changes_list import ChangesList
from plugins._ea_ansible.helpers import change_store, executor_client, failures, preflight
from plugins._ea_ansible.helpers.change_model import (
    APPROVED,
    AWAITING_APPROVAL,
    EXPIRED,
    ChangeRecord,
)

pytestmark = pytest.mark.usefixtures("database")  # the approval register is in PostgreSQL (ADR 0077)


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    with user_scope.user_scope("asha"):
        yield tmp_path


def read(**overrides) -> ChangeRecord:
    record = ChangeRecord(
        requested_by="asha", status=AWAITING_APPROVAL, kind="read",
        playbook_name="reads/interfaces", commit="c" * 40, limit="lab-sonic",
        commands=["show interfaces status"], expires_at=2000.0,
    )
    for key, value in overrides.items():
        setattr(record, key, value)
    return record


def approve(**body):
    return asyncio.run(ChangeApprove(None, None).process({"decision": "approve", **body}, None))


def test_expiry_and_kind_are_part_of_the_record():
    assert read().expired(2000.0) and not read().expired(1999.0)
    assert not ChangeRecord().expired(1e12)  # a change never expires
    assert read().current_hash != read(kind="change").current_hash
    # Hashes of existing changes must not move.
    assert ChangeRecord.from_dict({"commands": ["x"]}).kind == "change"


def test_a_read_needs_its_hash_and_it_must_match(instance):
    record = change_store.save(read(expires_at=None))
    assert not approve(change_id=record.id)["ok"]
    assert not approve(change_id=record.id, expected_hash="stale")["ok"]
    result = approve(change_id=record.id, expected_hash=record.current_hash)
    assert result["ok"] and change_store.load(record.id).status == APPROVED


def test_an_expired_read_cannot_be_approved_and_is_marked(instance):
    record = change_store.save(read(expires_at=1.0))
    assert "expired" in approve(change_id=record.id, expected_hash=record.current_hash)["error"]
    assert change_store.load(record.id).status == EXPIRED


def test_a_change_without_a_hash_still_approves(instance):
    record = change_store.save(read(kind="change", expires_at=None))
    assert approve(change_id=record.id)["ok"]


def test_the_register_lists_changes_only_unless_asked(instance):
    change_store.save(read())
    change_store.save(read(kind="change"))
    def kinds(**body):
        out = asyncio.run(ChangesList(None, None).process(body, None))
        return sorted(c["kind"] for c in out["changes"])
    assert kinds() == ["change"] and kinds(kind="read") == ["read"] and kinds(kind="all") == ["change", "read"]


def test_failures_are_classified_with_the_specific_cause_first():
    assert failures.classify("fatal: [lab-sonic]: UNREACHABLE! unable to open sonic cli")["code"] == "platform_mismatch"
    assert failures.classify("UNREACHABLE! Failed to connect: Permission denied (password)")["code"] == "credential_failed"
    assert failures.classify("No route to host")["code"] == "host_unreachable"
    assert failures.classify("read timed out")["code"] == "timeout"
    assert failures.classify("% Invalid input detected")["code"] == "command_rejected"
    assert failures.classify("unable to open shell")["code"] == "connection_failed"
    assert failures.classify("boom\n")["code"] == "unknown"
    # What IOS 12.4(24)T5 answered in the test lab of 2026-10-11; before, this was "unknown" with the recap line.
    old_ios = ("fatal: [r1]: FAILED! => {\"msg\": \"ssh connection failed: ssh connect failed: kex error : no match for "
               "method kex algos: server [diffie-hellman-group14-sha1,diffie-hellman-group1-sha1], client [curve25519-sha256]\"}")
    found = failures.classify(old_ios)
    assert found["code"] == "ssh_algorithms" and "legacy-ssh" in found["cause"]


ENTRY = SimpleNamespace(name="reads/interfaces", platforms=("sonic_community",),
                        read_commands={"sonic_community": ("show interfaces status",)})
DEVICE = {"name": "lab-sonic", "mgmt_ip": "172.30.30.11", "status": "active",
          "platform": "sonic_community", "site": {"name": "lab"}, "last_seen_at": "t"}
GOOD = {"lab-sonic": {"in_inventory": True, "address": "172.30.30.11", "port": 22, "reachable": True,
                      "credential": "lab-sonic", "credential_present": True,
                      "network_os": "sonic_community", "connection": "paramiko_ssh"}}


def run(monkeypatch, remote, device=DEVICE):
    monkeypatch.setattr(executor_client, "preflight", lambda agent, hosts: remote)
    return preflight.run_preflight(None, [device], ENTRY)


def failed(result):
    return [c["label"].split(": ", 1)[1] for c in result["checks"] if not c["ok"]]


def test_preflight_passes_and_reports_inventory_facts(monkeypatch):
    from plugins._ea_inventory.helpers import executor_sync
    monkeypatch.setitem(executor_sync.PLATFORM_VARS, "sonic_community",
                        {"ansible_network_os": "sonic_community", "ansible_connection": "paramiko_ssh"})
    result = run(monkeypatch, GOOD)
    assert result["ok"] and result["devices"][0]["credential"] == "lab-sonic"


def test_preflight_names_every_failed_check(monkeypatch):
    bad = {"lab-sonic": {**GOOD["lab-sonic"], "reachable": False, "credential_present": False}}
    assert {"reachable", "credential present"} <= set(failed(run(monkeypatch, bad)))
    assert "known to the executor" in failed(run(monkeypatch, {}))
    assert "active in Inventory" in failed(run(monkeypatch, GOOD, {**DEVICE, "status": "retired"}))
    assert "platform supported" in failed(run(monkeypatch, GOOD, {**DEVICE, "platform": "ios"}))
