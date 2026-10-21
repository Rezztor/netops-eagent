# ws.py DOX

## Purpose

- Own the `ws.py` helper module.
- This module defines WebSocket handler registration, origin validation, and security checks.
- Keep this file-level DOX profile synchronized with `ws.py` because this directory is intentionally flat.

## Ownership

- `ws.py` owns the runtime implementation.
- `ws.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `ConnectionNotFoundError` (`RuntimeError`)
- `_SecurityContext` (no explicit base class)
- `WsHandler` (no explicit base class)
  - `namespace(self) -> str`
  - `manager(self) -> 'WsManager'`
  - `identifier(self) -> str`
  - `bind_manager(self, manager: 'WsManager', namespace: str | None=...) -> None`
  - `requires_loopback(cls) -> bool`
  - `requires_api_key(cls) -> bool`
  - `requires_auth(cls) -> bool`
  - `requires_csrf(cls) -> bool`
- Top-level functions:
- `_ws_debug_enabled() -> bool`: Check REZZTOR_WS_DEBUG env var - lightweight, no heavy imports.
- `ws_debug(message: str) -> None`: Log *message* via :class:`PrintStyle` when ``REZZTOR_WS_DEBUG`` is active.
- `_default_port_for_scheme(scheme: str) -> int | None`
- `normalize_origin(value: Any) -> str | None`: Normalize an Origin/Referer header value to scheme://host[:port].
- `_parse_host_header(value: Any) -> tuple[str | None, int | None]`
- `validate_ws_origin(environ: dict[str, Any]) -> tuple[bool, str | None]`: Validate the browser Origin during the Socket.IO handshake.
- `_account_now(ctx: _SecurityContext) -> dict[str, Any] | None`: the account behind a socket as the store has it now, or None; also None once the socket is idle (`_idle`).
- `idle_sids() -> list[str]`: sockets whose client has sent nothing for longer than `login.IDLE_HOURS`. `_SecurityContext.last_seen` is set at connect and on every message the client sends (a message is activity, as a request is over HTTP); a message on an idle socket disconnects it and answers `AUTH_REQUIRED`, and `extensions/python/job_loop/_40_close_idle_sockets.py` closes idle sockets that only listen, within a minute. A screen that polls keeps its session and its socket alive indefinitely, as it does over HTTP; that is accepted.
- `_check_security(handler_cls: type[WsHandler], ctx: _SecurityContext) -> dict[str, Any] | None`: Return an error payload dict if the check fails, or ``None`` on success.
- `register_ws_namespace(socketio_server: socketio.AsyncServer, webapp: Flask, lock: ThreadLockType, manager: 'WsManager | None'=...) -> None`
- `_error_response(code: str, message: str, correlation_id: str) -> dict[str, Any]`
- Notable constants/configuration names: `NAMESPACE`, `CACHE_AREA`.

## Runtime Contracts

- A WebSocket handler is found by its `.py` name through `helpers.modules.find`. Under `usr/` only a `.py` file counts.
- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- `WsHandler` defines `process(...)`.
- `WsHandler` defines `requires_auth(...)`.
- `WsHandler` defines `requires_csrf(...)`.
- `WsHandler` defines `requires_api_key(...)`.
- `WsHandler` defines `requires_loopback(...)`.
- Observed side-effect areas: filesystem reads, filesystem deletion, network calls, WebSocket state, plugin state, settings/state persistence, secret handling.
- Imported dependency areas include: `abc`, `dataclasses`, `flask`, `helpers`, `helpers.errors`, `helpers.network`, `helpers.print_style`, `os`, `pathlib`, `socketio`, `threading`, `typing`, `urllib.parse`, `uuid`.
- `WsHandler.requires_development()` marks a test harness; `_check_security` refuses such a handler with `NOT_AVAILABLE` unless `runtime.is_development()`, before any other check. `api/ws_dev_test.py` and `api/ws_hello.py` declare it.

## Key Concepts

- Important called helpers/classes observed in the source: `threading.Lock`, `os.getenv.strip.lower`, `_ws_debug_enabled`, `urlparse`, `normalize_origin`, `_parse_host_header`, `handler_cls.requires_loopback`, `handler_cls.requires_auth`, `handler_cls.requires_csrf`, `handler_cls.requires_api_key`, `socketio_server.on`, `PrintStyle.debug`, `value.strip`, `origin_parsed.hostname.lower`, `_default_port_for_scheme`, `req_host.lower`, `forwarded_host_raw.strip`, `forwarded_host_raw.split.strip`, `forwarded_proto_raw.strip`, `forwarded_proto_raw.split.strip.lower`.
- `_check_security` enforces permissions as well as authentication, the forced password change, CSRF and the API key: a handler's own `required_permission()` or the entry in `helpers/api_policy.py` under the handler's dispatch name. `WsHandler` had no such hook and `_check_security` consulted neither, so a handler declaring a permission would have received no error and no enforcement -- worse than the gate being missing. Nothing shipped was bypassed; no WebSocket handler needed one. The account is read again from the store on every message (`_account_now(ctx)`, by the id captured at connect): a disabled or deleted account, one whose password changed since the socket opened (`password_stamp`), or one that cannot be read answers `AUTH_REQUIRED`, and the role and the forced password change are the account's as they are now, as over HTTP. The `role` and `must_change_password` captured at connect no longer decide anything. Messages the server pushes to a socket that is already open are not covered by this check. `tests/test_ws_permissions.py` covers it. The CSRF token, its cookie and the API key are compared with `helpers.security.same_secret`.
- `_on_connect` refuses the handshake (`return False`) when there is no signed-in account, after the Origin check and before any handler name is resolved, as HTTP refuses before the handler runs. An admitted anonymous socket received every lifecycle broadcast (sids, connection counts, `server_restart`), held a manager record and had each frame decoded before `NO_HANDLERS`. A consequence: a handler that overrides `requires_auth()` to `False` (loopback- or API-key-only) cannot be activated over this transport. The connection is registered with the manager under `ctx.user_id`, never a placeholder string. The frontend opens the socket only inside `AppShell`, which mounts after an authenticated bootstrap. `tests/test_ws_connect_gate.py` pins both.
- `_resolve_handler` sets `handler_cls._dispatch_path` to the connect-payload path that resolved it, so `_dispatch_name` (and the `api_policy` lookup) uses `plugins/<plugin>/<handler>` for a plugin handler instead of the module basename that never matched a table key. `tests/test_ws_connect_gate.py::test_a_plugin_socket_handler_is_gated_by_its_policy_key` pins it. `REFUSED_WHEN_RESTRICTED` still has no socket counterpart.
- `_resolve_handler` refuses a handler name with an empty, `.` or `..` segment. The name comes from the client's connect payload (`auth.handlers`), is resolved before the security check, and resolving imports the file -- so a name with `..` executed any `.py` on disk, an uploaded one included, inside the web server. `tests/test_ws_security.py::test_a_handler_name_cannot_leave_the_api_directories` pins it.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Prefer adding cohesive helper functions here only when behavior is reused across modules.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_ea_connector_computer_use_metadata.py`
  - `tests/test_ea_connector_prompt_gating.py`
  - `tests/test_browser_agent_regressions.py`
  - `tests/test_docker_release_plan.py`
  - `tests/test_download_toast_regressions.py`
  - `tests/test_git_version_label.py`
  - `tests/test_host_browser_connector.py`
  - `tests/test_multi_tab_isolation.py`

## Child DOX Index

No child DOX files.
