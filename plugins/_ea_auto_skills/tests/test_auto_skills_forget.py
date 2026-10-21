"""Removing a skill the agent wrote for you.

The feature applies skills without asking, so undoing that has to be within
reach of the person it happened to. These tests pin the two things that makes
risky: that it reaches only the caller's own skills, and that discarding a skill
also retires the proposal the queue is holding for it.
"""

import asyncio
import threading

import pytest

from helpers import files, user_scope
from plugins._ea_auto_skills.api.auto_skills_forget import AutoSkillsForget
from plugins._ea_auto_skills.helpers import config, promote, store
from plugins._ea_auto_skills.helpers.capture import Candidate
from plugins._ea_auto_skills.helpers.review import Draft
from plugins._ea_auto_skills.helpers.validate import render_skill_md, Result


SETTINGS = {
    "mode": "personal",
    "min_tool_calls": 2,
    "max_personal_skills": 25,
    "max_pending_promotions": 50,
    "max_skill_md_chars": 10000,
    "max_skill_bytes": 40000,
    "review_timeout_seconds": 120,
}


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(config, "is_enabled", lambda: True)
    (tmp_path / "usr" / "skills").mkdir(parents=True)
    return tmp_path


def _draft(name="trace-a-slow-endpoint"):
    return Draft(
        action="create",
        name=name,
        description="Find why one endpoint is slow.",
        body="1. Compare against a neighbouring route.\n2. Check query counts.",
        rationale="Profiling first wastes the most time.",
    )


def _induce(instance, user_id, draft=None):
    """Produce an induced skill and its queued proposal, the way a real turn does."""
    draft = draft or _draft()
    root = instance / "usr" / "users" / user_id / "skills"
    root.mkdir(parents=True, exist_ok=True)
    result = Result(
        ok=True,
        skill_md=render_skill_md(draft),
        target_dir=str(root / draft.name),
    )
    applied = store.apply(draft, result, SETTINGS, user_id=user_id)
    promotion_id = promote.queue(
        draft,
        Candidate(
            sequence=9,
            tool_calls=3,
            evidence="user: it got slow\nai: found an N+1",
            loaded_skills=[],
            context_id="ctx-1",
        ),
        SETTINGS,
        user_id=user_id,
        applied=applied,
    )
    return draft, promotion_id


def _forget(payload, user_id):
    handler = AutoSkillsForget(app=None, thread_lock=threading.RLock())
    with user_scope.user_scope(user_id):
        return asyncio.run(handler.process(payload, request=None))


# --- the ordinary case ------------------------------------------------------

def test_a_user_can_remove_a_skill_the_agent_wrote_for_them(instance):
    draft, _ = _induce(instance, "alice")
    folder = instance / "usr" / "users" / "alice" / "skills" / draft.name
    assert folder.is_dir()

    result = _forget({"name": draft.name}, "alice")

    assert result["ok"], result
    assert not folder.exists()
    assert draft.name not in store.read_ledger("alice")


def test_the_name_is_taken_as_given_but_normalised(instance):
    draft, _ = _induce(instance, "alice")
    assert _forget({"name": f"  {draft.name.upper()}  "}, "alice")["ok"]


# --- the boundary -----------------------------------------------------------

def test_one_user_cannot_reach_anothers_skills(instance):
    """There is no user id in the payload; the caller is whoever the session says."""
    draft, _ = _induce(instance, "alice")

    result = _forget({"name": draft.name}, "bob")

    assert not result["ok"]
    assert (instance / "usr" / "users" / "alice" / "skills" / draft.name).is_dir()
    assert draft.name in store.read_ledger("alice")


def test_supplying_a_user_id_in_the_payload_changes_nothing(instance):
    draft, _ = _induce(instance, "alice")
    result = _forget({"name": draft.name, "user_id": "alice"}, "bob")
    assert not result["ok"]
    assert (instance / "usr" / "users" / "alice" / "skills" / draft.name).is_dir()


