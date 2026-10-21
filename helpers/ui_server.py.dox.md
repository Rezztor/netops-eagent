# ui_server.py DOX

## Purpose

- Own the `ui_server.py` helper module.
- This module configures and owns Flask/WebUI runtime route handlers.
- Keep this file-level DOX profile synchronized with `ui_server.py` because this directory is intentionally flat.

## Ownership

- `ui_server.py` owns the runtime implementation.
- `ui_server.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `HttpsSessionInterface` (`flask.sessions.SecureCookieSessionInterface`)
  - `get_cookie_secure(self, app: Flask) -> bool`
- `UiServerRuntime` (no explicit base class)
  - `create(cls) -> 'UiServerRuntime'`
  - `refresh_runtime_settings(self) -> None`
  - `register_http_routes(self) -> None`
  - `register_transport_handlers(self) -> None`
  - `build_asgi_app(self, startup_monitor: StartupMonitor)`
  - `access_log_enabled(self) -> bool`
- `UiRouteHandlers` (no explicit base class)
  - `async login_handler(self)`
  - `async logout_handler(self)`
  - `async serve_splash(self)`
  - `async serve_safe(self)`
  - `async serve_index(self)`
  - `async serve_ui_asset_bundle(self)`
  - `async serve_builtin_plugin_asset(self, plugin_name, asset_path)`
  - `async serve_plugin_asset(self, plugin_name, asset_path)`
  - `async serve_extension_asset(self, asset_path)`
  - `async serve_user_extension_asset(self, asset_path)`
- Top-level functions:
- `_positive_int_env(name: str, default: int) -> int`
- `bound_request_bodies(webapp: Flask) -> None`
- `refuse_to_be_framed(webapp: Flask) -> None`: every response carries `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin` unless it set its own (the application shell sets a full policy, `helpers/spa.py`); the interface frames nothing.
- `configure_process_environment() -> None`
- Notable constants/configuration names: `UPLOAD_LIMIT_BYTES`, `MEMORY_BODY_LIMIT_BYTES`, `ANONYMOUS_BODY_LIMIT_BYTES`, `SOCKETIO_PING_INTERVAL_SECONDS`, `SOCKETIO_PING_TIMEOUT_SECONDS`, `GZIP_MINIMUM_RESPONSE_BYTES`, `GZIP_COMPRESSION_LEVEL`, `UI_INDEX_ASSET_URL`, `REZZTOR_SOCKETIO_PING_INTERVAL_SECONDS`, `REZZTOR_SOCKETIO_PING_TIMEOUT_SECONDS`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- The session cookie is `Secure` when the request is HTTPS, or when the first `X-Forwarded-Proto` value is `https` (TLS ends at a proxy in front, and uvicorn trusts forwarded headers only from the addresses in `FORWARDED_ALLOW_IPS`: none in a hand install, the gateway of `app_net` on the appliance, where nginx sets `X-Forwarded-For` to the browser's address so that the sign-in throttle counts each browser apart). The header is read for this flag alone and can only add it: never trust `X-Forwarded-For` the same way, since `requires_loopback` (`helpers/api.py`, `helpers/ws.py`) decides on the client address. `SESSION_COOKIE_SECURE=true` forces the flag. Plain-HTTP local development keeps working. Signing stays compatible with Flask's plain `SecureCookieSessionInterface`. `tests/test_session_cookie.py` covers this.
- Socket.IO heartbeat defaults are intentionally longer than Engine.IO's short defaults so CLI sessions survive long prompt/context work; environment overrides must remain positive integers and fall back to source defaults when invalid.
- Observed side-effect areas: filesystem reads, network calls, subprocess/runtime control, WebSocket state, plugin state, settings/state persistence, secret handling.
- Imported dependency areas include: `asyncio`, `dataclasses`, `datetime`, `flask`, `helpers`, `helpers.api`, `helpers.extension`, `helpers.files`, `helpers.print_style`, `helpers.server_startup`, `helpers.ws`, `helpers.ws_manager`, `logging`, `os`, `secrets`, `socketio`, `starlette.middleware.gzip`.
- Request bodies are bounded by who sent them, on the declared `Content-Length`, before a byte is read (`bound_request_bodies`, a `before_request` hook installed by `create()`): 1 MiB from a caller with neither a session nor the correct `X-API-KEY`; 50 MiB for anything held in memory (a JSON body, a form, the text fields of an upload, via Werkzeug's `max_form_memory_size`); `UPLOAD_LIMIT_BYTES` for a signed-in account's multipart upload, whose files go to disk. Over the bound is `413`. `/mcp`, `/a2a` and Socket.IO are not Flask and are not covered. `tests/test_request_body_bounds.py` covers this.
- `POST /login` sits outside the API dispatcher, so it carries its own CSRF defence: every render of the sign-in page (`_login_page`) puts `login.login_form_token()` in a hidden `login_form` field, and a POST whose field does not match the session's token is answered with a fresh form and "Your sign-in page had expired" before the throttle or bcrypt run. It stops login CSRF (another site signing the visitor in as its own account). `tests/test_login_form_csrf.py` covers this.
- `/forgot-password` carries the sign-in form's token (`login.login_form_token`) and refuses a POST without it, like `/login`; a source address over `password_reset.address_may_request` (given the peer and its `X-Forwarded-For`) is told to try later. Otherwise it hands the request to `password_reset.send_link_later` and answers at once, so its timing does not say whether the identifier holds an account.
- `GET /logout` does not sign out; it redirects to the application. Sign-out is `POST /api/logout` with the CSRF header.

## Key Concepts

- Important called helpers/classes observed in the source: `logging.getLogger.setLevel`, `Localization.get.apply_process_timezone`, `_positive_int_env`, `field`, `Flask`, `threading.RLock`, `socketio.AsyncServer`, `WsManager`, `set_shared_ws_manager`, `cls`, `server_runtime.refresh_runtime_settings`, `settings_helper.get_settings`, `settings_helper.set_runtime_settings_snapshot`, `self.ws_manager.set_server_restart_broadcast`, `UiRouteHandlers`, `self.webapp.add_url_rule`, `register_api_route`, `register_ws_namespace`, `files.read_file`, `render_template_string`, `session.pop`.
- `serve_index()` bootstraps the normalized UI control visibility map, timezone and time-format preferences, and the complete enabled WebUI extension manifest so startup extension discovery requires no per-surface API requests.
- Authenticated extension asset routes serve root-contained files from both `extensions/webui/` and `usr/extensions/webui/`, matching the URLs emitted by the WebUI extension manifest.
- The authenticated `/` route uses `serve_splash()` to return the no-store, self-contained bootstrap document. The authenticated extensionless `/ui/index` route renders the existing index and runtime/user placeholders for the splash to install into the current document without navigation; `/index.html` remains a direct fallback for the same rendering path. The authenticated `/safe` route first returns a no-store, self-contained document that unregisters all origin service workers, then renders the existing index through `serve_index()` when its internal `__direct=1` marker is present; it never initializes the asset bundle or a worker. The authenticated `serve_ui_asset_bundle()` endpoint passes the application entry URL to the generic recursive bundler and supports gzip transfer and payload-specific ETag revalidation while component, extension, and Alpine lifecycles remain unchanged.
- The Starlette HTTP branch applies negotiated gzip to responses of at least 1 KiB at compression level 6 while preserving already encoded responses; Socket.IO remains outside that middleware branch.
- Flask is bridged to ASGI by `a2wsgi.WSGIMiddleware`, imported by name. uvicorn's `uvicorn.middleware.wsgi.WSGIMiddleware` is the same class only while `a2wsgi` is installed; without it uvicorn uses its own older bridge, which sends header names in the case Flask wrote them (the gzip layer then keeps the uncompressed `Content-Length` and a file arrives cut short) and reads a request body whole. `tests/test_spa_serving.py` holds a compressed file to its declared length.
- The local sign-in form is throttled through `helpers.login_throttle`, checked before `users.authenticate` so a blocked caller costs a lookup rather than a bcrypt hash. It was guarded only by `await asyncio.sleep(1)` after a failure, which delays one connection and limits nothing. A refusal emits `auth.signin` with `outcome: throttled`, so the attempt is still in the audit trail.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- No direct test reference was found by name search; choose the nearest behavioral test or perform a focused smoke check.

## Child DOX Index

No child DOX files.
