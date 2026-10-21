"""User accounts for Rezztor EAgent.

Replaces upstream's single AUTH_LOGIN/AUTH_PASSWORD pair with a real account
store: bcrypt-hashed passwords, roles, and an admin-managed lifecycle.

Accounts live in usr/users.json, which is runtime state (gitignored) and must
be on the persistent volume in a container deployment.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import string
import uuid
from datetime import datetime, timezone
from typing import Any

import bcrypt

from helpers import db, files, rbac
from helpers.print_style import PrintStyle


# Where an account's authority comes from. Local accounts hold their own
# password; federated ones are owned by the identity provider and are refreshed
# from its claims on every sign-in.
PROVIDER_LOCAL = "local"
PROVIDER_OIDC = "oidc"
# The thread lock the file store needed is gone with it. Concurrency is the
# database's now: uniqueness is an index, and the last-administrator guard holds
# a row lock for the length of its transaction -- which is what the old lock
# could not do, because it only ever bound one process.

# Seed credentials are read from the environment on first boot only.
ENV_ADMIN_USER = "REZZTOR_ADMIN_USER"
ENV_ADMIN_PASSWORD = "REZZTOR_ADMIN_PASSWORD"
# When true, a configured seed password still requires a change at first
# sign-in. A deployment sets it from its own environment, never a shipped default.
ENV_ADMIN_MUST_CHANGE_PASSWORD = "REZZTOR_ADMIN_MUST_CHANGE_PASSWORD"

MIN_PASSWORD_LENGTH = 12
#: What bcrypt takes. A longer password is refused, not cut: two passwords
#: that differ after byte 72 would otherwise be the same password.
MAX_PASSWORD_BYTES = 72

# First-run credentials.
#
# There is deliberately no default password. A shared constant is the credential
# an attacker tries first, and it is the same one on every instance that has ever
# been deployed -- publishing the source publishes the password. The previous
# value existed so that first setup did not require reading a log; generating one
# and printing it costs the operator the same glance at a terminal they are
# already watching, and is not guessable from anywhere else.
DEFAULT_ADMIN_USERNAME = "admin"
BCRYPT_ROUNDS = 12


# --- Errors -----------------------------------------------------------------

class UserError(Exception):
    """Raised for expected, user-correctable problems (duplicate name, weak password)."""


# --- Hashing ----------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password, enforcing the minimum length.

    There is no waiver. There used to be one, for exactly one caller: the
    first-run default credential was seven characters and could not otherwise be
    seeded. The default is now long enough to pass the same rule as every other
    password, so the exception went with the reason for it -- a length check with
    a bypass is a weaker guarantee than one without.
    """
    check_password(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")


def check_password(password: object) -> None:
    """Raise UserError unless `password` is one that can be set.

    Apart from `hash_password` so that a caller with something to lose first --
    the reset flow spends its link -- can ask before it does. bcrypt takes 72
    bytes and raises past that with an error of its own; counted in bytes,
    because that is what it counts.
    """
    if not isinstance(password, str) or not password:
        raise UserError("Password must not be empty.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise UserError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes "
            f"({MAX_PASSWORD_BYTES} plain characters, fewer with accented ones)."
        )


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash on disk must read as "no match", never as an exception
        # that a caller might treat as success.
        return False


def generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# --- Storage ----------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: Every column, in one place, so a projection cannot drift from the table.
_COLUMNS = (
    "id", "username", "password_hash", "display_name", "email", "role",
    "disabled", "created_at", "created_by", "last_login", "must_change_password",
    "provider", "updated_at", "updated_by", "session_epoch",
)

_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM users"


def _row(row: "tuple[Any, ...] | None") -> dict[str, Any] | None:
    """A row as the dict the rest of this module and its callers expect.

    Timestamps come back as datetimes and go out as ISO strings, because that is
    what the file store produced and what the API contract has always carried.
    """
    if row is None:
        return None
    record = dict(zip(_COLUMNS, row))
    for field in ("created_at", "last_login", "updated_at"):
        value = record.get(field)
        record[field] = value.isoformat(timespec="seconds") if value else None
    return record


def _connection():
    """A transaction for account work.

    `users` carries no row-level policy -- see `helpers/schema/0002_accounts.sql`
    for why -- so the scope this sets is irrelevant here. It goes through the
    same call as everything else rather than through the owner role, because
    reaching for the owner role out of habit is how a table that *does* have a
    policy ends up being read without one.

    `NO_SCOPE`, not `None`. `None` asks `transaction()` to resolve the ambient
    scope, and resolving it calls `login.current_user()`, which comes straight
    back here -- a cycle that wedged a running instance. This function is how
    the system finds out who the user is, so it must not need to know already.
    """
    return db.transaction(db.NO_SCOPE)


def normalize_username(username: str | None) -> str:
    return (username or "").strip().lower()


# --- Public API -------------------------------------------------------------

def _public(user: dict[str, Any]) -> dict[str, Any]:
    """User record without the password hash, safe to return over the API."""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "email": user.get("email") or "",
        "role": rbac.normalize_role(user.get("role")),
        "disabled": bool(user.get("disabled")),
        "created_at": user.get("created_at"),
        "created_by": user.get("created_by"),
        "last_login": user.get("last_login"),
        "must_change_password": bool(user.get("must_change_password")),
        "provider": user.get("provider", PROVIDER_LOCAL),
    }


