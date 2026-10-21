"""The gate between what a model wrote and what lands on disk.

Everything a generated skill has to survive, in one place, reusing the checks
the product already trusts rather than reimplementing them: helpers.skills owns
the SKILL.md standard, helpers.secrets owns what a secret looks like.

The rule throughout is refuse, not repair. A skill that had to be corrected to
be safe is a skill nobody reviewed, and it would be loaded into a prompt on
every future turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from helpers import files, skills as skills_helper

if TYPE_CHECKING:
    from agent import Agent
    from plugins._ea_auto_skills.helpers.review import Draft


# Bodies longer than this are transcripts, not procedures.
MAX_BODY_CHARS = 8000



@dataclass(slots=True)
class Result:
    ok: bool = False
    reasons: list[str] = field(default_factory=list)
    skill_md: str = ""
    target_dir: str = ""

    def fail(self, reason: str) -> "Result":
        self.ok = False
        self.reasons.append(reason)
        return self


def render_skill_md(draft: "Draft") -> str:
    """The draft as a SKILL.md file.

    The description is quoted and escaped rather than written bare: a colon or a
    leading bracket in a model-written sentence would otherwise produce YAML
    that parses as something else, or fails to parse and makes the skill vanish
    silently at discovery time.
    """
    description = draft.description.replace("\\", "\\\\").replace('"', '\\"')
    description = " ".join(description.split())
    return (
        "---\n"
        f"name: {draft.name}\n"
        f'description: "{description}"\n'
        "---\n\n"
        f"{draft.body.strip()}\n"
    )


def _count_personal_skills(root: str) -> int:
    try:
        return len(skills_helper.discover_skill_md_files(Path(root)))
    except Exception:
        return 0


def _secret_scan(agent: "Agent", text: str) -> str:
    """Empty when the text carries no registered secret, otherwise the reason
    it is being refused.

    Failing to run the check is still a refusal -- unable to tell is not the
    same as clean -- but it is a different refusal, and saying so matters. An
    unrelated fault here (the secrets store unreachable, or anything it depends
    on raising) would otherwise report every generated skill as containing a
    credential, and the feature would silently stop writing anything while
    blaming the wrong thing.
    """
    try:
        from helpers.secrets import get_secrets_manager

        manager = get_secrets_manager(getattr(agent, "context", None))
    except Exception as exception:
        return f"could not reach the secrets store to check for credentials: {exception}"

    try:
        masked = manager.mask_values(text)
    except Exception as exception:
        return f"could not check for credentials: {exception}"

    return "contains a value registered as a secret" if masked != text else ""


def _shadows_another_skill(agent: "Agent", draft: "Draft", user_id: str) -> str:
    """Where an existing skill of this name lives, when it is not the user's own."""
    try:
        from plugins._ea_auto_skills.helpers import store

        if draft.name in store.read_ledger(user_id):
            return ""  # this plugin wrote it for this user; revising is allowed
    except Exception:
        return "unknown (the ledger could not be read)"

    try:
        for skill in skills_helper.list_skills(agent=agent):
            if (skill.name or "").strip().lower() == draft.name:
                return skill.name
    except Exception:
        return "unknown (the skill list could not be read)"
    return ""


def _near_duplicate(agent: "Agent", draft: "Draft") -> str:
    """Name of an existing skill this one would overlap, or empty."""
    try:
        query = f"{draft.name.replace('-', ' ')} {draft.description}"
        matches = skills_helper.search_skills(query, limit=3, agent=agent)
    except Exception:
        return ""

    for match in matches:
        existing = (match.name or "").strip().lower()
        if existing and existing != draft.name:
            # The lexical searcher ranks by shared terms, so a top hit whose own
            # name appears in the description is the same ground, not a neighbour.
            if existing.replace("-", " ") in draft.description.lower():
                return existing
    return ""


def check(
    agent: "Agent",
    draft: "Draft",
    settings: dict,
    *,
    user_id: str,
) -> Result:
    result = Result(ok=True)

    root = files.get_abs_path(
        "usr", "users", user_id, "skills"
    )
    target = str(Path(root) / draft.name)

    # 1. The SKILL.md standard, as helpers.skills defines it.
    skill = skills_helper.Skill(
        name=draft.name,
        description=draft.description,
        path=Path(target),
        skill_md_path=Path(target) / "SKILL.md",
    )
    for issue in skills_helper.validate_skill(skill):
        result.fail(issue)

    # The personal root resolves before every other, and list_skills keeps the
    # first match, so reusing a name that exists anywhere else would silently
    # replace that skill for this user. Two guessed prefixes used to stand in
    # for this and covered only some of the bundled skills.
    if shadowed := _shadows_another_skill(agent, draft, user_id):
        result.fail(f"name would shadow the existing {shadowed} skill")

    # 2. Size.
    if not draft.body:
        result.fail("body is empty")
    if len(draft.body) > MAX_BODY_CHARS:
        result.fail(f"body is {len(draft.body)} characters, over {MAX_BODY_CHARS}")

    skill_md = render_skill_md(draft)
    max_md = settings["max_skill_md_chars"]
    if len(skill_md) > max_md:
        result.fail(f"SKILL.md is {len(skill_md)} characters, over {max_md}")

    max_bytes = settings["max_skill_bytes"]
    if len(skill_md.encode("utf-8")) > max_bytes:
        result.fail(f"SKILL.md is over {max_bytes} bytes")

    # 3. Containment. validate_skill already restricts the name to
    #    [a-z0-9-], which forbids traversal outright; this is the assertion
    #    that the restriction is what actually protects the path.
    if not files.is_in_dir(target, root):
        result.fail("target path escapes the user's skills directory")

    # 4. Encoding.
    try:
        skill_md.encode("utf-8").decode("utf-8")
    except Exception:
        result.fail("SKILL.md is not valid UTF-8")
    if "\x00" in skill_md:
        result.fail("SKILL.md contains a null byte")

    # 5. Secrets. Written into a skill, a credential would be replayed into
    #    every prompt that loads it.
    if reason := _secret_scan(agent, skill_md):
        result.fail(f"SKILL.md {reason}")

    # 6. Overlap with a skill that already exists.
    if duplicate := _near_duplicate(agent, draft):
        result.fail(f"overlaps the existing skill {duplicate}")

    # 7. Library size. The catalog is rebuilt into every system prompt.
    limit = settings["max_personal_skills"]
    if Path(target).exists():
        pass  # replacing one already counted
    elif _count_personal_skills(root) >= limit:
        result.fail(f"already holding {limit} personal skills")

    result.skill_md = skill_md
    result.target_dir = target
    return result
