# users.py DOX

## Purpose

- Own the `users.py` helper module.
- This module is the account store for Rezztor EAgent: bcrypt-hashed passwords, roles, admin-managed lifecycle (create/update/delete), first-boot seeding of the initial administrator, and just-in-time provisioning of federated (OIDC) accounts.
- Keep this file-level DOX profile synchronized with `users.py` because this directory is intentionally flat.

## Ownership

- `users.py` owns the runtime implementation.
- `users.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Exceptions:
- `UserError` (`Exception`) — expected, user-correctable problems (duplicate name, weak password, unknown role).
- Top-level functions:
  - `hash_password(password: str) -> str` / `verify_password(password: str, password_hash: str) -> bool` / `generate_password(length: int = 20) -> str`.
  - `check_password(password) -> None`: raises `UserError` unless the password can be set: not empty, at least `MIN_PASSWORD_LENGTH` characters, at most `MAX_PASSWORD_BYTES` (72) bytes of UTF-8, which is what bcrypt takes. `hash_password` calls it; `helpers/password_reset.complete` calls it before it spends a link.
  - `normalize_username(username) -> str` / `normalize_email(email) -> str`.
  - `list_users() -> list[dict]`: public records, ordered by username.
  - `get_user(username) -> dict | None`, `get_user_by_id(user_id) -> dict | None`, `get_user_by_email(email) -> dict | None` (returns `None` on no match rather than raising, since password reset calls this with attacker-influenced input and must not distinguish "found" from "not found" in its own behavior).
  - `count_admins(conn=None) -> int`: enabled administrators; takes an optional open connection so a caller can count inside the same transaction it is about to write in.
  - `create_user(username, password, role, display_name="", email="", created_by="system", must_change_password=True) -> dict`
  - `update_user(username, *, role=None, password=None, display_name=None, email=None, disabled=None, must_change_password=None, actor="system") -> dict`
  - `delete_user(username, actor="system") -> None`: refuses self-deletion and last-administrator deletion; also removes both of the account's directories and hands back its runner slot.
  - `authenticate(username, password) -> dict | None`: always runs a bcrypt comparison (against `_DUMMY_HASH` for an unknown username) so response timing does not reveal which accounts exist; refuses federated accounts outright.
  - `must_change_password(username) -> bool`.
  - `has_any_users() -> bool`: raises rather than returning `False` on a store read failure, since the only caller treats "no accounts" as "seed a fresh admin," and a broken store answering "no" would replace a real roster with a freshly-generated, logged password.
  - `ensure_seed_admin() -> tuple[str, str] | None`: create the initial administrator if none exist; returns `(username, password)` only when a password was generated and must be shown once.
  - `upsert_federated_user(*, subject, username, email, display_name, role, issuer) -> dict`: create or refresh the local record for an OIDC identity, matched on `(issuer, subject)`.
  - Internal: `_row`, `_connection`, `_public`, `_check_email`, `_require_address_shape`, `_delete_user_data`, `_delete_user_rows`, `_end_account_activity`.
- Constants: `PROVIDER_LOCAL`, `PROVIDER_OIDC`, `ENV_ADMIN_USER`, `ENV_ADMIN_PASSWORD`, `ENV_ADMIN_MUST_CHANGE_PASSWORD`, `MIN_PASSWORD_LENGTH` (12), `DEFAULT_ADMIN_USERNAME`, `BCRYPT_ROUNDS` (12), `_COLUMNS`, `_SELECT`, `_DUMMY_HASH`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Concurrency safety moved from a file-store thread lock to the database: username/email uniqueness is enforced by a unique index (caught as `psycopg.errors.UniqueViolation` in `create_user` for a friendly message, after a pre-check that can still lose a race), and the last-administrator guard in `update_user`/`delete_user` takes a transaction-level advisory lock (`_one_at_a_time_through_the_admin_guard`) before it reads the row and counts, because the row lock (`FOR UPDATE`) it also holds does not make two requests changing two *different* administrators wait for each other — something a thread lock bound to one process could never do, which is how two processes could each demote the second-to-last administrator concurrently under the old file store.
- The last-administrator guard applies to both demotion (role changed away from admin) and deactivation (`disabled=True`): a deployment must not be able to lock itself out of every administrator account through the UI.
- No default admin password exists. `ensure_seed_admin()` either reads `REZZTOR_ADMIN_PASSWORD` from the environment (never printed, never forced to change) or generates one, prints it once to the terminal with a large banner, and forces a change at first sign-in (`must_change_password=True`) — because a generated password has already reached terminal scrollback and any log collector.
- `authenticate()` refuses any account whose `provider` is not `PROVIDER_LOCAL`, even if it somehow has a password hash set — federated accounts have no password of their own by design (`upsert_federated_user` writes an empty string, never a hash), and this is the second, independent check that keeps identity-provider accounts from being bypassed locally.
- `upsert_federated_user` never resets `disabled`: an administrator who has locally suspended a federated account has made a decision that a fresh successful IdP sign-in must not silently undo.
- `delete_user` removes **two** directories via `_delete_user_data`: the account's private data (`usr/users/<id>/` — chats, memory, uploads) and its agent working directory (`<workdir base>/<id>`, resolved through `helpers/workdir`). The second lives outside the first and was being left behind, so a deleted account's documents and generated files stayed on disk under a directory named for an account that no longer exists — which the editor's `account_from_path` then read as an owner. Resolving the working directory reads settings, so that half is wrapped: an unreadable setting leaves a directory behind loudly rather than failing a deletion that has already happened. `scope_to_user` returns its base unchanged for an unsafe id, so the base itself is explicitly never removed.
- `_connection()` uses `db.NO_SCOPE`, not `None` — `None` would ask `db.transaction()` to resolve the ambient scope, which calls `helpers.login.current_user()`, which comes back into this module; this function is how the system finds out who the user is, so it must not need to already know.
- `delete_user` also removes what the account owned in PostgreSQL (`_delete_user_rows`, as the owner role: `conversations`, user-scope `memories` and `kvp`, `scheduled_tasks`, and its `password_resets` rows; `change_approval` is kept as the record of what it approved), after dropping its loaded conversations and its tasks from memory (`_end_account_activity(user_id, forget=True)`), because a conversation left in memory is written back. Row-level security hides those rows from every other account, so they are deleted with the account or kept for good. `update_user(disabled=True)` calls `_end_account_activity(user_id, forget=False)`, which kills the agent runs the account has going and keeps its conversations. Either way the account's open sockets are closed (`ws_manager.disconnect_user`), so a tab it left open is pushed nothing more. Both are reported and not raised: the roster change has already happened. `tests/test_users_rbac.py` covers this.
- Observed side-effect areas: filesystem deletion (`_delete_user_data`), settings/state persistence (`users` table), secret handling (password hashes).
- Imported dependency areas include: `json`, `os`, `secrets`, `shutil`, `string`, `uuid`, `datetime`, `typing`, `bcrypt`, `helpers.db`, `helpers.files`, `helpers.rbac`, `helpers.print_style`, `helpers.mailer` (deferred), `helpers.user_scope` (deferred), `psycopg` (deferred).
- `end_sessions(user_id)` bumps the account's `session_epoch` (migration 0026), which `helpers.login` folds into every session's stamp: each session the account holds ends on its next request. `api/logout.py` calls it.
- `holds_permission(user_id, permission)` answers for an owner id rather than a request session (an agent run, a scheduled task): no id, an unknown id and a disabled account are all no.

## Key Concepts

- Important called helpers/classes observed in the source: `bcrypt.hashpw`, `bcrypt.checkpw`, `bcrypt.gensalt`, `db.transaction`, `db.NO_SCOPE`, `rbac.normalize_role`, `rbac.is_admin`, `rbac.is_valid_role`, `mailer.is_address_shaped`, `user_scope.get_user_root`, `shutil.rmtree`.
- `_public()` is the projection every read path returns — it never includes `password_hash`; callers that need to verify a password (e.g. `api/change_password.py`) read the raw row via `login.current_user()`, not via this module's public getters.
- `update_user(password=...)` deletes the account's outstanding `password_resets` rows in the same transaction: every route to a new password (self-service change, administrator reset, the reset flow) ends there, so a link mailed earlier stops working.
- `upsert_federated_user` normalises the address and records none when another account already holds it. The unique index on `lower(email)` would otherwise refuse the row and fail the sign-in; the address stays with the account that had it, because password reset finds an account by it.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Any new write path that touches `role` or `disabled` on an existing account must go through `update_user`/`delete_user` so the last-administrator guard applies; never `UPDATE users` directly.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_backup_round_trip.py`
  - `tests/test_member_mail.py`
  - `tests/test_oidc.py`
  - `tests/test_users_rbac.py`

## Child DOX Index

No child DOX files.