def test_a_skill_the_user_wrote_themselves_is_not_removed(instance):
    """Absent from the ledger means this plugin did not create it."""
    folder = instance / "usr" / "users" / "alice" / "skills" / "my-own-skill"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: my-own-skill\ndescription: Mine.\n---\n\nMine.\n", encoding="utf-8"
    )

    result = _forget({"name": "my-own-skill"}, "alice")

    assert not result["ok"]
    assert (folder / "SKILL.md").is_file()


@pytest.mark.parametrize(
    "name",
    ["", "   ", "../../etc/passwd", "/etc/passwd", "../alice", "does-not-exist"],
)
def test_a_name_that_is_not_an_induced_skill_is_refused(instance, name):
    _induce(instance, "alice")
    assert not _forget({"name": name}, "alice")["ok"]


def test_an_unauthenticated_call_is_refused(instance):
    _induce(instance, "alice")
    handler = AutoSkillsForget(app=None, thread_lock=threading.RLock())
    result = asyncio.run(handler.process({"name": "trace-a-slow-endpoint"}, request=None))
    assert not result["ok"]
    assert "signed-in" in result["error"]


def test_a_disabled_plugin_does_nothing(instance, monkeypatch):
    draft, _ = _induce(instance, "alice")
    monkeypatch.setattr(config, "is_enabled", lambda: False)

    assert not _forget({"name": draft.name}, "alice")["ok"]
    assert (instance / "usr" / "users" / "alice" / "skills" / draft.name).is_dir()


def test_an_unknown_action_is_refused(instance):
    draft, _ = _induce(instance, "alice")
    assert not _forget({"action": "delete", "name": draft.name}, "alice")["ok"]
    assert (instance / "usr" / "users" / "alice" / "skills" / draft.name).is_dir()


# --- the queue ---------------------------------------------------------------

def test_forgetting_withdraws_the_pending_proposal(instance):
    """The queue holds its own copy taken at capture time. Without this, a user
    could discard a skill and an administrator could still publish it."""
    draft, promotion_id = _induce(instance, "alice")
    assert promote.get(promotion_id)["status"] == promote.STATUS_PENDING

    _forget({"name": draft.name}, "alice")

    assert promote.get(promotion_id)["status"] == promote.STATUS_WITHDRAWN


def test_a_withdrawn_proposal_cannot_be_promoted(instance):
    draft, promotion_id = _induce(instance, "alice")
    _forget({"name": draft.name}, "alice")

    ok, message = promote.promote(promotion_id)

    assert not ok
    assert "withdrawn" in message
    assert not (instance / "usr" / "skills" / draft.name).exists()


def test_withdrawal_reaches_only_this_users_proposals(instance):
    draft, alice_promotion = _induce(instance, "alice")
    _, bob_promotion = _induce(instance, "bob", _draft())

    _forget({"name": draft.name}, "alice")

    assert promote.get(alice_promotion)["status"] == promote.STATUS_WITHDRAWN
    assert promote.get(bob_promotion)["status"] == promote.STATUS_PENDING


def test_an_already_promoted_proposal_is_left_alone(instance):
    """Once it is in usr/skills the skill belongs to the instance, and removing
    it is an administrator's decision."""
    draft, promotion_id = _induce(instance, "alice")
    ok, message = promote.promote(promotion_id)
    assert ok, message

    # Promotion already removed the personal copy, so there is nothing to forget.
    assert not _forget({"name": draft.name}, "alice")["ok"]
    assert promote.get(promotion_id)["status"] == promote.STATUS_PROMOTED
    assert (instance / "usr" / "skills" / draft.name / "SKILL.md").is_file()


def test_a_withdrawn_proposal_can_be_discarded(instance):
    draft, promotion_id = _induce(instance, "alice")
    _forget({"name": draft.name}, "alice")

    assert promote.discard(promotion_id) is True
    assert promote.get(promotion_id) is None
