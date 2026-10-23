# db.py DOX

## Purpose

- Own the `db.py` helper module.
- This module owns the PostgreSQL connection pool, the security roles (owner, application, and the cloud executor's `eagent_cloud`), schema migrations, and instance-wide backup/restore of the database.
- Keep this file-level DOX profile synchronized with `db.py` because this directory is intentionally flat.

## Ownership

- `db.py` owns the runtime implementation.
- `db.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Exceptions:
- `DatabaseError` (`Exception`) — refused or unreachable; message is written for an operator.
- `NotConfigured` (`DatabaseError`) — no database URL/app password; fatal, there is no file fallback.
- Top-level functions:
  - `owner_url() -> str`: `REZZTOR_DB_URL`, raising `NotConfigured` if unset.
  - `app_url() -> str`: the owner URL with credentials swapped for the application role (`REZZTOR_DB_APP_PASSWORD`); the password is percent-quoted, so any character is safe in it.
  - `bootstrap() -> list[str]`: create/refresh the application role, run pending migrations, then create/refresh the cloud role; returns migration names applied.
  - `_bootstrap_cloud_role() -> None`: when `REZZTOR_DB_CLOUD_PASSWORD` is set, create or refresh `CLOUD_ROLE` (`LOGIN NOSUPERUSER NOBYPASSRLS`) and apply `_CLOUD_GRANTS`; with no password it does nothing (a deployment without the cloud executor gets no role). Runs after `migrate()` because the grants name tables migration 0021 creates.
  - `migrations() -> list[tuple[int, str, str]]`: `(number, name, path)` for every `helpers/schema/NNNN_name.sql` file, in order; raises on a duplicate number.
  - `migrate() -> list[str]`: apply unapplied migrations, one transaction per migration, recording each in `schema_migrations`.
  - `pool() -> Any`: lazily-built, process-global `ConnectionPool` (as the application role).
  - `transaction(user_id: str | None = None) -> Iterator[Any]`: a pooled connection scoped to one user via `SET LOCAL app.user_id`; resolves the ambient scope when `user_id` is `None`.
  - `unscoped_transaction() -> Iterator[Any]`: a connection as the *owner*, bypassing row-level security entirely — named deliberately awkwardly so it is not reached for by habit.
  - `data_tables() -> list[str]`: every table in `public` except `schema_migrations`.
  - `dump_data() -> dict[str, Any]`: every row of every data table as JSON-able values, for backup (timestamps and dates as ISO text, UUIDs as text, numerics as floats, bytes as `{"__bytes__": base64}`).
  - `load_data(payload: dict[str, Any]) -> dict[str, int]`: truncate and repopulate every table the payload names, inside one transaction, inserting only the columns this schema has (a backup from before a column was dropped still restores, and no name from the file reaches the statement); afterwards a table with an integer `id` backed by a sequence (`states`) has the sequence set to the highest restored id, so the next insert does not collide.
  - `reset_for_tests() -> None`: close and drop the pool, for tests and post-bootstrap invalidation.
  - `_jsonable`, `_column_types`, `_resolve_ambient_scope`, `_current_scope`: internal helpers.
- Constants: `ENV_URL`, `ENV_APP_PASSWORD`, `ENV_CLOUD_PASSWORD`, `APP_ROLE`, `CLOUD_ROLE`, `_CLOUD_GRANTS`, `SCOPE_SETTING`, `NO_SCOPE`, `SCHEMA_DIR`, `MIGRATION_PATTERN`, `UNDUMPED_TABLES`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Row-level security is structural on four tables, `conversations`, `memories`, `kvp` and `change_approval`: they are `FORCE ROW LEVEL SECURITY`, and `APP_ROLE` is `NOSUPERUSER`/`NOBYPASSRLS`, so a query that reaches them with no scope set sees nothing (the policy compares against a `NULL` setting). No other table has a policy; `scheduled_tasks`, `password_resets`, `runner_slots`, `inventory_proposal` and `inventory_source_run` carry an owner and depend on the `WHERE` of the code that reads them.
- `CLOUD_ROLE` (`eagent_cloud`, ADR 0061/0064) belongs to the cloud executor container alone; the application never connects as it. Its grants are exactly `_CLOUD_GRANTS`: `SELECT, INSERT, UPDATE, DELETE` on `states`, `USAGE` on `states_id_seq`, `SELECT, INSERT` on `cloud_state_version` (append-only), `SELECT, INSERT, UPDATE` on `cloud_price`. No default privileges name it, so a new table is invisible to it. Migration 0021 revokes `states`, its sequence and `cloud_state_version` from `APP_ROLE`; widening either role is a security change.
- `transaction()` always runs as `APP_ROLE`; `unscoped_transaction()` always runs as the owner role from `owner_url()`. Only legitimately instance-wide operations should use the unscoped path; today those are backup/restore (`dump_data`, `load_data`), deleting an account's rows (`helpers/users.py`), the runner pool, the chat index (`helpers/persist_chat.py`), the scheduler (`helpers/task_scheduler.py`) and renaming a model preset, several of them while serving a request.
- `NO_SCOPE` (`""`) and `None` are not interchangeable: `None` asks `transaction()` to resolve the ambient scope via `helpers.user_scope.get_current_user_id()`, which itself may read the `users` table — callers that are *part of* resolving identity (e.g. `helpers/users.py`) must pass `NO_SCOPE` explicitly to avoid recursion.
- `_resolving` (thread-local) guards against a real recursion case that once ran a thousand frames deep and silently hung a running instance: `_current_scope()` returns `None` re-entrantly rather than resolving again.
- `bootstrap()` is idempotent — an existing `APP_ROLE`'s password is reset to match the environment, so rotating the secret is a restart rather than a procedure. It also calls `reset_for_tests()` to discard any pool built before the role existed.
- A migration file is `NNNN_name.sql`; two files sharing a number raise `DatabaseError` rather than silently picking one.
- `dump_data`/`load_data` deliberately do not use `pg_dump`/`pg_restore` — schema comes from migrations, not from the dump, which is what lets an older backup restore onto a newer schema version. `load_data` truncates only tables present in both the payload and the live schema; a table the backup predates is left alone.
- Observed side-effect areas: filesystem reads (migration files), network calls (PostgreSQL), settings/state persistence, secret handling.
- Imported dependency areas include: `os`, `re`, `threading`, `contextlib`, `typing`, `psycopg` (deferred), `psycopg.sql` (deferred), `psycopg_pool` (deferred), `helpers.user_scope` (deferred).

## Key Concepts

- Important called helpers/classes observed in the source: `psycopg.connect`, `sql.SQL`, `sql.Identifier`, `sql.Literal`, `conn.execute`, `conn.transaction`, `ConnectionPool`, `user_scope.get_current_user_id`, `conn.execute("SELECT set_config...")`, `Jsonb`.
- `transaction()` intentionally does not open its own `conn.transaction()` block: `ConnectionPool.connection()` already wraps the block in one and calls `rollback()` when returning the connection to the pool, which psycopg forbids while an explicit `Transaction` context is open — this was found in production, not in tests, because a test throws its pool away rather than reusing pooled connections.
- `migrate()` refuses to start against a schema newer than the build ships (`_refuse_a_schema_this_build_does_not_know`). Migrations only move forward, so rolling REZZTOR_VERSION back after an upgrade would otherwise start an older application cleanly against a newer schema and fail at runtime instead of at boot -- the hazard ADR 0037 named, unmitigated since the appliance update engine that restored a dump first was retired on 2026-09-18. An unrecognised migration number is the signal; a gap below the high-water mark is just work for `migrate()` to do.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Any new code path that must see every user's rows (not just its own) should go through `unscoped_transaction()` explicitly and visibly, never by working around scoping in `transaction()`.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_backup_round_trip.py`
  - `tests/test_chats_search.py`
  - `tests/test_cloud_operations_schema.py`
  - `tests/test_database_isolation.py`
  - `tests/test_lazy_conversations.py`
  - `tests/test_member_mail.py`
  - `tests/test_memory_store.py`
  - `tests/test_model_config_project_presets.py`
  - `tests/test_persist_chat_log_ids.py`
  - `tests/test_user_isolation.py`
  - `tests/test_users_rbac.py`

## Child DOX Index

No child DOX files.
