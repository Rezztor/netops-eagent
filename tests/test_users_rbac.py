"""Tests for the Rezztor EAgent account store and RBAC model."""

import json

import inspect
from pathlib import Path

import pytest

from helpers import db, files, rbac, users


@pytest.fixture
def store(database, tmp_path, monkeypatch):
    """An empty account store.

    Accounts live in PostgreSQL now, so this is the `database` fixture with the
    name the tests below already use. `files._base_dir` is still redirected
    because deleting an account also removes its data directory and its working
    directory, and that part is still the filesystem.

    Through `build_instance_root` rather than a bare tmp_path: resolving the
    working directory reads settings, and settings reads `conf/` off the same
    redirected base.
    """
    from testsupport import build_instance_root

    monkeypatch.setattr(files, "_base_dir", build_instance_root(tmp_path))
    return tmp_path


# --- RBAC model -------------------------------------------------------------

def test_admin_holds_every_permission():
    assert rbac.get_permissions(rbac.ROLE_ADMIN) == set(rbac.ALL_PERMISSIONS)


def test_standard_user_cannot_execute_code_or_administer():
    for permission in (
        rbac.PERM_TOOL_CODE_EXECUTION,
        rbac.PERM_USERS_MANAGE,
        rbac.PERM_SETTINGS_MANAGE,
        rbac.PERM_PLUGINS_MANAGE,
    ):
        assert not rbac.has_permission(rbac.ROLE_USER, permission)


def test_power_user_gets_tools_but_no_administration():
    assert rbac.has_permission(rbac.ROLE_POWER, rbac.PERM_TOOL_CODE_EXECUTION)
    assert not rbac.has_permission(rbac.ROLE_POWER, rbac.PERM_USERS_MANAGE)
    assert not rbac.has_permission(rbac.ROLE_POWER, rbac.PERM_SETTINGS_MANAGE)


def test_unknown_role_collapses_to_least_privilege():
    """An unrecognised role must never widen access."""
    assert rbac.normalize_role("superuser") == rbac.DEFAULT_ROLE
    assert rbac.normalize_role(None) == rbac.DEFAULT_ROLE
    assert not rbac.has_permission("superuser", rbac.PERM_USERS_MANAGE)


# --- Passwords --------------------------------------------------------------

def test_password_is_hashed_not_stored(store):
    users.create_user("alice", "alice-password", rbac.ROLE_USER)
    from helpers import db as _db

    with _db.unscoped_transaction() as conn:
        raw = {
            "users": [
                {"username": username, "password_hash": password_hash}
                for username, password_hash in conn.execute(
                    "SELECT username, password_hash FROM users"
                ).fetchall()
            ]
        }
    record = raw["users"][0]
    assert "alice-password" not in json.dumps(raw)
    assert record["password_hash"].startswith("$2b$")


def test_short_password_rejected(store):
    with pytest.raises(users.UserError):
        users.create_user("alice", "short", rbac.ROLE_USER)


def test_malformed_hash_reads_as_no_match():
    """A corrupted hash must not raise into a caller that treats errors as success."""
    assert users.verify_password("anything", "not-a-bcrypt-hash") is False
    assert users.verify_password("anything", "") is False


# --- Account lifecycle ------------------------------------------------------

def test_authenticate_round_trip(store):
    users.create_user("alice", "alice-password", rbac.ROLE_POWER)
    assert users.authenticate("alice", "alice-password")["role"] == rbac.ROLE_POWER
    assert users.authenticate("alice", "wrong-password") is None
    assert users.authenticate("nobody", "alice-password") is None


def test_username_is_case_insensitive(store):
    users.create_user("Alice", "alice-password", rbac.ROLE_USER)
    assert users.authenticate("ALICE", "alice-password") is not None
    with pytest.raises(users.UserError):
        users.create_user("aLiCe", "another-password", rbac.ROLE_USER)


def test_disabled_account_cannot_authenticate(store):
    users.create_user("alice", "alice-password", rbac.ROLE_USER)
    users.update_user("alice", disabled=True)
    assert users.authenticate("alice", "alice-password") is None


def test_invalid_role_rejected_on_create_and_update(store):
    with pytest.raises(users.UserError):
        users.create_user("alice", "alice-password", "superuser")
    users.create_user("alice", "alice-password", rbac.ROLE_USER)
    with pytest.raises(users.UserError):
        users.update_user("alice", role="superuser")