def list_users() -> list[dict[str, Any]]:
    with _connection() as conn:
        rows = conn.execute(f"{_SELECT} ORDER BY username").fetchall()
    return [_public(_row(row)) for row in rows]  # type: ignore[arg-type]


def normalize_email(email: str | None) -> str:
    """An account's address, lowercased and trimmed, or empty."""
    return str(email or "").strip().lower()


def _check_email(email: str, *, exclude_id: str | None = None, conn: Any = None) -> str:
    """Validate an address and refuse one already used by another account.

    Uniqueness is not cosmetic here. The platform mails accounts, never typed-in
    addresses, and password reset looks an account up *by* its address: two
    accounts sharing one would make that lookup ambiguous at exactly the moment
    it must not be.

    Validation is shape-only. Address syntax is far larger than any regex, and
    the useful check is "did somebody paste a sentence in here", not conformance.
    """
    from helpers import mailer

    address = normalize_email(email)
    if not address:
        return ""
    if not mailer.is_address_shaped(address):
        raise UserError(f"'{email}' is not a valid email address.")
    # Checked here for the message, and enforced by a unique index regardless.
    # This half can lose a race; the index cannot.
    #
    # `conn` is passed by callers that are already inside a transaction -- an
    # account editing its own record has to exclude itself, and its id is only
    # known once the row has been read and locked. Opening a second connection
    # from inside the first would be a different transaction that cannot see the
    # lock, which is the same check-then-act this is meant to avoid.
    sql = "SELECT 1 FROM users WHERE lower(email) = %s AND id <> %s"
    params = (address, exclude_id or "")
    if conn is not None:
        taken = conn.execute(sql, params).fetchone()
    else:
        with _connection() as opened:
            taken = opened.execute(sql, params).fetchone()
    if taken:
        raise UserError(f"Another account already uses {address}.")
    return address


def _require_address_shape(email: str) -> None:
    """Refuse something that is not address-shaped, without touching the store."""
    from helpers import mailer

    address = normalize_email(email)
    if address and not mailer.is_address_shaped(address):
        raise UserError(f"'{email}' is not a valid email address.")


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """The account holding this address, if any.

    Used by password reset, which is given an address by somebody who cannot
    sign in. Returns None rather than raising: a caller must not be able to tell
    a registered address from an unregistered one.
    """
    target = normalize_email(email)
    if not target:
        return None
    with _connection() as conn:
        row = conn.execute(f"{_SELECT} WHERE lower(email) = %s", (target,)).fetchone()
    return _row(row)


def get_user(username: str) -> dict[str, Any] | None:
    with _connection() as conn:
        row = conn.execute(
            f"{_SELECT} WHERE username = %s", (normalize_username(username),)
        ).fetchone()
    return _row(row)


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    with _connection() as conn:
        row = conn.execute(f"{_SELECT} WHERE id = %s", (user_id,)).fetchone()
    return _row(row)


def end_sessions(user_id: str) -> None:
    """End every session this account holds, wherever its cookie is.

    Bumps `session_epoch`, which `helpers.login` folds into each session's
    stamp; a session issued before the bump stops matching on its next request.
    Sign-out calls it, so a copied cookie does not outlive the sign-out.
    """
    with _connection() as conn:
        conn.execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE id = %s", (user_id,))


