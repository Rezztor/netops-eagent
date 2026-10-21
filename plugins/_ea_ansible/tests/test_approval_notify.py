"""Tell the person who has to sign: who is told that a change waits, and what the Approvals page lists.

Nobody is told about a change they cannot decide: a device change goes to every holder of `network.approve`, an Azure
change to its requester only (the endpoints' own rules), a read to nobody. The approval model is untouched; these only
pin who hears about it.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from helpers import files, rbac, user_scope, users
from helpers.notification import NotificationManager

from plugins._ea_ansible.api.approvals_waiting import ApprovalsWaiting
from plugins._ea_ansible.helpers import approvers, change_store
from plugins._ea_ansible.helpers.change_model import AWAITING_APPROVAL, ChangeRecord

pytestmark = pytest.mark.usefixtures("database")


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setenv(change_store.ENV_KEY, "a-server-secret")
    monkeypatch.setattr(change_store, "audit", None)
    told: list[tuple[str | None, str, str]] = []
    monkeypatch.setattr(
        NotificationManager, "send_notification",
        staticmethod(lambda *a, **k: told.append((user_scope.get_current_user_id(), a[2], k.get("link", "")))),
    )
    people = {
        name: users.create_user(name, "correcthorsebattery", role, email=f"{name}@corp.example")["id"]
        for name, role in (("root", rbac.ROLE_ADMIN), ("asha", rbac.ROLE_POWER), ("ravi", rbac.ROLE_POWER),
                           ("uma", rbac.ROLE_USER), ("gone", rbac.ROLE_POWER))
    }
    users.update_user("gone", disabled=True)
    return people, told


def draft(owner: str, **overrides) -> ChangeRecord:
    record = ChangeRecord(
        requested_by=owner, status=AWAITING_APPROVAL, kind="change", tier="change-high", confirm="sw1",
        playbook_name="changes/ios_vlan_name", summary="name VLAN 300", limit="sw1", commands=["vlan 300"],
        expires_at=time.time() + 1800,
    )
    for key, value in overrides.items():
        setattr(record, key, value)
    with user_scope.user_scope(owner):
        return change_store.save(record)


def test_a_device_change_tells_every_approver_with_a_link_and_no_one_else(instance):
    people, told = instance
    record = draft(people["asha"])

    assert sorted(who for who, _, _ in told) == sorted([people["root"], people["asha"], people["ravi"]])
    assert people["uma"] not in {who for who, _, _ in told}  # no network.approve
    assert people["gone"] not in {who for who, _, _ in told}  # disabled
    assert {link for _, _, link in told} == {f"/approvals?change={record.id}"}
    assert "name VLAN 300 on sw1" in told[0][1] and "expires at" in told[0][1]


def test_only_the_transition_into_waiting_tells_anyone(instance):
    people, told = instance
    record = draft(people["asha"])
    told.clear()
    with user_scope.user_scope(people["asha"]):
        change_store.save(record)  # saved again, still waiting
    assert told == []


def test_a_read_or_a_rollback_card_tells_nobody(instance):
    people, told = instance
    draft(people["asha"], kind="read", tier="read")
    draft(people["asha"], kind="rollback", tier="")
    draft(people["asha"], kind="plan", tier="read")
    assert told == []


def test_an_azure_change_tells_its_requester_only(instance):
    people, told = instance
    draft(people["asha"], kind="azure_change", tier="", confirm="")
    assert [who for who, _, _ in told] == [people["asha"]]


@pytest.fixture
def mail(monkeypatch):
    """Mail configured, sent synchronously, on a clock the test moves; timers are kept to be fired by hand."""
    from helpers import mailer

    sent: list[tuple[str, str, str]] = []
    clock = {"now": 1_000_000.0}
    timers: list[tuple[float, object]] = []
    monkeypatch.setattr(mailer, "is_available", lambda: (True, ""))
    monkeypatch.setattr(mailer, "config", lambda: {"base_url": "https://rezztor.corp.example"})
    monkeypatch.setattr(mailer, "send_to_user", lambda user_id, **k: sent.append((user_id, k["subject"], k["body"])))
    monkeypatch.setattr(approvers, "_background", lambda work: work())
    monkeypatch.setattr(approvers, "_later", lambda delay, work: timers.append((delay, work)))
    monkeypatch.setattr(approvers, "_now", lambda: clock["now"])
    monkeypatch.setattr(approvers, "_mailed_at", {})
    monkeypatch.setattr(approvers, "_held", {})
    return sent, clock, timers


def test_mail_goes_to_the_other_approvers_accounts_when_mail_is_configured(instance, mail, monkeypatch):
    from helpers import mailer

    people, _ = instance
    sent, _, _ = mail
    record = draft(people["asha"])
    assert sorted(who for who, _, _ in sent) == sorted([people["root"], people["ravi"]])  # not the requester, uma or the disabled
    assert f"https://rezztor.corp.example/app/approvals?change={record.id}" in sent[0][2]

    sent.clear()
    monkeypatch.setattr(mailer, "is_available", lambda: (False, "Email is turned off in settings."))
    draft(people["asha"])
    assert sent == []


def test_an_approver_gets_at_most_one_mail_in_ten_minutes_with_what_waited_batched(instance, mail):
    people, told = instance
    sent, clock, timers = mail
    draft(people["asha"], summary="first")
    assert sorted(who for who, _, _ in sent) == sorted([people["root"], people["ravi"]])
    sent.clear()

    clock["now"] += 60
    second = draft(people["asha"], summary="second")
    clock["now"] += 60
    draft(people["asha"], summary="third")
    clock["now"] += 60
    draft(people["asha"], summary="last in the window")
    assert sent == []  # inside the window: held
    assert len(timers) == 2 and all(delay == approvers.MAIL_EVERY - 60 for delay, _ in timers)  # one per approver
    assert len(told) == 3 * 4  # the bell is still told about every change, at once

    with user_scope.user_scope(people["asha"]):
        gone = change_store.load(second.id)
        gone.status = "cancelled"
        change_store.save(gone)
    clock["now"] += approvers.MAIL_EVERY
    for _, fire in timers:
        fire()
    assert sorted(who for who, _, _ in sent) == sorted([people["root"], people["ravi"]])  # one mail each
    subject, body = sent[0][1], sent[0][2]
    assert subject == "2 changes need your approval"
    assert "third" in body and "last in the window" in body and "second" not in body  # cancelled meanwhile

    sent.clear()
    timers.clear()
    clock["now"] += 30
    draft(people["asha"], summary="after the batch")
    assert sent == [] and len(timers) == 2  # the batch mail started a new window


def test_a_failing_notification_never_breaks_the_save(instance, monkeypatch):
    people, _ = instance
    monkeypatch.setattr(approvers, "approvers", lambda *a: 1 / 0)
    record = draft(people["asha"])
    with user_scope.user_scope(people["asha"]):
        assert change_store.load(record.id).status == AWAITING_APPROVAL


def waiting(viewer: str) -> list[dict]:
    with user_scope.user_scope(viewer):
        return asyncio.run(ApprovalsWaiting(None, None).process({}, None))["changes"]


def test_the_approvals_page_lists_what_the_viewer_may_sign_oldest_first(instance):
    people, _ = instance
    older = draft(people["asha"], created_at=time.time() - 600)
    newer = draft(people["ravi"])
    azure = draft(people["asha"], kind="azure_change", tier="", confirm="")
    draft(people["asha"], kind="read", tier="read")
    draft(people["asha"], expires_at=time.time() - 1)  # expired: cannot be signed any more

    assert ApprovalsWaiting.required_permission() == rbac.PERM_NETWORK_APPROVE
    seen = waiting(people["ravi"])
    assert [row["id"] for row in seen] == [older.id, newer.id]  # not asha's Azure change, not the read
    assert seen[0]["expires_at"] == older.expires_at and seen[0]["confirm"] == "sw1"
    assert seen[0]["requested_by_name"] == "asha" and seen[0]["context_id"] == ""
    assert [row["id"] for row in waiting(people["asha"])] == [older.id, newer.id, azure.id]


def test_a_user_without_the_permission_sees_nothing(instance):
    people, told = instance
    draft(people["asha"])
    assert people["uma"] not in {who for who, _, _ in told}
    assert waiting(people["uma"]) == []
