"""The gate between what a model wrote and what lands on disk.

Every check here exists because the alternative is an instruction file that
every future turn of that user's agent reads. The bias is refuse, not repair.
"""

import pytest

from helpers import files, skills as skills_helper
from plugins._ea_auto_skills.helpers import validate
from plugins._ea_auto_skills.helpers.review import Draft


SETTINGS = {
    "mode": "personal",
    "min_tool_calls": 2,
    "max_personal_skills": 25,
    "max_pending_promotions": 50,
    "max_skill_md_chars": 10000,
    "max_skill_bytes": 40000,
    "review_timeout_seconds": 120,
}


class _FakeAgent:
    context = None


# Captured before the fixture below stubs it out.
_REAL_SECRET_SCAN = validate._secret_scan


@pytest.fixture
def clean(tmp_path, monkeypatch):
    """No secrets registered, no existing skills, isolated filesystem."""
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    monkeypatch.setattr(validate, "_secret_scan", lambda agent, text: "")
    monkeypatch.setattr(skills_helper, "search_skills", lambda *a, **k: [])
    # The shadowing check reads the whole list, and fails closed when it cannot.
    monkeypatch.setattr(skills_helper, "list_skills", lambda *a, **k: [])
    (tmp_path / "usr" / "users" / "u1" / "skills").mkdir(parents=True)
    return tmp_path


def _draft(**overrides):
    base = dict(
        action="create",
        name="rotate-staging-keys",
        description="Rotate the staging API keys. Use when a key is expiring or leaked.",
        body="1. Read the current key.\n2. Issue a replacement.\n3. Revoke the old one.",
        rationale="The revoke step is easy to forget.",
    )
    base.update(overrides)
    return Draft(**base)


def _check(agent, draft, settings=None):
    return validate.check(agent, draft, settings or SETTINGS, user_id="u1")


# --- the happy path ---------------------------------------------------------

def test_a_well_formed_draft_passes(clean):
    result = _check(_FakeAgent(), _draft())
    assert result.ok, result.reasons
    assert result.target_dir.endswith("/usr/users/u1/skills/rotate-staging-keys")


def test_the_rendered_file_parses_back_as_the_skill_it_claims_to_be(clean):
    """Rendering and discovery must agree, or the skill is written and then
    silently skipped at load time."""
    draft = _draft()
    result = _check(_FakeAgent(), draft)

    folder = clean / "usr" / "users" / "u1" / "skills" / draft.name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(result.skill_md, encoding="utf-8")

    parsed = skills_helper.skill_from_markdown(folder / "SKILL.md")
    assert parsed is not None
    assert parsed.name == draft.name
    assert parsed.description == draft.description


def test_a_description_containing_yaml_punctuation_survives(clean):
    """A colon in a model-written sentence would otherwise produce frontmatter
    that parses as something else, or not at all."""
    draft = _draft(description='Rotate keys: staging only, never "production".')
    result = _check(_FakeAgent(), draft)
    assert result.ok, result.reasons

    folder = clean / "usr" / "users" / "u1" / "skills" / draft.name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(result.skill_md, encoding="utf-8")
    parsed = skills_helper.skill_from_markdown(folder / "SKILL.md")
    assert parsed is not None
    assert parsed.description == draft.description


# --- the refusals -----------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["Rotate-Keys", "rotate keys", "../escape", "rotate--keys", "-rotate", "rotate-", ""],
)
def test_a_name_the_skill_standard_rejects_is_refused(clean, name):
    assert not _check(_FakeAgent(), _draft(name=name)).ok


def test_a_traversing_name_cannot_reach_outside_the_users_directory(clean):
    result = _check(_FakeAgent(), _draft(name="../../../../etc/passwd"))
    assert not result.ok


def test_a_name_that_would_shadow_another_skill_is_refused(clean, monkeypatch):
    """The personal root resolves first and list_skills keeps the first match, so
    reusing a name silently replaces that skill for this user."""
    bundled = skills_helper.Skill(
        name="eagent-create-plugin",
        description="Ships with the product.",
        path=clean,
        skill_md_path=clean / "SKILL.md",
    )
    monkeypatch.setattr(skills_helper, "list_skills", lambda *a, **k: [bundled])

    result = _check(_FakeAgent(), _draft(name="eagent-create-plugin"))

    assert not result.ok
    assert any("shadow" in reason for reason in result.reasons)


