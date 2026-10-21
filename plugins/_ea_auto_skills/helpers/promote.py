"""The queue between one user's induced skill and everyone's skill library.

usr/skills is loaded into every account's prompt, which makes an unreviewed
write there a way to put instructions in front of other people's agents. The
same reasoning already gates knowledge import in helpers/api_policy.py. So the
only path from a personal skill to a shared one runs through here, and the
promote call is reached solely from an endpoint requiring plugins.manage.

Queued proposals live under usr/plugins/_ea_auto_skills/promotions/ rather than
anywhere named skills/, because skill discovery globs usr/plugins/*/skills and
would otherwise make every pending proposal live the moment it was written.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from helpers import files
from plugins._audit.helpers import audit
from plugins._ea_auto_skills.helpers import store

if TYPE_CHECKING:
    from plugins._ea_auto_skills.helpers.capture import Candidate
    from plugins._ea_auto_skills.helpers.review import Draft


PLUGIN_STATE = ("usr", "plugins", "_ea_auto_skills")
QUEUE_DIR = "promotions"
PROMOTED_FILE = "promoted.json"

STATUS_PENDING = "pending"
STATUS_PROMOTED = "promoted"
STATUS_REJECTED = "rejected"
# Its author threw the skill away before anyone acted on the proposal.
STATUS_WITHDRAWN = "withdrawn"


def queue_root() -> str:
    return files.get_abs_path(*PLUGIN_STATE, QUEUE_DIR)


def _promoted_ledger_path() -> str:
    return files.get_abs_path(*PLUGIN_STATE, PROMOTED_FILE)


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)


def _proposal_dir(promotion_id: str) -> Path | None:
    """The directory for an id, or None if the id is not a plain hex token.

    The id reaches this from a request body, so it is validated as a name rather
    than trusted as a path component.
    """
    token = str(promotion_id or "").strip().lower()
    if not token or len(token) > 64 or not all(c in "0123456789abcdef" for c in token):
        return None
    candidate = Path(queue_root()) / token
    if not files.is_in_dir(str(candidate), queue_root()):
        return None
    return candidate


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def pending_count() -> int:
    return len([item for item in list_all() if item.get("status") == STATUS_PENDING])


def queue(
    draft: "Draft",
    candidate: "Candidate",
    settings: dict,
    *,
    user_id: str,
    applied: bool,
) -> str:
    """Record a proposal for an administrator to consider. Returns its id."""
    if pending_count() >= settings["max_pending_promotions"]:
        return ""

    promotion_id = uuid.uuid4().hex
    folder = Path(queue_root()) / promotion_id
    folder.mkdir(parents=True, exist_ok=True)

    skill_md = ""
    personal = Path(store.personal_skills_root(user_id)) / draft.name / "SKILL.md"
    if applied and personal.is_file():
        skill_md = personal.read_text(encoding="utf-8", errors="replace")
    if not skill_md:
        from plugins._ea_auto_skills.helpers.validate import render_skill_md

        skill_md = render_skill_md(draft)

    (folder / "SKILL.md").write_text(skill_md, encoding="utf-8")

    evidence_digest = audit.digest(candidate.evidence)
    _write_json(
        str(folder / "metadata.json"),
        {
            "id": promotion_id,
            "status": STATUS_PENDING,
            "name": draft.name,
            "description": draft.description,
            "rationale": draft.rationale,
            "action": draft.action,
            "owner_user_id": user_id,
            "source_context": candidate.context_id,
            "source_sequence": candidate.sequence,
            "tool_calls": candidate.tool_calls,
            "evidence_digest": evidence_digest,
            "applied_personally": applied,
            "created": _now(),
        },
    )

    store.record(
        draft,
        user_id=user_id,
        context_id=candidate.context_id,
        evidence_digest=evidence_digest,
        promotion_id=promotion_id,
    )

    audit.emit(
        "auto_skills.queued",
        skill=draft.name,
        promotion_id=promotion_id,
        applied_personally=applied,
    )
    return promotion_id


def list_all() -> list[dict[str, Any]]:
    """Every proposal, newest first."""
    root = Path(queue_root())
    if not root.is_dir():
        return []

    items: list[dict[str, Any]] = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        metadata = _read_json(str(folder / "metadata.json"))
        if metadata.get("id"):
            items.append(metadata)

    items.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
    return items


def get(promotion_id: str) -> dict[str, Any] | None:
    folder = _proposal_dir(promotion_id)
    if not folder or not folder.is_dir():
        return None

    metadata = _read_json(str(folder / "metadata.json"))
    if not metadata.get("id"):
        return None

    skill_md = ""
    skill_file = folder / "SKILL.md"
    if skill_file.is_file():
        skill_md = skill_file.read_text(encoding="utf-8", errors="replace")

    # How often its owner has actually loaded it since it was written. The first
    # thing worth knowing when deciding whether everyone should have it: a skill
    # its own author never reaches for is not one to hand to the instance.
    owner = str(metadata.get("owner_user_id") or "")
    name = str(metadata.get("name") or "")
    usage = store.usage(owner, name) if owner and name else {}

    return {**metadata, "skill_md": skill_md, "usage": usage}


def _set_status(folder: Path, status: str, **extra: Any) -> None:
    metadata = _read_json(str(folder / "metadata.json"))
    metadata["status"] = status
    metadata["resolved"] = _now()
    metadata.update(extra)
    _write_json(str(folder / "metadata.json"), metadata)


def promote(promotion_id: str) -> tuple[bool, str]:
    """Copy a proposal into the shared library. Returns (ok, message).

    Revalidated here rather than trusted from queue time: the file has been on
    disk in the meantime, and this is the step that puts it in front of everyone.
    """
    folder = _proposal_dir(promotion_id)
    if not folder or not folder.is_dir():
        return False, "Proposal not found"

    metadata = _read_json(str(folder / "metadata.json"))
    if metadata.get("status") != STATUS_PENDING:
        return False, f"Proposal is already {metadata.get('status')}"

    name = str(metadata.get("name") or "").strip()
    source = folder / "SKILL.md"
    if not name or not source.is_file():
        return False, "Proposal is incomplete"

    from helpers import skills as skills_helper

    issues = skills_helper.validate_skill_md(source)
    if issues:
        return False, "; ".join(issues)

    shared_root = files.get_abs_path("usr", "skills")
    target = Path(shared_root) / name
    if not files.is_in_dir(str(target), shared_root):
        return False, "Refusing to write outside the shared skills directory"

    promoted = _read_json(_promoted_ledger_path())
    if target.exists() and name not in promoted:
        return False, f"A skill named {name} already exists and was not created here"

    target.mkdir(parents=True, exist_ok=True)
    temporary = target / ".SKILL.md.tmp"
    temporary.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(temporary, target / "SKILL.md")

    promoted[name] = {
        "promotion_id": promotion_id,
        "owner_user_id": metadata.get("owner_user_id"),
        "promoted": _now(),
    }
    _write_json(_promoted_ledger_path(), promoted)

    # Marked promoted before the personal copy is removed, not after. forget()
    # withdraws the owner's pending proposals for that name, and while this one
    # still read as pending it withdrew the very proposal being promoted --
    # leaving an audit trail that said "withdrawn" then "promoted" for the same
    # id. The end state was right and the record was not.
    _set_status(folder, STATUS_PROMOTED)

    # One canonical copy: the personal root takes priority in skill resolution,
    # and list_skills does not dedupe when called without an agent, so leaving
    # the original behind would shadow the shared skill and list it twice.
    owner = str(metadata.get("owner_user_id") or "")
    if owner:
        store.forget(owner, name)

    audit.emit("auto_skills.promoted", skill=name, promotion_id=promotion_id, scope="shared")
    return True, f"Promoted {name}"


def reject(promotion_id: str, reason: str = "") -> tuple[bool, str]:
    """Decline a proposal. The user's own copy is left alone."""
    folder = _proposal_dir(promotion_id)
    if not folder or not folder.is_dir():
        return False, "Proposal not found"

    metadata = _read_json(str(folder / "metadata.json"))
    if metadata.get("status") != STATUS_PENDING:
        return False, f"Proposal is already {metadata.get('status')}"

    _set_status(folder, STATUS_REJECTED, reason=str(reason or "").strip()[:500])
    audit.emit(
        "auto_skills.rejected",
        skill=metadata.get("name"),
        promotion_id=promotion_id,
    )
    return True, "Rejected"