def holds_permission(user_id: str | None, permission: str) -> bool:
    """True when `user_id` is an enabled account whose role grants `permission`.

    For code that has an owner id rather than a request session: an agent run,
    a scheduled task. No id, an unknown id and a disabled account all answer no.
    """
    user = get_user_by_id(user_id) if user_id else None
    return (
        bool(user)
        and not user.get("disabled")
        and rbac.has_permission(user.get("role"), permission)
    )


#: One at a time through the last-administrator guard. A number, because that
#: is what PostgreSQL's advisory locks are named by; it means nothing else.
_ADMIN_GUARD_LOCK = 0x52455A41444D


def _one_at_a_time_through_the_admin_guard(conn: Any) -> None:
    """Hold the guard's lock until this transaction ends.

    The row lock on the account being changed does not do it: two requests
    changing two *different* administrators do not wait for each other, each
    counts two, each passes, and none is left. Taken before the row is read, so
    the second request counts after the first has committed.
    """
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (_ADMIN_GUARD_LOCK,))


def count_admins(conn: Any = None) -> int:
    """Enabled administrators.

    Takes an open connection so the caller can count inside the same
    transaction it is about to write in. Counting on a separate connection and
    then writing is the check-then-act the file store had. Counting inside the
    transaction is not enough on its own either: see
    `_one_at_a_time_through_the_admin_guard`, which every caller that may
    remove an administrator takes first.
    """
    sql = "SELECT count(*) FROM users WHERE disabled = FALSE AND role = ANY(%s)"
    admin_roles = [role for role in rbac.ROLES if rbac.is_admin(role)]
    if conn is not None:
        return int(conn.execute(sql, (admin_roles,)).fetchone()[0])
    with _connection() as opened:
        return int(opened.execute(sql, (admin_roles,)).fetchone()[0])


def create_user(
    username: str,
    password: str,
    role: str,
    display_name: str = "",
    email: str = "",
    created_by: str = "system",
    must_change_password: bool = True,
) -> dict[str, Any]:
    username = normalize_username(username)
    if not username:
        raise UserError("Username is required.")
    if not all(c.isalnum() or c in "._-" for c in username):
        raise UserError("Username may contain only letters, digits, dot, underscore and hyphen.")
    if not rbac.is_valid_role(role):
        raise UserError(f"Unknown role '{role}'. Valid roles: {', '.join(rbac.ROLES)}.")

    # Validates length before we touch the store.
    password_hash = hash_password(password)

    address = _check_email(email)
    import psycopg

    record = {
        "id": uuid.uuid4().hex,
        "username": username,
        "password_hash": password_hash,
        "display_name": display_name.strip() or username,
        "email": address,
        "role": role,
        "disabled": False,
        "created_at": _now(),
        "created_by": created_by,
        "last_login": None,
        # Accounts an administrator creates start with a password the
        # administrator has seen. The holder must replace it before the
        # account can be used for anything else.
        "must_change_password": bool(must_change_password),
        "provider": PROVIDER_LOCAL,
        "updated_at": None,
        "updated_by": "",
    }
    try:
        with _connection() as conn:
            conn.execute(
                """
                INSERT INTO users (id, username, password_hash, display_name, email,
                                   role, disabled, created_by, must_change_password,
                                   provider)
                VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s)
                """,
                (
                    record["id"], username, password_hash, record["display_name"],
                    address, role, created_by, record["must_change_password"],
                    PROVIDER_LOCAL,
                ),
            )
    except psycopg.errors.UniqueViolation as error:
        # The check above can lose a race; this cannot. Which index was violated
        # decides the message, because "already exists" pointed at the wrong
        # field is worse than no message.
        if "users_email" in str(error):
            raise UserError(f"Another account already uses {address}.") from error
        raise UserError(f"User '{username}' already exists.") from error
    return _public(record)


