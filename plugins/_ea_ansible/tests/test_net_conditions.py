"""The gate's conditions, checked one at a time.

Each condition owns its own refusal, and the gate stops at the first failure --
so these tests assert both that the wrong thing is refused and that the refusal
says which thing was wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from plugins._ea_ansible.helpers import conditions


@dataclass
class Record:
    playbook_name: str = "changes/vlan_update"
    commit: str = "a" * 40
    requested_by: str = "asha"
    approved_by: str = "ravi"
    approved_hash: str = "h1"
    current_hash: str = "h1"


ACTOR = "asha"


def test_a_fully_satisfied_change_may_proceed():
    assert conditions.first_failure(Record(), ACTOR) is None


def test_the_conditions_are_ordered_and_named():
    names = [condition.name for condition in conditions.CONDITIONS]
    assert names == ["catalogue_entry", "engineer_approval", "plan_unchanged", "approval_current"]
    for condition in conditions.CONDITIONS:
        assert condition.summary.endswith(".")


def test_a_change_without_a_pinned_commit_is_refused():
    refusal = conditions.first_failure(Record(commit=""), ACTOR)
    assert refusal and "pinned commit" in refusal


def test_a_change_nobody_approved_is_refused():
    refusal = conditions.first_failure(Record(approved_by=""), ACTOR)
    assert refusal and "approved the rendered commands" in refusal


def test_the_requester_may_approve_their_own_change():
    assert conditions.first_failure(Record(approved_by="asha"), ACTOR) is None


def test_a_hash_mismatch_voids_a_self_approval():
    refusal = conditions.first_failure(Record(approved_by="asha", current_hash="h2"), ACTOR)
    assert refusal and "superseded" in refusal


def test_a_drifted_plan_is_refused_rather_than_silently_applied():
    refusal = conditions.first_failure(Record(current_hash="h2"), ACTOR)
    assert refusal and "superseded" in refusal


def test_an_unrendered_plan_is_refused():
    refusal = conditions.first_failure(Record(current_hash=""), ACTOR)
    assert refusal and "re-rendered" in refusal


def test_the_first_failure_is_the_one_reported():
    # Two conditions unmet: the earlier one is the useful one to report.
    refusal = conditions.first_failure(Record(commit="", approved_by=""), ACTOR)
    assert refusal and "pinned commit" in refusal


def test_a_check_that_raises_is_a_refusal_not_a_pass():
    """Fails closed. The surrounding codebase does the opposite on purpose."""

    class Exploding:
        playbook_name = "x"
        commit = "y"

        def __getattr__(self, name):
            raise RuntimeError("store unavailable")

    refusal = conditions.first_failure(Exploding(), ACTOR)
    assert refusal and "could not be completed" in refusal
