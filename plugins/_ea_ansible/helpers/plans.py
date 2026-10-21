"""A runbook plan's pure parts: its tier, its listing, and its card (ADR 0053).

A plan is one `kind="plan"` record whose `steps` are hashed whole, so one approval binds to
every step's variables, devices and commands. Each step is a dict with `kind` ("read" or
"change"), `capability`, `variables`, `limit`, `commands`, `rollback` (the entry that undoes
it, "" for none), `rollback_commands`. Nothing here reaches a device; `tools/net_plan.py` does.
"""

from __future__ import annotations

from typing import Any

MAX_STEPS = 8
#: Lowest to highest: a plan is as risky as its riskiest step (the card's own tiers).
TIERS = ("read", "change-low", "change-high")


def tier_of(tiers: list[str]) -> str:
    return max(tiers, key=TIERS.index)


def confirm_for(tier: str, steps: list[dict[str, Any]]) -> str:
    return f"run {len(steps)} steps" if tier == "change-high" else ""


def listing(steps: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Every step's commands under a step header, and the undo of each change in reverse order.

    The second list is empty when no step changes anything; a change that names no rollback says so.
    """
    commands: list[str] = []
    for n, s in enumerate(steps, 1):
        commands += [f"# step {n}: {s['capability']} on {s['limit']}", *s["commands"]]
    undo: list[str] = []
    for n, s in reversed(list(enumerate(steps, 1))):
        if s["kind"] == "change":
            undo += [f"# undo step {n}", *(s["rollback_commands"] or ["No rollback: undo it by hand."])]
    return commands, undo


def card(record: Any, title: str, consequence: str) -> dict[str, str]:
    """The decision card for a plan or a plan's rollback, built from the record alone."""
    from plugins._ea_interaction.helpers import cards

    payload = {"change_id": record.id, "expected_hash": record.current_hash}
    undo = bool(record.rollback_of)
    return cards.decision_card(
        title,
        endpoint="plugins/_ea_ansible/change_approve",
        approve_payload={**payload, "decision": "approve"},
        reject_payload={**payload, "decision": "reject"},
        approve_label="Roll back" if undo else "Approve",
        reject_label="Keep" if undo else "Reject",
        consequence=consequence,
        details=[(f"Step {n} · {s['kind']}", f"{s['capability']} on {s['limit']}") for n, s in enumerate(record.steps, 1)],
        commands=record.commands,
        checks=record.preflight.get("checks", []),
        rollback=None if undo else record.rollback_commands or None,
        expires_at=record.expires_at,
        tier=record.tier,
        confirm=record.confirm,
    )