def test_a_skill_this_plugin_wrote_may_be_revised_under_its_own_name(clean, monkeypatch):
    """The same name is fine when the ledger says this plugin created it."""
    from plugins._ea_auto_skills.helpers import store

    existing = skills_helper.Skill(
        name="rotate-staging-keys",
        description="Written by this plugin.",
        path=clean,
        skill_md_path=clean / "SKILL.md",
    )
    monkeypatch.setattr(skills_helper, "list_skills", lambda *a, **k: [existing])
    monkeypatch.setattr(store, "read_ledger", lambda user_id: {"rotate-staging-keys": {}})

    assert _check(_FakeAgent(), _draft(action="update")).ok


def test_being_unable_to_read_the_skill_list_is_a_refusal(clean, monkeypatch):
    """Fails closed, and says which check could not run rather than blaming the
    skill for something it does not contain."""
    def explode(*a, **k):
        raise RuntimeError("skill roots unreadable")

    monkeypatch.setattr(skills_helper, "list_skills", explode)
    result = _check(_FakeAgent(), _draft())

    assert not result.ok
    assert any("could not be read" in reason for reason in result.reasons)


def test_an_empty_body_is_refused(clean):
    assert not _check(_FakeAgent(), _draft(body="")).ok


def test_an_oversized_body_is_refused(clean):
    result = _check(_FakeAgent(), _draft(body="x" * (validate.MAX_BODY_CHARS + 1)))
    assert not result.ok
    assert any("body is" in reason for reason in result.reasons)


def test_an_oversized_file_is_refused(clean):
    settings = {**SETTINGS, "max_skill_md_chars": 100}
    result = _check(_FakeAgent(), _draft(), settings)
    assert not result.ok
    assert any("SKILL.md is" in reason for reason in result.reasons)


def test_an_overlong_description_is_refused(clean):
    assert not _check(_FakeAgent(), _draft(description="d" * 1025)).ok


def test_a_null_byte_is_refused(clean):
    result = _check(_FakeAgent(), _draft(body="step one\x00step two"))
    assert not result.ok
    assert any("null byte" in reason for reason in result.reasons)


def test_a_registered_secret_is_refused(clean, monkeypatch):
    monkeypatch.setattr(
        validate, "_secret_scan",
        lambda agent, text: "contains a value registered as a secret",
    )
    result = _check(_FakeAgent(), _draft())
    assert not result.ok
    assert any("secret" in reason for reason in result.reasons)


def test_being_unable_to_check_for_secrets_is_still_a_refusal(clean, monkeypatch):
    """Not knowing is not the same as clean."""
    def explode(context=None):
        raise RuntimeError("secrets store unavailable")

    monkeypatch.setattr("helpers.secrets.get_secrets_manager", explode)
    assert _REAL_SECRET_SCAN(_FakeAgent(), "anything")


def test_a_broken_secrets_check_does_not_claim_the_skill_had_a_credential(
    clean, monkeypatch
):
    """The distinction that cost an afternoon to find: an unrelated fault here
    used to report every generated skill as containing a secret, so the feature
    stopped writing anything and blamed the wrong thing."""
    def explode(context=None):
        raise FileNotFoundError("usr/plugins")

    monkeypatch.setattr("helpers.secrets.get_secrets_manager", explode)
    reason = _REAL_SECRET_SCAN(_FakeAgent(), "anything")

    assert "could not" in reason
    assert "registered as a secret" not in reason


def test_an_overlapping_skill_is_refused(clean, monkeypatch):
    existing = skills_helper.Skill(
        name="rotate-staging-keys",
        description="Rotate the staging keys.",
        path=clean,
        skill_md_path=clean / "SKILL.md",
    )
    existing.name = "key-rotation"
    monkeypatch.setattr(skills_helper, "search_skills", lambda *a, **k: [existing])

    result = _check(_FakeAgent(), _draft(description="Handles key rotation for staging."))
    assert not result.ok
    assert any("overlaps" in reason for reason in result.reasons)


def test_the_library_is_capped(clean):
    root = clean / "usr" / "users" / "u1" / "skills"
    for index in range(3):
        folder = root / f"existing-{index}"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: existing-{index}\ndescription: An existing skill.\n---\n\nBody.\n",
            encoding="utf-8",
        )

    result = _check(_FakeAgent(), _draft(), {**SETTINGS, "max_personal_skills": 3})
    assert not result.ok
    assert any("already holding" in reason for reason in result.reasons)


def test_revising_an_existing_skill_does_not_hit_the_cap(clean):
    root = clean / "usr" / "users" / "u1" / "skills"
    folder = root / "rotate-staging-keys"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: rotate-staging-keys\ndescription: The old version.\n---\n\nOld body.\n",
        encoding="utf-8",
    )

    result = _check(_FakeAgent(), _draft(action="update"), {**SETTINGS, "max_personal_skills": 1})
    assert result.ok, result.reasons
