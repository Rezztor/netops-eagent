"""kvp holds instance values and per-user values, and must not confuse them.

It began as "generic persistent key/value", which is the right shape for the one
thing it was built for: `memory_embedding_default` names the model this
deployment indexed with, and there is one of those. Then per-user state moved in
beside it through the same two functions, and `plugin_pin_to_top` became a single
row holding every account's pinned chats and tasks -- returned to whoever asked,
and deletable by them. `browser_open_tabs` did the same for the URLs each user's
agent had open.

`helpers/schema/0008_kvp_is_scoped.sql` gives the table a stated scope, an owner
and a policy. These tests check the policy, not the WHERE clauses: the point of
ADR 0034 is that a filter someone forgets is the bug, so the assertions that
matter here are the ones that would still pass if every WHERE in helpers/kvp.py
were deleted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import kvp, user_scope  # noqa: E402

ALICE = "alice000000000000000000000000000a"
BOB = "bob00000000000000000000000000000b"


def test_a_per_user_value_is_invisible_to_another_account(database):
    with user_scope.user_scope(ALICE):
        kvp.set_user_persistent("plugin_pin_to_top", {"chat": {"alice-chat": 1.0}})
        assert kvp.get_user_persistent("plugin_pin_to_top") == {
            "chat": {"alice-chat": 1.0}
        }

    with user_scope.user_scope(BOB):
        assert kvp.get_user_persistent("plugin_pin_to_top", "absent") == "absent"


def test_one_account_cannot_overwrite_another_accounts_value(database):
    with user_scope.user_scope(ALICE):
        kvp.set_user_persistent("plugin_pin_to_top", {"chat": {"alice-chat": 1.0}})

    with user_scope.user_scope(BOB):
        kvp.set_user_persistent("plugin_pin_to_top", {"chat": {"bob-chat": 2.0}})
        kvp.remove_user_persistent("plugin_pin_to_top")

    with user_scope.user_scope(ALICE):
        assert kvp.get_user_persistent("plugin_pin_to_top") == {
            "chat": {"alice-chat": 1.0}
        }


def test_the_same_key_holds_a_row_per_account(database):
    """The key alone stopped being unique, which is the schema change that makes
    the rest of this possible -- a primary key on `key` would have made the
    second write an update of the first."""
    for owner, value in ((ALICE, "a"), (BOB, "b")):
        with user_scope.user_scope(owner):
            kvp.set_user_persistent("plugin_pin_to_top", value)

    for owner, value in ((ALICE, "a"), (BOB, "b")):
        with user_scope.user_scope(owner):
            assert kvp.get_user_persistent("plugin_pin_to_top") == value


def test_an_instance_value_is_shared_and_a_user_value_is_not(database):
    """Both live in this table on purpose; the scope column is what separates
    them. The embedding-model marker must stay readable with no user in scope,
    because the job loop reads it that way."""
    kvp.set_persistent("memory_embedding_default", {"model_name": "shared"})

    with user_scope.user_scope(ALICE):
        assert kvp.get_persistent("memory_embedding_default") == {"model_name": "shared"}
        kvp.set_user_persistent("memory_embedding_default", {"model_name": "alices"})

    with user_scope.user_scope(BOB):
        assert kvp.get_persistent("memory_embedding_default") == {"model_name": "shared"}

    # Outside any scope, as the job loop runs.
    assert kvp.get_persistent("memory_embedding_default") == {"model_name": "shared"}
    assert kvp.get_user_persistent("memory_embedding_default", "absent") == "absent"


def test_writing_a_per_user_value_with_no_user_in_scope_is_refused(database):
    """Silently writing an unowned row is the shape this migration removed: the
    row would be `scope = 'user'` with a NULL owner, which the CHECK rejects --
    and if it did not, it would be a row nobody could read and nobody owned."""
    assert user_scope.get_current_user_id() is None
    with pytest.raises(ValueError):
        kvp.set_user_persistent("plugin_pin_to_top", {"chat": {}})


def test_a_glob_does_not_enumerate_other_accounts_keys(database):
    with user_scope.user_scope(ALICE):
        kvp.set_user_persistent("plugin_pin_to_top", {"chat": {}})
    kvp.set_persistent("memory_embedding_default", {"model_name": "shared"})

    with user_scope.user_scope(BOB):
        assert "plugin_pin_to_top" not in kvp.find_persistent("*")
        assert "memory_embedding_default" in kvp.find_persistent("*")


def test_the_policy_is_what_enforces_this_not_the_query(database):
    """Deleting the WHERE clauses in helpers/kvp.py must not open the table.

    Asserted against the schema rather than by mutating the module: the policy
    and FORCE are the guarantee, and a future refactor of kvp.py cannot quietly
    become the thing standing between two accounts.
    """
    from helpers import db

    with db.transaction(db.NO_SCOPE) as conn:
        forced = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'kvp'"
        ).fetchone()
        policies = conn.execute(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'kvp'"
        ).fetchone()

    assert forced == (True, True), "kvp must have row-level security, forced"
    assert policies[0] >= 1, "kvp must carry a policy"


def test_pinned_chats_do_not_cross_accounts(database):
    """The bug as a user would meet it, not as the store sees it.

    The tests above would all still pass if `plugins/_pin_to_top` went back to
    calling `get_persistent`, because they exercise kvp directly. This one fails
    in that case, which is the one that matters: it is the sidebar showing you
    somebody else's pinned conversations.
    """
    from plugins._pin_to_top.helpers import pins

    with user_scope.user_scope(ALICE):
        pins.toggle_pin("chat", "alice-private-conversation")
        assert "alice-private-conversation" in pins.get_pins()["chat"]

    with user_scope.user_scope(BOB):
        assert pins.get_pins() == {"chat": {}, "task": {}}
        # And cannot unpin what it cannot see.
        pins.toggle_pin("chat", "alice-private-conversation")

    with user_scope.user_scope(ALICE):
        assert "alice-private-conversation" in pins.get_pins()["chat"]


def test_open_browser_tabs_do_not_cross_accounts(database):
    """Same shape, different key: BROWSER_TABS_KEY held the URLs every user's
    agent had open in one instance-wide row."""
    with user_scope.user_scope(ALICE):
        kvp.set_user_persistent(
            "browser_open_tabs", {"version": 1, "tabs": [{"url": "https://alice.internal"}]}
        )

    with user_scope.user_scope(BOB):
        assert kvp.get_user_persistent("browser_open_tabs") is None