def withdraw(user_id: str, name: str) -> int:
    """Retire this user's pending proposals for a named skill. Returns how many.

    Called when the author deletes their own copy. The queue holds its own copy
    of the SKILL.md, taken at capture time, so without this a user could discard
    a skill and an administrator could still promote it to the whole instance
    afterwards -- publishing content its author had explicitly thrown away.

    Only pending proposals, and only that user's. A proposal already promoted is
    no longer theirs to withdraw: the skill belongs to the instance, and removing
    it from usr/skills is an administrator's decision.
    """
    withdrawn = 0
    for metadata in list_all():
        if metadata.get("status") != STATUS_PENDING:
            continue
        if metadata.get("owner_user_id") != user_id or metadata.get("name") != name:
            continue

        folder = _proposal_dir(str(metadata.get("id") or ""))
        if not folder or not folder.is_dir():
            continue

        _set_status(folder, STATUS_WITHDRAWN)
        audit.emit(
            "auto_skills.withdrawn",
            skill=name,
            promotion_id=metadata.get("id"),
        )
        withdrawn += 1

    return withdrawn


def discard(promotion_id: str) -> bool:
    """Remove a resolved proposal from disk."""
    folder = _proposal_dir(promotion_id)
    if not folder or not folder.is_dir():
        return False
    metadata = _read_json(str(folder / "metadata.json"))
    if metadata.get("status") == STATUS_PENDING:
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True
