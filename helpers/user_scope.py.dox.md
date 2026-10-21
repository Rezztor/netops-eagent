# user_scope.py DOX

## Purpose

- Own the `user_scope.py` helper module.
- This module resolves which user's private data (`usr/users/<user_id>/`) the current operation belongs to, across HTTP/WebSocket requests, background agent runs, and unowned framework startup/maintenance work.
- Keep this file-level DOX profile synchronized with `user_scope.py` because this directory is intentionally flat.

## Ownership

- `user_scope.py` owns the runtime implementation.
- `user_scope.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Exceptions:
- `UserScopeError` (`Exception`) — raised when an operation needs an owner and none can be resolved.
- Top-level functions:
  - `is_safe_user_id(user_id: str | None) -> bool`: matches `_SAFE_USER_ID` (`^[a-zA-Z0-9_-]{1,64}$`).
  - `user_scope(user_id: str | None) -> Iterator[None]` (context manager): push an explicit owner for the duration of a block; used for agent runs and scheduled tasks.
  - `get_scoped_user_id() -> str | None`: the explicitly pushed owner only, ignoring any request session.
  - `get_current_user_id() -> str | None`: explicit scope if set, else the signed-in request user, else `None`.
  - `require_current_user_id() -> str`: `get_current_user_id()` or raise `UserScopeError`.
  - `get_user_root(user_id: str | None = None) -> str`: absolute path to a user's private data root.
  - `get_user_path(*parts: str, user_id: str | None = None) -> str`: absolute path inside a user's root.
  - `list_user_ids() -> list[str]`: user ids that have a data directory on disk (filtered through `is_safe_user_id`).
  - `ensure_user_root(user_id: str | None = None) -> str`
  - `ensure_user_dir(*parts: str, user_id: str | None = None) -> str`: uses `os.makedirs`, not `files.make_dirs` — the latter would only create the *parent* of a directory argument.
- Constants: `USERS_ROOT`, `CHATS_DIR`, `MEMORY_DIR`, `SKILLS_DIR`, `WORKDIR_DIR`, `UPLOADS_DIR`, `DOWNLOADS_DIR` (the per-user root layout, declared here so light modules do not have to import heavier feature modules just for a path segment name).

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Owner resolution order in `get_current_user_id()`: an explicit `ContextVar` scope (set via the `user_scope()` context manager) always wins; otherwise the *live* Flask session is read (never a cached copy) so a stale `ContextVar` left on a pooled worker thread cannot leak one request's identity into the next request served by the same thread.
- `_SAFE_USER_ID` is the enforced boundary that keeps a crafted or malformed id from escaping `USERS_ROOT` when building a path — every path-returning function routes through `is_safe_user_id`.
- `get_current_user_id()` re-raises `RecursionError` rather than swallowing it: this function is read by `helpers/db.py`'s scope resolution, and reading the users table can itself open a transaction that asks for the scope again. `helpers/db.py` guards that specific cycle with a thread-local re-entrancy flag, but if a *new* recursive cycle appears here it must surface as a crash — swallowing it previously presented as a server that had simply stopped answering, with no error.
- All other exceptions (no Flask context, unreadable session) are treated as "no request-scoped owner" and return `None`, not an error.
- Observed side-effect areas: filesystem writes (directory creation via `ensure_user_root`/`ensure_user_dir`), context data (the scope `ContextVar`).
- Imported dependency areas include: `os`, `re`, `contextlib`, `contextvars`, `typing`, `helpers.files`, `flask` (deferred), `helpers.login` (deferred).

## Key Concepts

- Important called helpers/classes observed in the source: `ContextVar.set`/`.get`/`.reset`, `flask.has_request_context`, `login.current_user`, `files.get_abs_path`, `os.makedirs`.
- The explicit scope exists specifically so a background task (an agent run started from a request, or a scheduled task with no request at all) keeps operating as its owner even if it happens to run on a thread that later serves another request.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Any new code path resolving "whose data is this" should call `get_current_user_id()`/`require_current_user_id()` rather than reading the Flask session or a `ContextVar` directly.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_audit_logging.py`
  - `tests/test_browser_agent_regressions.py`
  - `tests/test_chats_search.py`
  - `tests/test_lazy_conversations.py`
  - `tests/test_parallel_tool.py`
  - `tests/test_persist_chat_log_ids.py`
  - `tests/test_personal_skills_scope.py`
  - `tests/test_projects.py`
  - `tests/test_runner_workdir.py`
  - `tests/test_scheduler_my_tasks.py`
  - `tests/test_skills_runtime.py`
  - `tests/test_snapshot_parity.py`
  - `tests/test_task_scheduler_timezone.py`
  - `tests/test_user_isolation.py`
  - `tests/test_users_rbac.py`
  - `tests/test_vision_load_image_refs.py`
  - `tests/test_ws_user_scope.py`

## Child DOX Index

No child DOX files.