def test_public_record_never_exposes_hash(store):
    users.create_user("alice", "alice-password", rbac.ROLE_USER)
    assert "password_hash" not in users.list_users()[0]


# --- Lockout guards ---------------------------------------------------------

def test_last_admin_cannot_be_demoted_disabled_or_deleted(store):
    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    for kwargs in ({"role": rbac.ROLE_USER}, {"disabled": True}):
        with pytest.raises(users.UserError):
            users.update_user("root", **kwargs)
    with pytest.raises(users.UserError):
        users.delete_user("root", actor="someone-else")


def test_admin_can_be_demoted_once_another_admin_exists(store):
    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    users.create_user("second", "second-password", rbac.ROLE_ADMIN)
    assert users.update_user("root", role=rbac.ROLE_USER)["role"] == rbac.ROLE_USER


def test_disabled_admin_does_not_count_toward_the_admin_quorum(store):
    """A disabled admin cannot log in, so it must not license demoting the
    last working one."""
    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    users.create_user("second", "second-password", rbac.ROLE_ADMIN)
    users.update_user("second", disabled=True)
    with pytest.raises(users.UserError):
        users.update_user("root", role=rbac.ROLE_USER)


def test_cannot_delete_own_account(store):
    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    users.create_user("alice", "alice-password", rbac.ROLE_USER)
    with pytest.raises(users.UserError):
        users.delete_user("alice", actor="alice")


def test_deleting_an_account_removes_its_working_directory_too(store, monkeypatch):
    """Two directories, and only one of them was being removed.

    `usr/users/<id>` holds chats, memory and uploads. The agent's working
    directory is `<workdir base>/<id>`, which is outside it -- so a deleted
    account's documents and generated files stayed on disk, under a directory
    named after an account that no longer exists.
    """
    from helpers import user_scope, workdir

    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    alice = users.create_user("alice", "alice-password", rbac.ROLE_USER)

    user_root = Path(user_scope.ensure_user_root(alice["id"]))
    (user_root / "note.txt").write_text("chats and uploads", encoding="utf-8")

    work = Path(workdir.scope_to_user(workdir.get_workdir_base(), alice["id"]))
    work.mkdir(parents=True, exist_ok=True)
    (work / "report.md").write_text("# generated by the agent", encoding="utf-8")

    users.delete_user("alice", actor="root")

    assert not user_root.exists()
    assert not work.exists(), "the account's working directory outlived the account"
    # The account's own directory, never the base every account shares.
    assert work.parent.exists(), "the shared workdir base was removed with it"


# --- Bootstrap --------------------------------------------------------------

def test_seed_admin_uses_environment_credentials(store, monkeypatch):
    monkeypatch.setenv(users.ENV_ADMIN_USER, "rezztor-admin")
    monkeypatch.setenv(users.ENV_ADMIN_PASSWORD, "env-supplied-password")
    assert users.ensure_seed_admin() is None  # nothing to print back
    assert users.authenticate("rezztor-admin", "env-supplied-password")["role"] == rbac.ROLE_ADMIN


def test_seed_admin_generates_a_password_when_none_is_configured(store, monkeypatch):
    """There is no default password, and there must never be one again.

    A shared constant is the credential an attacker tries first, and it is the
    same on every instance ever deployed from this source -- publishing the code
    publishes the password. A generated one costs the operator the same glance at
    a terminal they are already watching and is not guessable from anywhere else.
    """
    monkeypatch.delenv(users.ENV_ADMIN_USER, raising=False)
    monkeypatch.delenv(users.ENV_ADMIN_PASSWORD, raising=False)

    seeded = users.ensure_seed_admin()
    assert seeded is not None, "the generated password must be returned to be shown"
    username, password = seeded
    assert username == users.DEFAULT_ADMIN_USERNAME
    assert users.authenticate(username, password) is not None
    assert not hasattr(users, "DEFAULT_ADMIN_PASSWORD"), (
        "a default password constant is back; that is the whole defect"
    )


