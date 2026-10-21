"""From one user's private skill to everyone's.

usr/skills is read into every account's prompt, so promotion is the step that
decides what other people's agents will follow. These tests pin that it is
deliberate, revalidated, and leaves exactly one copy behind.
"""

import json
from pathlib import Path

import pytest

from helpers import files
from plugins._ea_auto_skills.helpers import promote, store
from plugins._ea_auto_skills.helpers.capture import Candidate
from plugins._ea_auto_skills.helpers.review import Draft
from plugins._ea_auto_skills.helpers.validate import Result, render_skill_md


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
    (tmp_path / "usr" / "skills").mkdir(parents=True)
    (tmp_path / "usr" / "users" / "u1" / "skills").mkdir(parents=True)
    return tmp_path


def _draft(**overrides):
    base = dict(
        action="create",
        name="rotate-staging-keys",
        description="Rotate the staging API keys.",
        body="1. Read the key.\n2. Replace it.\n3. Revoke the old one.",
        rationale="The revoke step is easy to forget.",
    )
    base.update(overrides)
    return Draft(**base)


def _candidate():
    return Candidate(
        sequence=4,
        tool_calls=3,
        evidence="user: rotate the key\nai: done",
        loaded_skills=[],
        context_id="ctx-1",
    )


def _result(instance, draft):
    return Result(
        ok=True,
        skill_md=render_skill_md(draft),
        target_dir=str(instance / "usr" / "users" / "u1" / "skills" / draft.name),
    )


def _apply_and_queue(instance, draft=None, settings=None):
    draft = draft or _draft()
    settings = settings or SETTINGS
    applied = store.apply(draft, _result(instance, draft), settings, user_id="u1")
    promotion_id = promote.queue(
        draft, _candidate(), settings, user_id="u1", applied=applied
    )
    return draft, promotion_id


# --- the personal write -----------------------------------------------------

def test_the_skill_lands_in_the_users_own_directory(instance):
    draft, _ = _apply_and_queue(instance)
    written = instance / "usr" / "users" / "u1" / "skills" / draft.name / "SKILL.md"
    assert written.is_file()
    assert "rotate-staging-keys" in written.read_text()
    assert not (instance / "usr" / "skills" / draft.name).exists()


def test_review_mode_queues_without_writing_anything(instance):
    draft, promotion_id = _apply_and_queue(instance, settings={**SETTINGS, "mode": "review"})
    assert not (instance / "usr" / "users" / "u1" / "skills" / draft.name).exists()
    assert promote.get(promotion_id)["status"] == "pending"


def test_a_skill_this_plugin_did_not_write_is_not_overwritten(instance):
    """A skill the user wrote themselves is not this plugin's to revise."""
    draft = _draft()
    folder = instance / "usr" / "users" / "u1" / "skills" / draft.name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: rotate-staging-keys\ndescription: Mine.\n---\n\nMine.\n")

    assert store.apply(draft, _result(instance, draft), SETTINGS, user_id="u1") is False
    assert "Mine." in (folder / "SKILL.md").read_text()


def test_a_revision_keeps_the_previous_version(instance):
    draft, _ = _apply_and_queue(instance)
    revised = _draft(action="update", body="1. A better procedure.")
    store.apply(revised, _result(instance, revised), SETTINGS, user_id="u1")

    folder = instance / "usr" / "users" / "u1" / "skills" / draft.name
    assert "A better procedure." in (folder / "SKILL.md").read_text()
    versions = list((folder / store.VERSIONS_DIR).glob("*.md"))
    assert len(versions) == 1
    assert "Revoke the old one." in versions[0].read_text()


def test_kept_versions_are_hidden_from_skill_discovery(instance):
    """.versions sits inside the skill folder; discovery must walk past it."""
    from helpers import skills as skills_helper

    draft, _ = _apply_and_queue(instance)
    revised = _draft(action="update", body="1. A better procedure.")
    store.apply(revised, _result(instance, revised), SETTINGS, user_id="u1")

    found = skills_helper.discover_skill_md_files(
        instance / "usr" / "users" / "u1" / "skills"
    )
    assert len(found) == 1


# --- the queue --------------------------------------------------------------

def test_a_queued_proposal_is_not_discoverable_as_a_skill(instance):
    """Skill roots include usr/plugins/*/skills. A proposal written anywhere a
    root could reach would go live the moment it was queued, before anyone had
    looked at it."""
    from helpers import skills as skills_helper

    _, promotion_id = _apply_and_queue(instance)
    proposal = Path(promote.queue_root()) / promotion_id / "SKILL.md"
    assert proposal.is_file(), "the proposal must exist for this test to mean anything"

    discovered = set()
    for root in skills_helper.get_skill_roots():
        discovered.update(skills_helper.discover_skill_md_files(Path(root)))

    assert proposal.resolve() not in {found.resolve() for found in discovered}