def update_user(
    username: str,
    *,
    role: str | None = None,
    password: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
    disabled: bool | None = None,
    must_change_password: bool | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    target = normalize_username(username)
    if email is not None:
        # Shape only, before opening anything. The uniqueness half needs the
        # account's own id to exclude it, which is not known until the row is
        # read below.
        normalize_email(email) and _require_address_shape(email)

    with _connection() as conn:
        if role is not None or disabled is True:
            _one_at_a_time_through_the_admin_guard(conn)
        # The row is locked for the length of the transaction as well. That
        # alone does not hold the administrator count still: a request changing
        # another administrator locks another row. The lock above is what does.
        row = conn.execute(
            f"{_SELECT} WHERE username = %s FOR UPDATE", (target,)
        ).fetchone()
        user = _row(row)
        if user is None:
            raise UserError(f"User '{target}' not found.")

        address = (
            None if email is None
            else _check_email(email, exclude_id=user["id"], conn=conn)
        )

        # Guard the last administrator against demotion and deactivation, or the
        # deployment locks itself out with no way back in through the UI.
        losing_admin = (role is not None and not rbac.is_admin(role)) or (disabled is True)
        if rbac.is_admin(user.get("role")) and not user.get("disabled") and losing_admin:
            if count_admins(conn) <= 1:
                raise UserError("Cannot demote or disable the last administrator.")

        changes: dict[str, Any] = {}
        if role is not None:
            if not rbac.is_valid_role(role):
                raise UserError(f"Unknown role '{role}'.")
            changes["role"] = role
        if password is not None:
            changes["password_hash"] = hash_password(password)
        if display_name is not None:
            changes["display_name"] = display_name.strip() or user["username"]
        if address is not None:
            changes["email"] = address
        if disabled is not None:
            changes["disabled"] = bool(disabled)
        if must_change_password is not None:
            changes["must_change_password"] = bool(must_change_password)
        changes["updated_at"] = datetime.now(timezone.utc)
        changes["updated_by"] = actor

        assignments = ", ".join(f"{column} = %s" for column in changes)
        conn.execute(
            f"UPDATE users SET {assignments} WHERE id = %s",
            [*changes.values(), user["id"]],
        )
        if password is not None:
            # Every route to a new password ends here, so this is where a reset
            # link mailed before it stops working. Only the reset flow itself
            # used to revoke; a link outlived a change made any other way.
            conn.execute("DELETE FROM password_resets WHERE user_id = %s", (user["id"],))
        updated = _row(
            conn.execute(f"{_SELECT} WHERE id = %s", (user["id"],)).fetchone()
        )
    if disabled is True:
        # After the commit: the account can start nothing new from here on, and
        # what it already started must not outlive the decision.
        _end_account_activity(user["id"], forget=False)
    return _public(updated)


def delete_user(username: str, actor: str = "system") -> None:
    target = normalize_username(username)
    if target == normalize_username(actor):
        raise UserError("You cannot delete your own account.")
    with _connection() as conn:
        _one_at_a_time_through_the_admin_guard(conn)
        row = conn.execute(
            f"{_SELECT} WHERE username = %s FOR UPDATE", (target,)
        ).fetchone()
        user = _row(row)
        if user is None:
            raise UserError(f"User '{target}' not found.")
        if (
            rbac.is_admin(user.get("role"))
            and not user.get("disabled")
            and count_admins(conn) <= 1
        ):
            raise UserError("Cannot delete the last administrator.")
        user_id = user["id"]
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))

    # Stop what it is running and drop its loaded conversations before the rows
    # go: a conversation still in memory is written back the next time it moves.
    _end_account_activity(user_id, forget=True)
    _delete_user_rows(user_id)

    # Remove the account's private data as well. Without this the directory is
    # orphaned permanently: the id is gone from the roster, so nothing can ever
    # attribute or reclaim it, and the person's uploads and working files stay
    # on disk after their account is deleted.
    _delete_user_data(user_id)

    # And give up their runner account, which lives on the runner rather than
    # here. Called separately rather than from inside _delete_user_data, which
    # returns early for an account that never wrote anything -- and an account
    # with no data directory still holds a slot, so folding this in there would
    # strand exactly the slots nobody notices.
    _release_runner_slot(user_id)