def test_two_instances_do_not_share_a_seed_password(store, monkeypatch):
    """The property that a constant cannot have.

    Guessing one deployment's initial credentials must tell an attacker nothing
    about any other.

    A second instance used to be a second directory. It is a second *database*
    now, so the empty roster is what gets recreated here rather than the base
    path -- pointing files._base_dir somewhere new would leave both calls
    looking at the same accounts and the second would correctly do nothing.
    """
    from helpers import db as _db

    monkeypatch.delenv(users.ENV_ADMIN_PASSWORD, raising=False)
    first = users.ensure_seed_admin()

    with _db.unscoped_transaction() as conn:
        conn.execute("TRUNCATE users CASCADE")
    second = users.ensure_seed_admin()

    assert first and second and first[1] != second[1]


def test_a_generated_password_must_be_replaced_at_first_sign_in(store, monkeypatch):
    """It has been printed to a terminal, and from there into scrollback, a log
    file, and possibly a log aggregator. It is a way in, not a credential."""
    monkeypatch.delenv(users.ENV_ADMIN_PASSWORD, raising=False)
    username, _ = users.ensure_seed_admin()  # type: ignore[misc]
    assert users.get_user(username)["must_change_password"] is True


def test_a_configured_password_is_not_forced_to_change(store, monkeypatch):
    """The operator chose it, it was never printed, and in a declarative
    deployment the Secret is the source of truth. Forcing a change would put the
    running password out of step with the manifest at the first sign-in."""
    monkeypatch.setenv(users.ENV_ADMIN_PASSWORD, "chosen-by-the-operator")
    monkeypatch.delenv(users.ENV_ADMIN_MUST_CHANGE_PASSWORD, raising=False)
    assert users.ensure_seed_admin() is None, "a configured password must not be printed"
    assert users.get_user(users.DEFAULT_ADMIN_USERNAME)["must_change_password"] is False


def test_a_published_default_password_is_forced_to_change(store, monkeypatch):
    """The appliance SKU prints a default password on the console. It is a way
    in, not a credential, so first sign-in still has to replace it."""
    monkeypatch.setenv(users.ENV_ADMIN_PASSWORD, "ChangeMe-Rezztor1")
    monkeypatch.setenv(users.ENV_ADMIN_MUST_CHANGE_PASSWORD, "true")
    assert users.ensure_seed_admin() is None, "a configured password must not be printed"
    assert users.get_user(users.DEFAULT_ADMIN_USERNAME)["must_change_password"] is True


def test_the_forced_change_cannot_lock_the_only_account_out(store):
    """The reason the previous implementation gave for not forcing a change.

    It was wrong: the exempt list admits exactly the endpoints needed to sign in,
    be shown the change screen, and complete it. If any of these is removed, the
    first administrator can never finish setup.
    """
    from helpers.api import PASSWORD_CHANGE_EXEMPT_ENDPOINTS

    for endpoint in ("change_password", "whoami", "csrf_token", "logout", "bootstrap"):
        assert endpoint in PASSWORD_CHANGE_EXEMPT_ENDPOINTS, endpoint


def test_the_generated_password_passes_the_same_rule_as_every_other(store, monkeypatch):
    """There is no waiver any more.

    The seed default used to be seven characters and needed one, which meant the
    length check had a bypass. A generated password is long enough to pass
    unaided -- keep it that way rather than reintroducing the waiver.
    """
    monkeypatch.delenv(users.ENV_ADMIN_PASSWORD, raising=False)
    assert len(users.generate_password()) >= users.MIN_PASSWORD_LENGTH
    assert "allow_weak" not in inspect.signature(users.hash_password).parameters
    assert "allow_weak_password" not in inspect.signature(users.create_user).parameters

    # Short and empty passwords are refused, with no way around it.
    with pytest.raises(users.UserError):
        users.hash_password("short")
    with pytest.raises(users.UserError):
        users.hash_password("")



def test_a_new_password_needs_twelve_characters_and_an_old_one_still_signs_in(store):
    """The minimum went from eight to twelve. It applies when a password is
    set; an account whose password was set under the old rule keeps signing in
    with it until it is changed."""
    import bcrypt

    with pytest.raises(users.UserError, match="12 characters"):
        users.create_user("alice", "elevenchars", rbac.ROLE_USER)
    users.create_user("alice", "twelve-chars", rbac.ROLE_USER)
    with pytest.raises(users.UserError, match="12 characters"):
        users.update_user("alice", password="short-pass", actor="alice")

    old = bcrypt.hashpw(b"eightchr", bcrypt.gensalt(4)).decode()
    with db.unscoped_transaction() as conn:
        conn.execute("UPDATE users SET password_hash = %s WHERE username = 'alice'", (old,))
    assert users.authenticate("alice", "eightchr") is not None


