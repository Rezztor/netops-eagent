# login.py DOX

## Purpose

- Own the `login.py` helper module.
- This module checks login requirements and credential hashes.
- Keep this file-level DOX profile synchronized with `login.py` because this directory is intentionally flat.

## Ownership

- `login.py` owns the runtime implementation.
- `login.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `get_credentials_hash()`
- `is_login_required()`

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Observed side-effect areas: secret handling.
- Imported dependency areas include: `hashlib`, `helpers`.
- `login_form_token()` returns this session's sign-in form token (`SESSION_LOGIN_FORM_TOKEN`), creating it on first use; `login_form_token_valid(given)` compares with `security.same_secret`, so a session without a token never matches. `login_session` pops the token, so the next form carries a new one. Used by `helpers/ui_server.py` for the `POST /login` CSRF check.
- The session carries a stamp derived from the stored password hash (`SESSION_PASSWORD_STAMP`); `current_user()` refuses a session whose stamp no longer matches, so a password change or reset ends every session issued before it without server-side session state. The caller that changed its own password re-issues its session with `login_session`. A session issued before this stamp existed has none and is refused once. The stamp also folds in the account's `session_epoch` (migration 0026) when it is not 0, so `users.end_sessions` -- called by sign-out -- ends every copy of every session the account holds; at epoch 0 the stamp is unchanged, so adding the epoch signed nobody out. `tests/test_users_rbac.py` covers this.
- Idle timeout: `login_session` records `SESSION_LAST_SEEN` (epoch seconds) and `current_user()` refuses a session unused for longer than `IDLE_HOURS` (`REZZTOR_SESSION_IDLE_HOURS`, default 8), inside the 24-hour cookie lifetime. A session with no last-seen time is refused. Each request that finds the time over a minute old writes it again, so only idleness ends a session. `tests/test_users_rbac.py::test_a_session_left_idle_ends_inside_its_lifetime`.

## Key Concepts

- Important called helpers/classes observed in the source: `dotenv.get_dotenv_value`, `hashlib.sha256.hexdigest`, `hashlib.sha256`, `encode`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_http_auth_csrf.py`
  - `tests/test_oauth_gemini_api.py`
  - `tests/test_oauth_github_copilot.py`
  - `tests/test_oauth_providers.py`
  - `tests/test_oauth_xai_grok.py`
  - `tests/test_tunnel_remote_link.py`
  - `tests/test_ws_security.py`

## Child DOX Index

No child DOX files.
