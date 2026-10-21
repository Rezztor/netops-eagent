"""The conditions an Azure change must meet before it reaches the executor, as an ordered list.

Modelled on `_ea_ansible/helpers/conditions.py` (the gate walks the list and stops at the first failure, which names
what is missing), with this plugin's own set, in the build contract's order. Nothing here reaches the network. A check
that raises is a refusal, never a pass.

The reference is checked **as the pattern stands now**, not as it stood at approval, and the document's files are read
again: an approval is only as good as what is still true when the change runs.
"""

from __future__ import annotations

import time
from typing import Any

from plugins._ea_ansible.helpers.conditions import Condition
from plugins._ea_azure_change.helpers import records, sets


def _template_or_free_code_allowed(record: Any, actor: Any) -> str | None:
    if record.kind not in records.KINDS:
        return f"`{record.id}` is not an Azure change, so this tool will not run it."
    if record.playbook_name == "free":
        return None if records.rails()["allow_free_code"] is True else "Free code is switched off on this instance, so this change cannot run."
    if not record.playbook_name or not record.commit:
        return "This change does not name a reviewed template or action at a pinned commit. Only reviewed ones can run."
    return None


def _engineer_approval(record: Any, actor: Any) -> str | None:
    if not record.approved_by:
        return "No engineer has approved this change. They must read the card, take the document through the change process and approve with the CR or SR number."
    return None


def _plan_unchanged(record: Any, actor: Any) -> str | None:
    if not record.approved_hash:
        return "This change has no approved plan hash, so nothing can be compared."
    if record.approved_hash != record.current_hash:
        return ("This change was altered after it was approved, so what would run is not what was reviewed. "
                "It is superseded and needs a fresh draft, a fresh document and a fresh approval.")
    return None


def _set_steps_match(record: Any, actor: Any) -> str | None:
    if sets.is_set(record) and not sets.steps_match(record):
        return "The steps of this change set are not the ones its digest was made from, so what would run is not what was approved. Draft it again."
    return None


def _approval_current(record: Any, actor: Any) -> str | None:
    if record.expires_at is not None and time.time() >= record.expires_at:
        return "This approval has expired with its document. Ask for a fresh draft and approve it again."
    return None


def _change_reference(record: Any, actor: Any) -> str | None:
    problem = records.reference_problem(record.reference)
    return f"The CR or SR number on this change cannot be accepted: {problem}" if problem else None


def _document_issued(record: Any, actor: Any) -> str | None:
    return records.issued_document_problem(record)


def _applied_once(record: Any, actor: Any) -> str | None:
    if record.applied_at is not None:
        return "This change was already applied. A change applies once; a new one needs its own draft, document and approval."
    return None


CONDITIONS: tuple[Condition, ...] = (
    Condition("template_or_free_code_allowed", "A reviewed template or action at a pinned commit, or free code where it is switched on.", _template_or_free_code_allowed),
    Condition("engineer_approval", "An engineer approved this change.", _engineer_approval),
    Condition("plan_unchanged", "The record still hashes to what was approved.", _plan_unchanged),
    Condition("set_steps_match", "A change set's steps still hash to the set digest that was approved.", _set_steps_match),
    Condition("approval_current", "The approval has not expired.", _approval_current),
    Condition("change_reference", "A CR or SR number is on the record and matches the pattern as it is now.", _change_reference),
    Condition("document_issued", "An issued document for this plan was downloaded and its files are unchanged.", _document_issued),
    Condition("applied_once", "The change has not been applied before.", _applied_once),
)


def first_failure(record: Any, actor: Any) -> str | None:
    """The first unmet condition's refusal, or None when the change may run. Fails closed."""
    for condition in CONDITIONS:
        try:
            refusal = condition.check(record, actor)
        except Exception as error:  # noqa: BLE001 - a broken check must never be an open gate
            return f"The '{condition.name}' check could not be completed, so this change cannot run: {error}"
        if refusal:
            return refusal
    return None