def test_seed_admin_is_a_no_op_when_accounts_exist(store, monkeypatch):
    monkeypatch.delenv(users.ENV_ADMIN_PASSWORD, raising=False)
    users.create_user("alice", "alice-password", rbac.ROLE_USER)
    assert users.ensure_seed_admin() is None
    assert [u["username"] for u in users.list_users()] == ["alice"]


def test_an_unreadable_store_raises_rather_than_reseeding(store, monkeypatch):
    """A store that cannot be read must not read as "no accounts".

    That is what makes the seeding path mint a fresh administrator and print its
    password, silently replacing the real roster. The file store had this test
    because a corrupt users.json did it; the database version is a connection
    that fails, and the answer has to be the same -- refuse, do not guess.
    """
    from helpers import db as _db

    def unreachable(*_args, **_kwargs):
        raise _db.DatabaseError("connection refused")

    monkeypatch.setattr(users, "_connection", unreachable)
    with pytest.raises(_db.DatabaseError):
        users.has_any_users()
    with pytest.raises(_db.DatabaseError):
        users.ensure_seed_admin()


# --- API policy -------------------------------------------------------------

def test_sensitive_endpoints_are_gated():
    from helpers import api_policy

    assert api_policy.required_permission_for("settings_get") == rbac.PERM_SETTINGS_MANAGE
    assert api_policy.required_permission_for("settings_set") == rbac.PERM_SETTINGS_MANAGE
    assert api_policy.required_permission_for("users_create") == rbac.PERM_USERS_MANAGE
    assert api_policy.required_permission_for("plugins") == rbac.PERM_PLUGINS_MANAGE
    assert api_policy.required_permission_for("scheduler_task_create") == rbac.PERM_SCHEDULER_MANAGE


def test_deleting_a_skill_requires_managing_plugins():
    """`skills` carries action=delete beside action=list.

    A skill is an instruction the agent loads on demand, so removing one changes
    what every agent on the instance can do. The table gates by endpoint rather
    than by action, so the whole endpoint is gated and read-only listing is
    served by plugins/_skills/skills_catalog instead.
    """
    from helpers import api_policy

    assert api_policy.required_permission_for("skills") == rbac.PERM_PLUGINS_MANAGE
    assert api_policy.required_permission_for("plugins/_skills/skills_catalog") is None, (
        "listing is informational and must stay open, or the Skills screen shows "
        "nothing to anyone who is not an administrator"
    )


def test_writing_to_the_shared_knowledge_base_requires_managing_plugins():
    """usr/knowledge is shared, not per-user.

    It is RAG-indexed and quoted back by every agent, so an ungated import is not
    a denial of service -- it is a prompt-injection channel into everyone else's
    conversations. Any signed-in account could have written instructions the
    agent then treats as reference material.
    """
    from helpers import api_policy

    for endpoint in (
        "plugins/_memory/import_knowledge",
        "plugins/_memory/knowledge_reindex",
    ):
        assert api_policy.required_permission_for(endpoint) == rbac.PERM_PLUGINS_MANAGE, endpoint


def test_promoting_an_induced_skill_requires_managing_plugins():
    """Auto Skills writes a generated skill into the user's own directory, which
    only their agent reads. Promotion copies it into usr/skills, which every
    account's agent reads -- the same exposure as knowledge import, except the
    content was written by a model rather than by a person.
    """
    from helpers import api_policy

    for endpoint in (
        "plugins/_ea_auto_skills/auto_skills_promotions",
        "plugins/_ea_auto_skills/auto_skills_config",
    ):
        assert api_policy.required_permission_for(endpoint) == rbac.PERM_PLUGINS_MANAGE, endpoint


def test_removing_your_own_induced_skill_is_open_to_any_user():
    """The deliberate inversion of the test below, and the reason it is safe.

    Auto Skills applies a generated skill to the user who produced it without
    asking. Requiring an administrator to undo that would be the wrong default,
    so this endpoint sits at chat.use rather than plugins.manage. It is safe at
    that level because it takes no user id: the owner comes from the session, so
    it can only ever reach the caller's own usr/users/<id>/skills, which nobody
    else's agent reads. Its neighbours are gated harder because they can reach
    usr/skills, where a change alters what every agent on the instance does.
    """
    from helpers import api_policy

    endpoint = "plugins/_ea_auto_skills/auto_skills_forget"
    required = api_policy.required_permission_for(endpoint)

    assert required == rbac.PERM_CHAT_USE, f"{endpoint} is ungated or gated wrongly"
    assert required in rbac.get_permissions(rbac.ROLE_USER)


