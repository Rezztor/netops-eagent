"""Asking the utility model whether a finished task taught anything reusable.

Runs detached from the turn that triggered it, so nothing here may raise into
the caller and nothing may block. A review that fails, times out, or comes back
malformed is a skip: the cost of missing a skill is one lost opportunity, the
cost of writing a bad one is an instruction every future turn has to read.

The owner is resolved once, at the top, and passed down explicitly. Every write
below this point is to that user's private directory, and deciding whose it is
from a context variable several frames deeper is how the wrong user's directory
gets written to.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from helpers import errors, user_scope
from helpers.dirty_json import DirtyJson
from plugins._audit.helpers import audit
from plugins._ea_auto_skills.helpers import promote, store, validate

if TYPE_CHECKING:
    from agent import Agent
    from plugins._ea_auto_skills.helpers.capture import Candidate


ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_SKIP = "skip"

# How many existing skills to show the reviewer. Enough to recognise overlap,
# bounded so the prompt does not grow with the library it is meant to protect.
MAX_LISTED_SKILLS = 60


@dataclass(slots=True)
class Draft:
    action: str
    name: str
    description: str
    body: str
    rationale: str


def parse_draft(raw: Any) -> Draft | None:
    """A draft from the model's reply, or None for anything unusable."""
    if isinstance(raw, str):
        try:
            raw = DirtyJson.parse_string(raw.strip())
        except Exception:
            return None

    # Some models wrap a single object in an array.
    if isinstance(raw, list):
        raw = raw[0] if len(raw) == 1 else None
    if not isinstance(raw, dict):
        return None

    action = str(raw.get("action") or "").strip().lower()
    if action not in (ACTION_CREATE, ACTION_UPDATE, ACTION_SKIP):
        return None

    draft = Draft(
        action=action,
        name=str(raw.get("name") or "").strip().lower(),
        description=str(raw.get("description") or "").strip(),
        body=str(raw.get("body") or "").strip(),
        rationale=str(raw.get("rationale") or "").strip(),
    )
    if action == ACTION_SKIP:
        return draft
    if not draft.name or not draft.description or not draft.body:
        return None
    return draft


def _describe_existing(agent: "Agent", user_id: str) -> str:
    """The skills in scope, marking the ones this plugin may revise.

    The distinction is load-bearing. Only a skill this plugin induced for this
    user can be updated; everything else ships with the product or belongs to
    someone else, and writing to that name would put a personal copy in a root
    that resolves first, silently shadowing it. Without the marking the reviewer
    proposes updates that the gate can only throw away.
    """
    try:
        from helpers import skills as skills_helper

        found = skills_helper.list_skills(agent=agent)
    except Exception:
        return "(could not be listed)"

    try:
        induced = set(store.read_ledger(user_id))
    except Exception:
        induced = set()

    lines = []
    for skill in found[:MAX_LISTED_SKILLS]:
        if not skill.name:
            continue
        mark = " [yours]" if skill.name in induced else ""
        lines.append(f"- {skill.name}{mark}: {(skill.description or '').strip()}")
    return "\n".join(lines) if lines else "(none)"


async def _ask(
    agent: "Agent", candidate: "Candidate", settings: dict, user_id: str = ""
) -> Draft | None:
    system = agent.read_prompt("auto_skills.review.sys.md")
    message = agent.read_prompt(
        "auto_skills.review.msg.md",
        existing_skills=_describe_existing(agent, user_id),
        loaded_skills="\n".join(f"- {name}" for name in candidate.loaded_skills) or "(none)",
        evidence=candidate.evidence,
    )

    reply = await asyncio.wait_for(
        agent.call_utility_model(system=system, message=message, background=True),
        timeout=settings["review_timeout_seconds"],
    )
    return parse_draft(reply)


async def run(agent: "Agent", candidate: "Candidate", settings: dict) -> None:
    owner = user_scope.get_current_user_id()
    if not user_scope.is_safe_user_id(owner):
        return

    try:
        draft = await _ask(agent, candidate, settings, owner)
    except asyncio.TimeoutError:
        return
    except Exception as exception:
        errors.handle_error(exception)
        return

    if not draft:
        audit.emit("auto_skills.unusable_reply", context=candidate.context_id)
        return

    if draft.action == ACTION_SKIP:
        audit.emit(
            "auto_skills.skipped",
            context=candidate.context_id,
            rationale=draft.rationale[:200],
        )
        return

    try:
        result = validate.check(agent, draft, settings, user_id=owner)
        if not result.ok:
            # A refusal is rare and actionable, and without this it is
            # indistinguishable from the reviewer declining to propose
            # anything -- the two look identical from outside. Emitted to the
            # audit stream rather than the chat: it is an operator's concern,
            # not something to interrupt the user with.
            audit.emit(
                "auto_skills.refused",
                skill=draft.name,
                action=draft.action,
                reasons=result.reasons,
            )
            return

        applied = store.apply(draft, result, settings, user_id=owner)
        promote.queue(draft, candidate, settings, user_id=owner, applied=applied)

        if applied:
            # info, not util: util is the memory plugin's housekeeping -- "Preloading
            # knowledge...", "2 memories found" -- which the transcript hides by
            # default. This is the one moment the feature is visible to the person
            # it happened to, and it should not be hidden with the noise.
            agent.context.log.log(
                type="info",
                heading=f"Learned a skill: {draft.name}",
                content=draft.rationale or draft.description,
            )
    except Exception as exception:
        errors.handle_error(exception)
