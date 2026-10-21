"""The conditions execution must satisfy, as an ordered list.

The gate walks this list and stops at the first failure, returning that
condition's own refusal so the agent can say what is missing rather than retry
blindly. A list rather than a hard-coded sequence is the point: the deferred
ServiceDesk change-record check (see the plan's roadmap section) is one more
entry here, not a rewrite of the gate.

Nothing in this module reaches the network. Each `check` is handed the change
record and the actor and answers from what it is given.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Condition:
    """One thing that must be true before a change may be applied."""

    name: str
    summary: str
    #: Returns None when satisfied, or the refusal text when not.
    check: Callable[[Any, Any], str | None]


def _catalogue_entry_at_pinned_commit(record: Any, actor: Any) -> str | None:
    playbook = getattr(record, "playbook_name", "") or ""
    commit = getattr(record, "commit", "") or ""
    if not playbook or not commit:
        return (
            "This change does not name a catalogue playbook at a pinned commit. "
            "Only reviewed playbooks can run."
        )
    return None


def _engineer_approved_the_commands(record: Any, actor: Any) -> str | None:
    approver = getattr(record, "approved_by", "") or ""
    if not approver:
        return (
            "No engineer has approved the rendered commands for this change. "
            "An approver must read the exact command lines and approve them first."
        )
    return None


def _plan_still_matches_the_approved_hash(record: Any, actor: Any) -> str | None:
    approved = getattr(record, "approved_hash", "") or ""
    current = getattr(record, "current_hash", "") or ""
    if not approved:
        return "This change has no approved plan hash, so nothing can be compared."
    if not current:
        return "The plan has not been re-rendered, so it cannot be confirmed unchanged."
    if approved != current:
        return (
            "The device has changed since this plan was approved, so the commands "
            "that would run are not the commands that were reviewed. The change is "
            "superseded and needs a fresh plan and a fresh approval."
        )
    return None


def _approval_not_expired(record: Any, actor: Any) -> str | None:
    expires_at = getattr(record, "expires_at", None)
    if expires_at is not None and time.time() >= expires_at:
        return "This approval has expired. Ask for a fresh request and approve it again."
    return None


#: Order matters: cheapest and most fundamental first, so a refusal names the
#: most useful thing that is wrong rather than the last thing checked.
CONDITIONS: tuple[Condition, ...] = (
    Condition(
        "catalogue_entry",
        "The playbook is a catalogue entry at a pinned commit.",
        _catalogue_entry_at_pinned_commit,
    ),
    Condition(
        "engineer_approval",
        "An engineer has approved the rendered command lines.",
        _engineer_approved_the_commands,
    ),
    Condition(
        "plan_unchanged",
        "The plan still renders to the commands that were approved.",
        _plan_still_matches_the_approved_hash,
    ),
    Condition(
        "approval_current",
        "The approval has not expired.",
        _approval_not_expired,
    ),
)


def standing_basis(record: Any, mode: str, role: str | None, history: list[Any]) -> str:
    """`standing:<mode>` when the `read_approval` setting lets a fixed read skip its card.

    Free-form commands never qualify (the caller does not ask). Only a requester
    who could have pressed Approve (`network.approve`) qualifies: the setting moves
    when a card is shown, not who may approve (ADR 0039, 0048). `once_per_chat`
    needs a card-approved read of every one of these devices by this user in this
    chat in `history`.
    """
    from helpers import rbac

    if mode not in ("once_per_chat", "log_only") or not record.requested_by:
        return ""
    if not rbac.has_permission(role, rbac.PERM_NETWORK_APPROVE):
        return ""
    if mode == "log_only":
        return "standing:log_only"
    seen = {
        name
        for r in history
        if r.kind == "read" and r.context_id == record.context_id and r.approved_by == record.requested_by
        and r.approval_basis == ""
        for name in r.limit.split(",")
    }
    return "standing:once_per_chat" if record.context_id and set(record.limit.split(",")) <= seen else ""


def first_failure(record: Any, actor: Any) -> str | None:
    """The first unmet condition's refusal, or None when execution may proceed.

    Fails closed: an exception raised by any check is a refusal, not a pass. This
    inverts the local convention -- `plugins/_infection_check/helpers/checker.py`
    swallows its own faults so a checker outage never blocks work -- because here
    a broken check must never become an open gate.
    """
    for condition in CONDITIONS:
        try:
            refusal = condition.check(record, actor)
        except Exception as exc:
            return (
                f"The '{condition.name}' check could not be completed, so this "
                f"change cannot be applied: {exc}"
            )
        if refusal:
            return refusal
    return None