def test_the_queue_is_capped(instance):
    for index in range(2):
        _apply_and_queue(instance, _draft(name=f"skill-number-{index}"))
    _, blocked = _apply_and_queue(
        instance, _draft(name="one-too-many"), {**SETTINGS, "max_pending_promotions": 2}
    )
    assert blocked == ""


def test_the_ledger_records_where_the_skill_came_from(instance):
    draft, promotion_id = _apply_and_queue(instance)
    entry = store.read_ledger("u1")[draft.name]
    assert entry["source_context"] == "ctx-1"
    assert entry["promotion_id"] == promotion_id
    assert entry["evidence_digest"].startswith("sha256:")


# --- promotion --------------------------------------------------------------

def test_promotion_moves_the_skill_and_leaves_one_copy(instance):
    draft, promotion_id = _apply_and_queue(instance)
    ok, message = promote.promote(promotion_id)

    assert ok, message
    assert (instance / "usr" / "skills" / draft.name / "SKILL.md").is_file()
    assert not (instance / "usr" / "users" / "u1" / "skills" / draft.name).exists()
    assert promote.get(promotion_id)["status"] == "promoted"
    assert draft.name not in store.read_ledger("u1")


def test_promoting_does_not_record_the_proposal_as_withdrawn(instance, capsys):
    """Promotion removes the owner's copy, and removing a copy withdraws that
    user's pending proposals for it. Ordered wrongly, promotion withdrew the
    proposal it was promoting: the end state was right, the audit trail was not.
    """
    draft, promotion_id = _apply_and_queue(instance)
    capsys.readouterr()

    ok, message = promote.promote(promotion_id)
    emitted = capsys.readouterr().out

    assert ok, message
    assert promote.get(promotion_id)["status"] == promote.STATUS_PROMOTED
    assert "auto_skills.promoted" in emitted
    assert "auto_skills.withdrawn" not in emitted


def test_a_proposal_can_only_be_resolved_once(instance):
    _, promotion_id = _apply_and_queue(instance)
    assert promote.promote(promotion_id)[0] is True
    assert promote.promote(promotion_id)[0] is False
    assert promote.reject(promotion_id)[0] is False


def test_promotion_refuses_to_overwrite_a_skill_it_did_not_create(instance):
    draft, promotion_id = _apply_and_queue(instance)
    existing = instance / "usr" / "skills" / draft.name
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("---\nname: rotate-staging-keys\ndescription: Curated.\n---\n\nCurated.\n")

    ok, message = promote.promote(promotion_id)
    assert not ok
    assert "already exists" in message
    assert "Curated." in (existing / "SKILL.md").read_text()


def test_promotion_revalidates_the_file_on_disk(instance):
    """It has been sitting on disk since it was queued, and this is the step
    that puts it in front of everyone."""
    _, promotion_id = _apply_and_queue(instance)
    proposal = Path(promote.queue_root()) / promotion_id / "SKILL.md"
    proposal.write_text("---\nname: NOT A VALID NAME\ndescription: x\n---\n\nBody.\n")

    ok, message = promote.promote(promotion_id)
    assert not ok
    assert "name" in message


def test_rejection_leaves_the_users_own_copy_alone(instance):
    draft, promotion_id = _apply_and_queue(instance)
    ok, _ = promote.reject(promotion_id, "Too specific to one project.")

    assert ok
    assert (instance / "usr" / "users" / "u1" / "skills" / draft.name / "SKILL.md").is_file()
    assert not (instance / "usr" / "skills" / draft.name).exists()
    assert promote.get(promotion_id)["reason"] == "Too specific to one project."


@pytest.mark.parametrize("bad", ["", "../../etc", "not-hex", "a" * 65, "../" * 4])
def test_an_id_that_is_not_a_plain_token_reaches_nothing(instance, bad):
    """The id arrives in a request body, so it is validated as a name rather
    than trusted as a path component."""
    assert promote.get(bad) is None
    assert promote.promote(bad)[0] is False
    assert promote.reject(bad)[0] is False
    assert promote.discard(bad) is False


def test_only_a_resolved_proposal_can_be_discarded(instance):
    _, promotion_id = _apply_and_queue(instance)
    assert promote.discard(promotion_id) is False
    promote.reject(promotion_id)
    assert promote.discard(promotion_id) is True
    assert promote.get(promotion_id) is None
