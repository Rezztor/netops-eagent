"""A notification belongs to the account it was raised for.

One process holds one NotificationManager and its items had no owner, so a
brand-new `user` account saw another account's change plan and sign-in codes in
its bell, and "Mark read" and "Clear" acted on everybody's. These pin the rule:
an item carries the user scope it was created under, every read and write is
limited to the caller's own, and an item raised with no scope (startup, system)
goes to administrators only.
"""

from __future__ import annotations

import pytest

from helpers import rbac, user_scope
from helpers.notification import NotificationManager, NotificationPriority, NotificationType

ROLES = {"alice": rbac.ROLE_USER, "bob": rbac.ROLE_USER, "root": rbac.ROLE_ADMIN}


@pytest.fixture
def manager(monkeypatch):
    from helpers import users

    monkeypatch.setattr(
        users, "get_user_by_id", lambda user_id: {"id": user_id, "role": ROLES[user_id]}
    )
    return NotificationManager()


def _add(manager, message, **kwargs):
    return manager.add_notification(
        NotificationType.INFO, NotificationPriority.NORMAL, message, **kwargs
    )


def _seen(manager):
    """Everything the current caller can read, by every route that reads."""
    pushed = [item["message"] for item in manager.output_with_state(start=0)[0]]
    history = [item["message"] for item in manager.output_all()]
    recent = [item.message for item in manager.get_recent_notifications(60)]
    assert pushed == history == recent
    return pushed


def test_one_account_never_sees_marks_or_clears_anothers(manager):
    with user_scope.user_scope("alice"):
        mine = _add(manager, "alice's sign-in code")
    with user_scope.user_scope("bob"):
        _add(manager, "bob's own")

        assert _seen(manager) == ["bob's own"]
        assert manager.mark_read_by_ids([mine.id]) == 0
        manager.mark_all_read()
        manager.clear_all()
        assert _seen(manager) == []

    with user_scope.user_scope("alice"):
        assert _seen(manager) == ["alice's sign-in code"]
        assert mine.read is False


def test_an_id_cannot_be_used_to_rewrite_anothers_item(manager):
    with user_scope.user_scope("alice"):
        mine = _add(manager, "alice's", id="shared-id")
    with user_scope.user_scope("bob"):
        theirs = _add(manager, "bob's", id="shared-id")

    assert mine.message == "alice's"
    assert theirs.owner_id == "bob" and theirs.id != mine.id


def test_a_system_item_goes_to_administrators_only(manager):
    system = _add(manager, "raised at startup")
    assert system.owner_id is None

    with user_scope.user_scope("alice"):
        assert _seen(manager) == []
        manager.clear_all()
    with user_scope.user_scope("root"):
        assert _seen(manager) == ["raised at startup"]
        manager.clear_all()
        assert _seen(manager) == []


@pytest.mark.asyncio
async def test_the_poll_shows_neither_anothers_notifications_nor_their_log(
    database, tmp_path, monkeypatch
):
    from agent import AgentConfig, AgentContext
    from helpers import files, state_snapshot, users
    from testsupport import build_instance_root

    monkeypatch.setattr(files, "_base_dir", build_instance_root(tmp_path))
    monkeypatch.setattr(
        users, "get_user_by_id", lambda user_id: {"id": user_id, "role": rbac.ROLE_USER}
    )
    monkeypatch.setattr(AgentContext, "_notification_manager", NotificationManager())

    AgentContext.remove("ctx-alice")
    try:
        with user_scope.user_scope("alice"):
            context = AgentContext(
                config=AgentConfig(mcp_servers="", profile="default"),
                id="ctx-alice",
                set_current=False,
            )
            context.log.log(type="info", content="alice's conversation")
            _add(AgentContext.get_notification_manager(), "alice's plan")
            own = await state_snapshot.build_snapshot(
                context="ctx-alice", log_from=0, notifications_from=0, timezone="UTC"
            )
        with user_scope.user_scope("bob"):
            other = await state_snapshot.build_snapshot(
                context="ctx-alice", log_from=0, notifications_from=0, timezone="UTC"
            )
    finally:
        AgentContext.remove("ctx-alice")

    assert own["context"] == "ctx-alice" and own["logs"]
    assert [item["message"] for item in own["notifications"]] == ["alice's plan"]
    assert other["context"] == "" and other["logs"] == []
    assert other["notifications"] == []


def test_one_accounts_notifications_do_not_push_out_anothers(manager):
    """The list held 100 items for the whole instance and dropped the oldest,
    whoever they belonged to. An account posting 101 -- `notification_create`
    is open to every role -- emptied every other account's bell, the
    administrator's system notices included; fifty people at work did the
    same to each other slowly."""
    with user_scope.user_scope("alice"):
        _add(manager, "alice: your change was applied")
    _add(manager, "system: the licence ends in 14 days")  # no account in scope: a system notice

    with user_scope.user_scope("bob"):
        for number in range(manager.max_notifications + 5):
            _add(manager, f"bob {number}")
        mine = _seen(manager)
        assert len(mine) == manager.max_notifications, "an account's own list is still capped"
        assert mine[-1] == f"bob {manager.max_notifications + 4}" and "bob 0" not in mine

    with user_scope.user_scope("alice"):
        assert _seen(manager) == ["alice: your change was applied"]
    with user_scope.user_scope("root"):
        assert "system: the licence ends in 14 days" in _seen(manager)


def test_numbers_and_pending_updates_stay_true_after_items_are_dropped(manager):
    with user_scope.user_scope("alice"):
        _add(manager, "alice 0")
    with user_scope.user_scope("bob"):
        for number in range(manager.max_notifications + 3):
            _add(manager, f"bob {number}")

    assert [item.no for item in manager.notifications] == list(range(len(manager.notifications)))
    assert all(0 <= no < len(manager.notifications) for no in manager.updates)


def test_an_item_carries_its_link_into_the_bell(manager):
    with user_scope.user_scope("alice"):
        _add(manager, "a change waits", link="/approvals?change=abc")
        assert [item["link"] for item in manager.output_all()] == ["/approvals?change=abc"]
        _add(manager, "plain")
        assert manager.output_all()[-1]["link"] == ""