def test_a_standard_user_reaches_none_of_the_authoring_endpoints():
    """The role that must not. `user` holds chat.use and nothing else."""
    from helpers import api_policy

    held = rbac.get_permissions(rbac.ROLE_USER)
    for endpoint in (
        "skills",
        "skills_import",
        "skills_import_preview",
        "skills_scan",
        "plugins/_memory/import_knowledge",
        "plugins/_memory/knowledge_reindex",
        "plugins/_ea_auto_skills/auto_skills_promotions",
        "plugins/_ea_auto_skills/auto_skills_config",
    ):
        required = api_policy.required_permission_for(endpoint)
        assert required is not None, f"{endpoint} is ungated"
        assert required not in held, f"{endpoint} is reachable by a standard user"


def test_ordinary_endpoints_are_open_to_any_signed_in_user():
    from helpers import api_policy

    for endpoint in ("whoami", "poll", "chat_load", "change_password"):
        assert api_policy.required_permission_for(endpoint) is None


# --- Forced password change ---------------------------------------------

def test_admin_created_accounts_must_change_password(store):
    created = users.create_user("alice", "temp-password-1", rbac.ROLE_USER, created_by="admin")
    assert created["must_change_password"] is True
    assert users.must_change_password("alice") is True


def test_self_service_change_clears_the_flag(store):
    users.create_user("alice", "temp-password-1", rbac.ROLE_USER, created_by="admin")
    users.update_user(
        "alice", password="chosen-password-1", must_change_password=False, actor="alice"
    )
    assert users.must_change_password("alice") is False
    assert users.authenticate("alice", "chosen-password-1") is not None


def test_admin_reset_rearms_the_flag(store):
    users.create_user("alice", "temp-password-1", rbac.ROLE_USER, created_by="admin")
    users.update_user("alice", password="chosen-password-1", must_change_password=False, actor="alice")
    assert users.must_change_password("alice") is False

    # An administrator resetting it has seen the new password.
    users.update_user("alice", password="admin-reset-1", must_change_password=True, actor="root")
    assert users.must_change_password("alice") is True


def test_seeded_admin_is_exempt(store, monkeypatch):
    """Forcing a change on the only account would lock the deployment out before
    anyone could sign in to change it."""
    monkeypatch.setenv(users.ENV_ADMIN_USER, "admin")
    monkeypatch.setenv(users.ENV_ADMIN_PASSWORD, "env-supplied-password")
    users.ensure_seed_admin()
    assert users.must_change_password("admin") is False


def test_flag_survives_unrelated_updates(store):
    users.create_user("alice", "temp-password-1", rbac.ROLE_USER, created_by="admin")
    users.update_user("alice", display_name="Alice A.", actor="admin")
    assert users.must_change_password("alice") is True


def test_exempt_endpoints_let_a_pending_user_finish_the_change():
    from helpers.api import PASSWORD_CHANGE_EXEMPT_ENDPOINTS

    # Without these an account pending a change could not complete it: it needs
    # a CSRF token, its own identity, and the change endpoint itself.
    for endpoint in ("change_password", "whoami", "csrf_token", "logout"):
        assert endpoint in PASSWORD_CHANGE_EXEMPT_ENDPOINTS

    # Everything else must stay blocked, including read-only endpoints.
    for endpoint in ("settings_get", "users_list", "poll", "chat_load", "message"):
        assert endpoint not in PASSWORD_CHANGE_EXEMPT_ENDPOINTS


def test_deleting_an_account_removes_its_data(store, monkeypatch):
    """Otherwise the directory is orphaned permanently: the id is gone from the
    roster, so nothing can attribute or reclaim it, and the person's chats,
    memory and uploads outlive their account."""
    from helpers import user_scope

    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    created = users.create_user("alice", "alice-password", rbac.ROLE_USER)

    data_root = user_scope.ensure_user_dir("chats", user_id=created["id"])
    marker = f"{data_root}/evidence.txt"
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("alice's conversation")
    assert __import__("os").path.isfile(marker)

    users.delete_user("alice", actor="root")

    assert not __import__("os").path.exists(user_scope.get_user_root(created["id"]))


