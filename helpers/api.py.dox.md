# api.py DOX

## Purpose

- Own the `api.py` helper module.
- This module defines API handler registration, request security gates, CSRF checks, and watchdog registration.
- Keep this file-level DOX profile synchronized with `api.py` because this directory is intentionally flat.

## Ownership

- `api.py` owns the runtime implementation.
- `api.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `ApiHandler` (no explicit base class)
  - `requires_loopback(cls) -> bool`
  - `requires_api_key(cls) -> bool`
  - `requires_auth(cls) -> bool`
  - `get_methods(cls) -> list[str]`
  - `requires_csrf(cls) -> bool`
  - `async process(self, input: Input, request: Request) -> Output`
  - `async handle_request(self, request: Request) -> Response`
  - `use_context(self, ctxid: str, create_if_not_exists: bool=...)`
- Top-level functions:
- `requires_api_key(f)`
- `requires_loopback(f)`
- `requires_auth(f)`
- `csrf_protect(f)`
- `register_api_route(app: Flask, lock: ThreadLockType) -> None`
- `register_watchdogs()`
- Notable constants/configuration names: `CACHE_AREA`.

## Runtime Contracts

- A handler is found by its `.py` name through `helpers.modules.find`. Under `usr/api/` only a `.py` file is a handler.
- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- `ApiHandler` defines `process(...)`.
- `ApiHandler` defines `get_methods(...)`.
- `ApiHandler` defines `requires_auth(...)`.
- `ApiHandler` defines `requires_csrf(...)`.
- `ApiHandler` defines `requires_api_key(...)`.
- `ApiHandler` defines `requires_loopback(...)`.
- Observed side-effect areas: filesystem reads, filesystem writes, filesystem deletion, WebSocket state, plugin state, settings/state persistence, secret handling.
- Imported dependency areas include: `abc`, `flask`, `functools`, `helpers`, `helpers.errors`, `helpers.network`, `helpers.print_style`, `json`, `pathlib`, `threading`, `typing`, `werkzeug.wrappers.response`.
- `csrf_protect` accepts the token in the `X-CSRF-Token` header only, compared in constant time with the session's. The `csrf_token_<runtime>` cookie alone does not pass; it remains for the WebSocket handshake, which checks it alongside the client's token. Exemptions stay explicit: a handler that overrides `requires_csrf()` (API-key, loopback, bootstrap, health, readiness, password reset). `tests/test_http_auth_csrf.py` covers this.

## Key Concepts

- Important called helpers/classes observed in the source: `wraps`, `app.add_url_rule`, `watchdog.add_watchdog`, `cls.requires_auth`, `_use_context`, `login.get_credentials_hash`, `files.get_abs_path`, `handler_cls.requires_csrf`, `handler_cls.requires_api_key`, `handler_cls.requires_auth`, `handler_cls.requires_loopback`, `cache.add`, `PrintStyle.debug`, `cache.clear`, `get_settings`, `f`, `is_loopback_address`, `Response`, `redirect`, `files.is_in_dir`.
- HTTP handlers retain built-in `api/` and explicit plugin API precedence, then fall back to standalone `usr/api/`; built-in and user roots are containment-checked, and every loaded handler keeps its declared authentication, CSRF, API-key, loopback, and method gates.
- `handle_request`'s exception handling maps two ownership-violation exceptions to an identical-shaped 404 rather than a 500: `helpers.context_utils.ContextAccessDenied` ("Context not found.") and `helpers.task_scheduler.TaskAccessDenied` ("Task not found."), both imported locally inside the `except` block to avoid a module-level import cycle. Any handler that reaches a task or context by id and needs the same "exists but not yours reads as not found" behavior should raise one of these rather than inventing a new response shape.
- The same block maps `helpers.license_entitlements.LicenceRestrictedError` to a 403 JSON body `{"ok": false, "error": <sentence>}`: a restricted installation is an expected, explained refusal, not a server fault. The sentence names the licence page and is what the frontends render. Raising it from any handler is the sanctioned way to refuse an agent run on licence grounds.
- The same block maps `helpers.file_browser.QuotaExceeded` to a 413 JSON body `{"ok": false, "error": <sentence>}`: an account over its storage quota is the caller's to fix. `api/upload.py`, `api/message.py` and `api/api_message.py` raise it.
- The dispatcher answers the endpoints in `helpers.api_policy.REFUSED_WHEN_RESTRICTED` with the same 403 while the installation is restricted. The check is made on every call, inside the cached wrapper and after authentication and the permission check, because the licence state changes while the wrapper stays cached.
- Every other exception is a genuine 500 whose body is `helpers.errors.error_text(e)` -- the message alone. The traceback goes to the log via `format_error`, and must not go to the client: the body is `text/plain` and `messageFor` in `frontend/src/api/client.ts` renders a plain string body verbatim, so a returned trace is shown on screen to whoever made the request, including a `user`-role account whose only permission is `chat.use`. `tests/test_api_error_disclosure.py` asserts both halves -- the message survives, the trace does not.
- **An endpoint has one spelling.** `_dispatch` answers 404 for any path with an empty, `.` or `..` segment before it looks anything up. The permission table (`helpers.api_policy`) and `PASSWORD_CHANGE_EXEMPT_ENDPOINTS` are looked up by string while the handler is found through the file system, so `/api/./settings_get` reached the same file and matched no entry; and `..` after `plugins/<name>/` left the plugin's `api/` directory. `tests/test_user_routes.py` pins this.
- The HTTP method check runs inside the cached handler call, after authentication and CSRF, because the wrapped handler is cached per path: a check made only before caching stopped applying after the first request.
- `requires_api_key` compares the presented key with `hmac.compare_digest` (`_same_secret`) and refuses when the configured key is empty.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_api_chat_lifetime.py`
  - `tests/test_browser_agent_regressions.py`
  - `tests/test_download_toast_regressions.py`
  - `tests/test_fasta2a_client.py`
  - `tests/test_fastmcp_openapi_security.py`
  - `tests/test_host_browser_connector.py`
  - `tests/test_image_get_security.py`
  - `tests/test_model_config_api_keys.py`

## Child DOX Index

No child DOX files.
