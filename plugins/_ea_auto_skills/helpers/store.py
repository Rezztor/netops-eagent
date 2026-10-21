"""Writing an induced skill into the user's own skills directory, and recording
where it came from.

Private to one user by construction. usr/skills is instance-wide -- everything
in it is loaded into every account's prompt -- so a skill induced from one
person's conversation is written under their own data root instead, and only
reaches everyone else through an administrator's approval.

The write is a single file replace. A skill here is one SKILL.md, so os.replace
gives real atomicity: discovery walks for SKILL.md and can never see a half
written one.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from helpers import files, user_scope
from plugins._audit.helpers import audit
from plugins._ea_auto_skills.helpers.config import MODE_PERSONAL

if TYPE_CHECKING:
    from plugins._ea_auto_skills.helpers.review import Draft
    from plugins._ea_auto_skills.helpers.validate import Result


STATE_DIR = ".auto_skills"
LEDGER_FILE = "ledger.json"
# Dot-prefixed so skill discovery, which ignores hidden paths, walks past it.
VERSIONS_DIR = ".versions"


def personal_skills_root(user_id: str) -> str:
    return user_scope.get_user_path(user_scope.SKILLS_DIR, user_id=user_id)


def ledger_path(user_id: str) -> str:
    return user_scope.get_user_path(STATE_DIR, LEDGER_FILE, user_id=user_id)


def read_ledger(user_id: str) -> dict[str, Any]:
    path = ledger_path(user_id)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def write_ledger(user_id: str, ledger: dict[str, Any]) -> None:
    path = Path(ledger_path(user_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def is_induced(user_id: str, name: str) -> bool:
    """Whether this plugin created the named skill.

    Consulted before any overwrite: a skill the user wrote themselves is not
    this plugin's to revise.
    """
    return name in read_ledger(user_id)


def _keep_previous_version(skill_dir: Path) -> None:
    current = skill_dir / "SKILL.md"
    if not current.is_file():
        return
    versions = skill_dir / VERSIONS_DIR
    versions.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (versions / f"{stamp}.md").write_text(
        current.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
    )


def apply(
    draft: "Draft",
    result: "Result",
    settings: dict,
    *,
    user_id: str,
) -> bool:
    """Write the skill for this user. Returns whether anything was written."""
    if settings["mode"] != MODE_PERSONAL:
        return False
    if not result.ok or not result.target_dir:
        return False

    skill_dir = Path(result.target_dir)
    root = personal_skills_root(user_id)
    if not files.is_in_dir(str(skill_dir), root):
        return False

    # Refuse to revise a skill this plugin did not create, even when the
    # reviewer asked to update it by name.
    if skill_dir.exists() and not is_induced(user_id, draft.name):
        return False

    skill_dir.mkdir(parents=True, exist_ok=True)
    _keep_previous_version(skill_dir)

    target = skill_dir / "SKILL.md"
    temporary = skill_dir / ".SKILL.md.tmp"
    temporary.write_text(result.skill_md, encoding="utf-8")
    os.replace(temporary, target)

    audit.emit(
        "auto_skills.applied",
        skill=draft.name,
        scope="personal",
        action=draft.action,
        bytes=len(result.skill_md.encode("utf-8")),
    )
    return True


def record(
    draft: "Draft",
    *,
    user_id: str,
    context_id: str,
    evidence_digest: str,
    promotion_id: str,
) -> None:
    """Note in the ledger where this skill came from."""
    ledger = read_ledger(user_id)
    entry = ledger.get(draft.name) or {}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    entry.setdefault("created", now)
    entry["updated"] = now
    entry["description"] = draft.description
    entry["rationale"] = draft.rationale
    entry["source_context"] = context_id
    entry["evidence_digest"] = evidence_digest
    entry["promotion_id"] = promotion_id
    entry["revisions"] = int(entry.get("revisions") or 0) + 1

    ledger[draft.name] = entry
    write_ledger(user_id, ledger)


def note_usage(user_id: str, names: list[str], *, completed: bool) -> list[str]:
    """Record that these skills were loaded in the turn that just ended.

    The receipt was already being computed on every capture and handed to the
    reviewer, then discarded. Keeping it is what any later scoring has to reason
    over: a skill loaded before ten turns that finished is evidence, and a skill
    that has never been loaded since the day it was written is evidence too.

    Only skills this plugin wrote for this user are counted. Bundled and promoted
    skills are not in the ledger, so their usage is not tracked here -- the
    reviewer still sees the live receipt for those, and a shared-skill counter
    would need a store that is not per-user.

    Returns the names actually recorded, so the caller can say what it did.
    """
    ledger = read_ledger(user_id)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    touched: list[str] = []

    for name in names:
        entry = ledger.get(name)
        if not isinstance(entry, dict):
            continue
        entry["loads"] = int(entry.get("loads") or 0) + 1
        if completed:
            entry["loads_completed"] = int(entry.get("loads_completed") or 0) + 1
        entry["last_loaded"] = now
        touched.append(name)

    if touched:
        write_ledger(user_id, ledger)
    return touched


def usage(user_id: str, name: str) -> dict[str, Any]:
    """Loads recorded for one skill. Empty when it is not this plugin's."""
    entry = read_ledger(user_id).get(name)
    if not isinstance(entry, dict):
        return {}
    return {
        "loads": int(entry.get("loads") or 0),
        "loads_completed": int(entry.get("loads_completed") or 0),
        "last_loaded": entry.get("last_loaded") or "",
    }


def forget(user_id: str, name: str) -> bool:
    """Remove an induced skill and its ledger entry. Only ever the user's own."""
    if not is_induced(user_id, name):
        return False

    root = personal_skills_root(user_id)
    skill_dir = Path(root) / name
    if not files.is_in_dir(str(skill_dir), root):
        return False

    if skill_dir.is_dir():
        import shutil

        shutil.rmtree(skill_dir, ignore_errors=True)

    ledger = read_ledger(user_id)
    ledger.pop(name, None)
    write_ledger(user_id, ledger)

    # Imported here rather than at module scope: promote imports this module.
    from plugins._ea_auto_skills.helpers import promote

    promote.withdraw(user_id, name)

    audit.emit("auto_skills.forgotten", skill=name, scope="personal")
    return True