def test_failure_to_remove_data_does_not_resurrect_the_account(store, monkeypatch):
    """The roster write happens first and must stand on its own."""
    from helpers import user_scope

    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    users.create_user("alice", "alice-password", rbac.ROLE_USER)

    def explode(_path):
        raise OSError("permission denied")

    monkeypatch.setattr(users.shutil, "rmtree", explode)
    user_scope.ensure_user_dir("chats", user_id=users.get_user("alice")["id"])

    users.delete_user("alice", actor="root")  # must not raise

    assert [u["username"] for u in users.list_users()] == ["root"]


def test_a_reset_link_dies_with_the_password_it_was_issued_against(store):
    """A link mailed before the password changed by another route kept working
    for its half hour: only the reset flow itself revoked."""
    from helpers import password_reset

    user = users.create_user("dana", "first-password", rbac.ROLE_USER)
    token, _ = password_reset.issue(user["id"])
    users.update_user("dana", password="second-password")

    with pytest.raises(password_reset.ResetError):
        password_reset.consume(token)


def test_a_federated_sign_in_survives_an_address_a_local_account_holds(store):
    """The unique index on email refused the insert and the callback answered
    500 at every sign-in, for the person who had a local account first."""
    local = users.create_user("erin", "long-enough-pw", rbac.ROLE_USER, email="erin@x.example")

    federated = users.upsert_federated_user(
        subject="sub-9", username="erin", email="Erin@x.example",
        display_name="Erin", role="user", issuer="https://idp.example",
    )

    assert federated["id"] != local["id"]
    assert federated["email"] == ""
    assert users.get_user_by_email("erin@x.example")["id"] == local["id"]


def test_a_password_change_ends_every_other_session(store):
    """The session is a signed cookie with no server state, so a session issued
    before the change stayed good for its whole day -- including a thief's."""
    from flask import Flask

    from helpers import login

    app = Flask("test_session_stamp")
    app.secret_key = "not-a-secret"
    user = users.create_user("fay", "first-password", rbac.ROLE_USER)

    def sign_in():
        client = app.test_client()
        with app.test_request_context():
            login.login_session(user)
            from flask import session

            issued = dict(session)
        with client.session_transaction() as sess:
            sess.update(issued)
        return client

    def signed_in(client) -> bool:
        with client.session_transaction() as sess:
            carried = dict(sess)
        with app.test_request_context():
            from flask import session

            session.update(carried)
            return login.is_authenticated()

    old, other = sign_in(), sign_in()
    assert signed_in(old) and signed_in(other)

    users.update_user("fay", password="second-password")

    assert not signed_in(old) and not signed_in(other)
    assert signed_in(sign_in())


def _session_harness(user):
    from flask import Flask

    from helpers import login

    app = Flask("test_session_end")
    app.secret_key = "not-a-secret"

    def sign_in():
        with app.test_request_context():
            login.login_session(user)
            from flask import session

            return dict(session)

    def signed_in(carried) -> bool:
        with app.test_request_context():
            from flask import session

            session.update(carried)
            return login.is_authenticated()

    return sign_in, signed_in


def test_signing_out_ends_every_copy_of_the_session(store):
    """Sign-out removed the cookie from the browser that asked; a copy of it
    elsewhere stayed good for the rest of its day."""
    import asyncio
    import threading

    from flask import Flask

    from api.logout import ApiLogout

    user = users.create_user("fay", "first-password", rbac.ROLE_USER)
    sign_in, signed_in = _session_harness(user)
    browser, stolen = sign_in(), sign_in()
    assert signed_in(browser) and signed_in(stolen)

    app = Flask("test_logout")
    app.secret_key = "not-a-secret"
    with app.test_request_context():
        from flask import session

        session.update(browser)
        asyncio.run(ApiLogout(app=app, thread_lock=threading.RLock()).process({}, None))  # type: ignore[arg-type]

    assert not signed_in(stolen)
    assert signed_in(sign_in()), "signing in again works"


