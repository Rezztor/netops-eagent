"""Personal skills: one user's induced skills must not reach another's agent.

usr/skills/ is instance-wide -- every skill in it is loaded into every user's
prompt. Skills induced automatically from one person's conversation therefore
cannot go there, so they are written to a private root under the user's own
data directory instead.

The sharp edge is caching. subagents.get_paths keys on (profile, project) with
no user in the key, so a user-specific path resolved inside it would be served
to the next user sharing a profile and project. These tests pin the invariant
that the personal root is added outside that call.
"""

import pytest

from helpers import files, skills, subagents, user_scope


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    for part in ("usr/skills", "usr/plugins", "usr/users", "skills", "plugins"):
        (tmp_path / part).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_skill(root, name, description):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return folder


def _personal_root(tmp_path, user_id):
    return tmp_path / "usr" / "users" / user_id / "skills"


class _FakeContext:
    def get_data(self, _key):
        return None


class _FakeConfig:
    profile = "default"


class _FakeAgent:
    config = _FakeConfig()
    context = _FakeContext()


# --- resolution -------------------------------------------------------------

def test_no_owner_means_no_personal_root(isolated):
    """Startup and maintenance run without a user. Callers get None, not a raise."""
    assert user_scope.get_current_user_id() is None
    assert skills.get_personal_skills_root() is None


def test_personal_root_resolves_inside_a_user_scope(isolated):
    with user_scope.user_scope("alice"):
        root = skills.get_personal_skills_root()
    assert root == str(_personal_root(isolated, "alice"))


def test_unsafe_user_id_yields_no_root(isolated):
    with user_scope.user_scope("../../etc"):
        assert skills.get_personal_skills_root() is None


# --- isolation --------------------------------------------------------------

def test_each_user_lists_only_their_own_personal_skills(isolated):
    _write_skill(_personal_root(isolated, "alice"), "alice-only", "Alice's induced skill.")
    _write_skill(_personal_root(isolated, "bob"), "bob-only", "Bob's induced skill.")
    _write_skill(isolated / "usr" / "skills", "shared-one", "Approved for everyone.")

    with user_scope.user_scope("alice"):
        names = {s.name for s in skills.list_skills()}
    assert names == {"alice-only", "shared-one"}

    with user_scope.user_scope("bob"):
        names = {s.name for s in skills.list_skills()}
    assert names == {"bob-only", "shared-one"}


def test_personal_skills_are_invisible_without_an_owner(isolated):
    _write_skill(_personal_root(isolated, "alice"), "alice-only", "Alice's induced skill.")
    assert [s.name for s in skills.list_skills()] == []


def test_catalog_labels_the_personal_root(isolated):
    _write_skill(_personal_root(isolated, "alice"), "alice-only", "Alice's induced skill.")
    _write_skill(isolated / "usr" / "skills", "shared-one", "Approved for everyone.")

    with user_scope.user_scope("alice"):
        catalog = {item["name"]: item["origin"] for item in skills.list_skill_catalog()}

    assert catalog == {"alice-only": "Personal", "shared-one": "User"}


def test_personal_skill_takes_priority_over_a_global_one_of_the_same_name(isolated):
    """list_skills dedupes by name with the earlier root winning, so the user's
    own version has to come first."""
    _write_skill(_personal_root(isolated, "alice"), "shared-one", "Alice's version.")
    _write_skill(isolated / "usr" / "skills", "shared-one", "Everyone's version.")

    with user_scope.user_scope("alice"):
        roots = skills.get_skill_roots()

    assert roots[0] == str(_personal_root(isolated, "alice"))


# --- the caching invariant --------------------------------------------------

def test_personal_root_is_not_passed_through_the_paths_cache(isolated, monkeypatch):
    """subagents.get_paths must be called identically for both users; the
    personal root is appended after it returns. If it were resolved inside,
    the (profile, project) cache key would hand Alice's root to Bob."""
    calls = []

    def fake_get_paths(agent, *subpaths, **kwargs):
        calls.append((subpaths, kwargs))
        return [str(isolated / "usr" / "skills")]

    monkeypatch.setattr(subagents, "get_paths", fake_get_paths)
    agent = _FakeAgent()

    with user_scope.user_scope("alice"):
        alice_roots = skills.get_skill_roots(agent)
    with user_scope.user_scope("bob"):
        bob_roots = skills.get_skill_roots(agent)

    assert calls[0] == calls[1], "user identity must not reach the cached call"
    assert alice_roots[0] == str(_personal_root(isolated, "alice"))
    assert bob_roots[0] == str(_personal_root(isolated, "bob"))


def test_the_cached_list_is_not_mutated(isolated, monkeypatch):
    """get_paths returns its cached list object; prepending must copy."""
    cached = [str(isolated / "usr" / "skills")]
    monkeypatch.setattr(subagents, "get_paths", lambda *a, **k: cached)

    with user_scope.user_scope("alice"):
        skills.get_skill_roots(_FakeAgent())

    assert cached == [str(isolated / "usr" / "skills")]