def _release_runner_slot(user_id: str) -> None:
    """Hand back the runner account this user held, emptying its home first.

    The pool is fixed at the size the runner image was built with, so a slot
    never given back is one the next person cannot have. The home matters more:
    it persists, and handing it over with the last occupant's files in it is the
    leak the per-user accounts exist to prevent -- release() wipes before it
    frees, and holds the slot back if it cannot.

    Failure is reported, not raised. The account is already gone from the
    roster; refusing to finish the deletion over a runner that happens to be
    down would leave an account half-deleted, which is worse than a slot an
    operator has to reclaim.
    """
    try:
        from plugins._code_execution.helpers import runner_pool

        pool = runner_pool.get_pool()
        if pool is not None:
            pool.release(user_id)
    except Exception as exc:  # noqa: BLE001
        PrintStyle.error(
            f"Deleted account {user_id} but could not release its runner slot: "
            f"{exc}. The slot stays assigned until it is released by hand."
        )


#: What an account owns in the database, beside its row in `users`. Row-level
#: security hides these from every other account, so once the account is gone
#: nothing can find them again: they are deleted with it or kept for good.
#: `change_approval` is left: it is the record of what the account approved.
#: `password_resets` is not hidden by row-level security, but an unspent link
#: digest has no use once its account is gone.
_OWNED_ROWS = (
    "DELETE FROM conversations WHERE owner_id = %s",
    "DELETE FROM memories WHERE scope = 'user' AND owner_id = %s",
    "DELETE FROM kvp WHERE scope = 'user' AND owner_id = %s",
    "DELETE FROM scheduled_tasks WHERE owner_id = %s",
    "DELETE FROM password_resets WHERE user_id = %s",
)


def _delete_user_rows(user_id: str) -> None:
    """Delete what a deleted account owned in the database.

    As the owner role, because that is the only one that can see another
    account's rows; the account itself no longer exists to be scoped to.
    Reported and not raised, like the folders below: the account is already
    gone from the roster, and that must stand.
    """
    from helpers import db

    try:
        with db.unscoped_transaction() as conn:
            for statement in _OWNED_ROWS:
                conn.execute(statement, (user_id,))
    except Exception as exc:  # noqa: BLE001
        PrintStyle.error(f"Deleted account {user_id} but could not remove its rows: {exc}")


def _end_account_activity(user_id: str, forget: bool) -> None:
    """Stop what an account is running; with `forget`, drop what it has loaded too.

    Disabling an account stopped it signing in and nothing else: an agent it
    had started ran on to the end with its tools. Deleting it left its
    conversations in memory, from where they were written back.
    """
    try:
        from agent import AgentContext

        if forget:
            for summary in AgentContext.index_for_user(user_id):
                AgentContext.remove(summary.id)
        else:
            for context in AgentContext.resident_for_user(user_id):
                if context.task:
                    context.task.kill()
        if forget:
            from helpers.task_scheduler import TaskScheduler

            TaskScheduler.get().forget_owner(user_id)
    except Exception as exc:  # noqa: BLE001
        PrintStyle.error(f"Could not stop what account {user_id} was running: {exc}")
    try:
        from helpers import ws_manager

        ws_manager.get_shared_ws_manager().disconnect_user(user_id)
    except RuntimeError:
        pass  # no socket server in this process (a script, a test)