def test_a_session_left_idle_ends_inside_its_lifetime(store, monkeypatch):
    from helpers import login

    user = users.create_user("fay", "first-password", rbac.ROLE_USER)
    sign_in, signed_in = _session_harness(user)
    session = sign_in()
    now = session[login.SESSION_LAST_SEEN]

    monkeypatch.setattr(login.time, "time", lambda: now + login.IDLE_HOURS * 3600 - 5)
    assert signed_in(session)
    monkeypatch.setattr(login.time, "time", lambda: now + login.IDLE_HOURS * 3600 + 5)
    assert not signed_in(session)
    assert not signed_in({k: v for k, v in session.items() if k != login.SESSION_LAST_SEEN}), "no time, no session"


def test_a_used_session_stays_signed_in(store, monkeypatch):
    """Each request moves the last-seen time on, so the limit is idleness and
    not the age of the session."""
    from flask import Flask

    from helpers import login

    user = users.create_user("fay", "first-password", rbac.ROLE_USER)
    sign_in, _ = _session_harness(user)
    carried = sign_in()
    start = carried[login.SESSION_LAST_SEEN]
    app = Flask("test_session_refresh")
    app.secret_key = "not-a-secret"
    step = login.IDLE_HOURS * 3600 * 0.75
    for n in (1, 2, 3):
        monkeypatch.setattr(login.time, "time", lambda n=n: start + n * step)
        with app.test_request_context():
            from flask import session

            session.update(carried)
            assert login.is_authenticated()
            carried = dict(session)


# --- what an account leaves in the database ----------------------------------
#
# Deleting an account removed its folders. Its conversations, memories and
# per-user settings moved into PostgreSQL since that was written, and stayed:
# under row-level security nobody else can see them, so they could never be
# found again, only kept. Its scheduled tasks stayed too, and the job loop went
# on running them.


def _rows_owned_by(user_id):
    with db.unscoped_transaction() as conn:
        return {
            table: conn.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (user_id,)).fetchone()[0]
            for table, column in (
                ("conversations", "owner_id"), ("memories", "owner_id"), ("kvp", "owner_id"),
                ("scheduled_tasks", "owner_id"), ("password_resets", "user_id"),
            )
        }


def _leave_rows_as(user_id):
    from psycopg.types.json import Jsonb

    with db.transaction(user_id) as conn:
        conn.execute(
            "INSERT INTO conversations (id, owner_id, name, type, created_at, last_message, document) "
            "VALUES (%s, %s, 'private', 'user', now(), now(), %s)",
            (f"chat-{user_id[:6]}", user_id, Jsonb({})),
        )
        conn.execute(
            "INSERT INTO memories (id, scope, owner_id, area, content, embedding) "
            "VALUES (%s, 'user', %s, 'main', 'a private note', '[1,2,3]')",
            (f"mem-{user_id[:6]}", user_id),
        )
        conn.execute(
            "INSERT INTO kvp (key, scope, owner_id, value) VALUES ('pins', 'user', %s, %s)", (user_id, Jsonb([]))
        )
    with db.unscoped_transaction() as conn:
        conn.execute(
            "INSERT INTO scheduled_tasks (uuid, owner_id, type, name, state, document) "
            "VALUES (%s, %s, 'scheduled', 'nightly', 'idle', %s)",
            (f"task-{user_id[:6]}", user_id, Jsonb({})),
        )
        conn.execute(
            "INSERT INTO password_resets (digest, user_id, issued_at, expires_at) VALUES (%s, %s, 0, 4102444800)",
            (f"digest-{user_id[:6]}", user_id),
        )


def test_deleting_an_account_removes_what_it_owned_in_the_database(store):
    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    alice = users.create_user("alice", "alice-password", rbac.ROLE_POWER)
    bob = users.create_user("bob", "bob-password", rbac.ROLE_USER)
    _leave_rows_as(alice["id"])
    _leave_rows_as(bob["id"])
    assert set(_rows_owned_by(alice["id"]).values()) == {1}

    users.delete_user("alice", actor="root")

    assert _rows_owned_by(alice["id"]) == {
        "conversations": 0, "memories": 0, "kvp": 0, "scheduled_tasks": 0, "password_resets": 0,
    }
    assert set(_rows_owned_by(bob["id"]).values()) == {1}, "another account's rows went with it"


def _loaded_conversation(owner_id, ctxid):
    from agent import AgentConfig, AgentContext

    return AgentContext(config=AgentConfig(mcp_servers="", profile=""), id=ctxid, owner_id=owner_id, set_current=False)


def test_deleting_an_account_drops_its_loaded_conversations(store):
    """Left in memory, a conversation is written back the next time it moves."""
    from agent import AgentContext

    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    alice = users.create_user("alice", "alice-password", rbac.ROLE_USER)
    bob = users.create_user("bob", "bob-password", rbac.ROLE_USER)
    _loaded_conversation(alice["id"], "alices-chat")
    _loaded_conversation(bob["id"], "bobs-chat")
    try:
        users.delete_user("alice", actor="root")

        assert AgentContext.resident_for_user(alice["id"]) == []
        assert AgentContext.index_for_user(alice["id"]) == []
        assert [c.id for c in AgentContext.resident_for_user(bob["id"])] == ["bobs-chat"]
    finally:
        AgentContext.remove("alices-chat")
        AgentContext.remove("bobs-chat")


def test_disabling_an_account_stops_what_it_is_running(store):
    """It could no longer sign in, and the agent it had started ran on with its tools."""
    from types import SimpleNamespace

    from agent import AgentContext

    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    alice = users.create_user("alice", "alice-password", rbac.ROLE_POWER)
    killed = []
    context = _loaded_conversation(alice["id"], "alices-run")
    context.task = SimpleNamespace(kill=lambda: killed.append("alices-run"))
    try:
        users.update_user("alice", display_name="Alice A.", actor="root")
        assert killed == [], "an ordinary edit must not stop anything"

        users.update_user("alice", disabled=True, actor="root")

        assert killed == ["alices-run"]
        assert AgentContext.get("alices-run") is context, "disabling keeps the conversation"
    finally:
        context.task = None
        AgentContext.remove("alices-run")


# --- a password the hash cannot take ------------------------------------------


def test_a_password_too_long_to_hash_is_refused_in_words(store):
    """bcrypt takes 72 bytes and raises past that. The error was not one of ours,
    so setting such a password answered 500 instead of saying what to change."""
    with pytest.raises(users.UserError, match="72"):
        users.create_user("carol", "x" * 73, rbac.ROLE_USER)
    # Bytes, not characters: 40 of these are 80 bytes.
    with pytest.raises(users.UserError, match="72"):
        users.hash_password("é" * 40)
    assert users.verify_password("x" * 72, users.hash_password("x" * 72))


def test_a_password_that_cannot_be_set_does_not_cost_the_reset_link(store):
    """`complete` checked the minimum length before it spent the token, so that
    a typing mistake would not cost the link. A password too long for the hash
    passed that check, the token was spent, and the hash then raised: the reset
    answered 500 and the link was gone."""
    from helpers import password_reset

    user = users.create_user("dana", "first-password", rbac.ROLE_USER)
    token, _ = password_reset.issue(user["id"])

    ok, message = password_reset.complete(token, "x" * 73)
    assert (ok, "72" in message) == (False, True)

    ok, _ = password_reset.complete(token, "a-password-that-fits")
    assert ok, "the link was spent by the attempt that could not succeed"
    assert users.authenticate("dana", "a-password-that-fits")


def test_two_administrators_cannot_disable_each_other_at_once(store, monkeypatch):
    """Each request locked the row it was changing and then counted the enabled
    administrators. Two requests changing two different rows do not wait for
    each other, both count two, both pass, and none is left: the instance has
    no administrator and no way to make one from the interface. The guard's own
    comment said counting inside the transaction was what prevented this."""
    import threading

    users.create_user("root", "root-password", rbac.ROLE_ADMIN)
    users.create_user("second", "second-password", rbac.ROLE_ADMIN)

    counted = threading.Barrier(2, timeout=3)
    count_admins = users.count_admins

    def count_then_wait_for_the_other(conn=None):
        result = count_admins(conn)
        try:
            counted.wait()  # both have counted; neither has written
        except threading.BrokenBarrierError:
            pass  # the other request is waiting its turn, which is the fix
        return result

    monkeypatch.setattr(users, "count_admins", count_then_wait_for_the_other)
    outcomes = []

    def disable(target, actor):
        try:
            users.update_user(target, disabled=True, actor=actor)
            outcomes.append("disabled")
        except users.UserError:
            outcomes.append("refused")

    threads = [
        threading.Thread(target=disable, args=("second", "root")),
        threading.Thread(target=disable, args=("root", "second")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    monkeypatch.setattr(users, "count_admins", count_admins)
    assert sorted(outcomes) == ["disabled", "refused"]
    assert users.count_admins() == 1