def _delete_user_data(user_id: str) -> None:
    from helpers import user_scope, workdir

    if not user_scope.is_safe_user_id(user_id):
        return

    # Two directories, not one. `usr/users/<id>` holds chats, memory and
    # uploads; the agent's working directory is `<workdir base>/<id>`, which is
    # outside it and was left behind -- so a deleted account's files stayed on
    # disk, and the ownership stamp derived from that path named an account
    # that no longer exists.
    roots = [user_scope.get_user_root(user_id)]
    try:
        base = workdir.get_workdir_base()
        scoped = workdir.scope_to_user(base, user_id)
        # Never the base itself: `scope_to_user` returns it unchanged when the
        # id is not safe, and removing it would take every account's work.
        if scoped and os.path.abspath(scoped) != os.path.abspath(base):
            roots.append(scoped)
    except Exception as exc:
        # Resolving it reads settings. The account is already deleted by now, so
        # an unreadable setting must leave a directory behind loudly rather than
        # fail the deletion halfway through.
        PrintStyle.error(
            f"Deleted account {user_id} but could not resolve its working directory: {exc}"
        )

    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            shutil.rmtree(root)
            PrintStyle.info(f"Removed {root} for deleted account {user_id}.")
        except OSError as exc:
            # The account is already gone from the roster; report loudly rather
            # than failing the deletion, so an operator knows there is data to
            # clean up.
            PrintStyle.error(
                f"Deleted account {user_id} but could not remove {root}: {exc}"
            )


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Return the public user record on success, None otherwise.

    Always runs a bcrypt comparison, even for unknown usernames, so response
    timing does not reveal which accounts exist.
    """
    target = normalize_username(username)
    with _connection() as conn:
        user = _row(
            conn.execute(f"{_SELECT} WHERE username = %s", (target,)).fetchone()
        )
        stored_hash = user.get("password_hash") if user else None
        ok = verify_password(password, stored_hash or _DUMMY_HASH)
        if not user or not ok or user.get("disabled"):
            return None
        if user.get("provider", PROVIDER_LOCAL) != PROVIDER_LOCAL:
            # A federated account has no password of its own. Refusing here
            # rather than relying on the hash being absent means a record that
            # somehow acquires one still cannot be used to bypass the identity
            # provider -- which is the whole point of federating.
            return None
        now = datetime.now(timezone.utc)
        conn.execute("UPDATE users SET last_login = %s WHERE id = %s", (now, user["id"]))
        user["last_login"] = now.isoformat(timespec="seconds")
        return _public(user)


# Compared against when the username is unknown, to keep login timing flat.
_DUMMY_HASH = bcrypt.hashpw(b"rezztor-timing-equalizer", bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def must_change_password(username: str) -> bool:
    user = get_user(username)
    return bool(user and user.get("must_change_password"))


def has_any_users() -> bool:
    """Whether any account exists. Raises if it cannot tell.

    Deliberately not `False` on error. The only caller is the seeding path, and
    "no accounts" is what makes it mint a fresh administrator -- so an
    unreachable or broken store answering "no" would replace the real roster
    with an account whose password is printed to a log. The file store had a
    test for exactly this, because a corrupt users.json used to do it.

    Failing loudly at boot is the right outcome: an instance that cannot read
    its accounts cannot serve anyone anyway.
    """
    with _connection() as conn:
        return bool(conn.execute("SELECT 1 FROM users LIMIT 1").fetchone())


def ensure_seed_admin() -> tuple[str, str] | None:
    """Create the initial administrator if no accounts exist.

    Returns (username, password) when a password was generated and must be shown
    to the operator once; returns None if accounts already existed or the
    password came from the environment.
    """
    if has_any_users():
        return None

    username = normalize_username(os.getenv(ENV_ADMIN_USER) or DEFAULT_ADMIN_USERNAME)
    configured = os.getenv(ENV_ADMIN_PASSWORD) or ""
    password = configured or generate_password()
    generated = not configured
    force_change = os.getenv(ENV_ADMIN_MUST_CHANGE_PASSWORD, "").strip().lower() in {
        "1", "true", "yes", "on",
    }

    create_user(
        username=username,
        password=password,
        role=rbac.ROLE_ADMIN,
        display_name="Administrator",
        created_by="bootstrap",
        # A generated password has been printed to a terminal, and from there
        # into scrollback, a log file, and possibly a log aggregator. It is a
        # way in, not a credential, so it has to be replaced at first sign-in.
        #
        # A configured one is not forced: the operator chose it deliberately,
        # it was never printed, and in a declarative deployment the Secret is
        # the source of truth -- forcing a change there would put the running
        # password out of step with the manifest at the first sign-in.
        #
        # The appliance SKU is the exception: it publishes a default password
        # and sets REZZTOR_ADMIN_MUST_CHANGE_PASSWORD so first sign-in still
        # hits the change screen.
        #
        # This does not lock the account out, which an earlier comment here
        # feared. PASSWORD_CHANGE_EXEMPT_ENDPOINTS in helpers/api.py admits
        # change_password, whoami, csrf_token, bootstrap and logout, which is
        # exactly enough to sign in, be shown the change screen, and complete it.
        must_change_password=generated or force_change,
    )

    if generated:
        PrintStyle(font_color="red", bold=True).print(
            f"\n{'='*74}\n"
            f" ADMINISTRATOR ACCOUNT CREATED — this password is shown once\n"
            f"\n"
            f"   username: {username}\n"
            f"   password: {password}\n"
            f"\n"
            f" Write it down now. It is not stored anywhere in readable form and\n"
            f" will not be printed again.\n"
            f"\n"
            f" You will be asked to replace it at first sign-in, because this\n"
            f" line has already reached your terminal scrollback and any log\n"
            f" collector reading this output.\n"
            f"\n"
            f" To avoid this entirely, set {ENV_ADMIN_PASSWORD} before first start;\n"
            f" a password supplied that way is never printed and is not forced to\n"
            f" be changed.\n"
            f"{'='*74}\n"
        )
        return (username, password)

    PrintStyle(font_color="green").print(
        f"Rezztor EAgent: created initial administrator '{username}' from {ENV_ADMIN_USER}/{ENV_ADMIN_PASSWORD}."
    )
    return None


def upsert_federated_user(
    *,
    subject: str,
    username: str,
    email: str,
    display_name: str,
    role: str,
    issuer: str,
) -> dict[str, Any]:
    """Create or refresh the local record for an identity-provider account.

    Matched on `(issuer, subject)`, never on username or email. Both of those
    are mutable at every provider and reassignable at some, so keying on them is
    how one employee silently inherits another's conversations and files.

    The role is rewritten from the provider's groups on every sign-in, which is
    what makes directory membership authoritative: removing someone from
    `eagent-admins` takes effect at their next login without anyone touching
    this system.

    `disabled` is deliberately not reset. An administrator who locks a federated
    account here has made a decision that a successful sign-in should not quietly
    undo -- the provider governs identity, this governs local suspension.
    """
    normalized_role = rbac.normalize_role(role)
    federated_id = f"{issuer}|{subject}"

    now = datetime.now(timezone.utc)
    email = normalize_email(email)
    with _connection() as conn:
        # An address another account already holds is left off this one. The
        # unique index would refuse it, and the callback turned that into a 500
        # at every sign-in -- for exactly the person who had a local account
        # before the identity provider was switched on. Not merged, for the
        # reason a taken username is not: the address is how a reset finds an
        # account, and it has to keep finding the one it found before.
        if email and conn.execute(
            "SELECT 1 FROM users WHERE lower(email) = %s AND federated_id IS DISTINCT FROM %s",
            (email, federated_id),
        ).fetchone():
            PrintStyle.warning(
                "A federated sign-in carried an address another account holds; "
                "it was not recorded on the federated account."
            )
            email = ""
        row = conn.execute(
            f"{_SELECT} WHERE federated_id = %s FOR UPDATE", (federated_id,)
        ).fetchone()
        user = _row(row)

        if user is None:
            # A local account already holding this name would be shadowed, and
            # silently merging the two would hand the federated user whatever
            # the local one owned.
            desired = normalize_username(username)
            taken = {
                name
                for (name,) in conn.execute(
                    "SELECT username FROM users WHERE username LIKE %s", (f"{desired}%",)
                ).fetchall()
            }
            candidate, suffix = desired, 1
            while candidate in taken:
                suffix += 1
                candidate = f"{desired}-{suffix}"

            user_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO users (id, username, password_hash, display_name, email,
                                   role, disabled, created_by, must_change_password,
                                   provider, federated_id, last_login)
                VALUES (%s, %s, '', %s, %s, %s, FALSE, %s, FALSE, %s, %s, %s)
                """,
                (
                    user_id, candidate, display_name or candidate, email,
                    normalized_role, PROVIDER_OIDC, PROVIDER_OIDC, federated_id, now,
                ),
            )
        else:
            user_id = user["id"]
            conn.execute(
                """
                UPDATE users
                   SET display_name = %s, email = %s, role = %s, last_login = %s
                 WHERE id = %s
                """,
                (
                    display_name or user.get("display_name") or user["username"],
                    email, normalized_role, now, user_id,
                ),
            )

        # No password hash is written, ever: authenticate() refuses federated
        # accounts outright, and a hash here would be a credential nobody asked
        # for and nobody rotates. The column holds an empty string, which is the
        # absence of a credential rather than a weak one.
        refreshed = _row(
            conn.execute(f"{_SELECT} WHERE id = %s", (user_id,)).fetchone()
        )
        return _public(refreshed)
