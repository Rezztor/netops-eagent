from abc import abstractmethod
import json
import threading
from urllib.parse import urlsplit, unquote
from functools import wraps
from pathlib import Path
from typing import Union, Dict, Any
from flask import (
    Request,
    Response,
    jsonify,
    Flask,
    session,
    request,
    send_file,
    redirect,
    url_for,
)
from werkzeug.wrappers.response import Response as BaseResponse
from helpers.print_style import PrintStyle
from helpers.errors import error_text, format_error
from helpers.security import same_secret
from helpers import files, cache

ThreadLockType = Union[threading.Lock, threading.RLock]

CACHE_AREA = "api_handlers(api)"
# cache.toggle_area(CACHE_AREA, False)  # cache off for now

Input = dict
Output = Union[Dict[str, Any], Response]


class ApiHandler:
    def __init__(self, app: Flask, thread_lock: ThreadLockType):
        self.app = app
        self.thread_lock = thread_lock

    @classmethod
    def requires_loopback(cls) -> bool:
        return False

    @classmethod
    def requires_api_key(cls) -> bool:
        return False

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    @classmethod
    def required_permission(cls) -> str | None:
        """RBAC permission required to call this endpoint, or None for any
        authenticated user. Enforced centrally in register_api_route()."""
        return None

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    @classmethod
    def requires_csrf(cls) -> bool:
        return cls.requires_auth()

    @classmethod
    def input_model(cls) -> type | None:
        """Pydantic model describing this endpoint's request body, or None.

        `Input` is a bare dict, so nothing here is validated at dispatch time and
        declaring a model does not change how the handler runs. It is read by
        `helpers.api_schema` to publish a real body schema in the OpenAPI
        document instead of a free-form object. Endpoints the frontend depends on
        should declare one; the rest can stay undeclared and be reported as such,
        which is honest about what is actually specified.
        """
        return None

    @classmethod
    def output_model(cls) -> type | None:
        """Pydantic model describing this endpoint's response body, or None.

        See `input_model`. Declared purely for documentation -- the handler still
        returns a plain dict, and the model is not used to serialise it.
        """
        return None

    @abstractmethod
    async def process(self, input: Input, request: Request) -> Output:
        pass

    async def handle_request(self, request: Request) -> Response:
        try:
            # input data from request based on type
            input_data: Input = {}
            if request.is_json:
                try:
                    if request.data:  # Check if there's any data
                        input_data = request.get_json()
                    # If empty or not valid JSON, use empty dict
                except Exception as e:
                    # Just log the error and continue with empty input
                    PrintStyle().print(f"Error parsing JSON: {str(e)}")
                    input_data = {}
            else:
                # input_data = {"data": request.get_data(as_text=True)}
                input_data = {}

            # process via handler
            output = await self.process(input_data, request)

            # return output based on type
            if isinstance(output, Response):
                return output
            else:
                response_json = json.dumps(output)
                return Response(
                    response=response_json, status=200, mimetype="application/json"
                )

        except Exception as e:
            from helpers.context_utils import ContextAccessDenied
            from helpers.task_scheduler import TaskAccessDenied

            if isinstance(e, ContextAccessDenied):
                return Response(
                    response=json.dumps({"ok": False, "error": "Context not found."}),
                    status=404,
                    mimetype="application/json",
                )

            if isinstance(e, TaskAccessDenied):
                return Response(
                    response=json.dumps({"ok": False, "error": "Task not found."}),
                    status=404,
                    mimetype="application/json",
                )

            from helpers.file_browser import QuotaExceeded

            if isinstance(e, QuotaExceeded):
                # The account is full: the caller's to fix, not a server fault.
                return Response(
                    response=json.dumps({"ok": False, "error": str(e)}),
                    status=413,
                    mimetype="application/json",
                )

            from helpers.license_entitlements import LicenceRestrictedError

            if isinstance(e, LicenceRestrictedError):
                # A restricted installation is an expected, explained refusal,
                # not a server fault: 403, the sentence that names the licence
                # page, and the rest of the product (sign-in, reading, export,
                # settings) stays reachable by design.
                return Response(
                    response=json.dumps({"ok": False, "error": str(e)}),
                    status=403,
                    mimetype="application/json",
                )

            # everything else is a genuine 500. The log gets the traceback and
            # the response gets the message alone; they used to be the same
            # string, which handed every signed-in user the call stack and the
            # absolute paths of this container. The frontend renders a
            # plain-text error body verbatim, so it reached the screen rather
            # than only the network tab -- and the lowest-privileged role could
            # read it by sending one malformed request.
            #
            # The ContextAccessDenied branch above already refuses to answer
            # with a traceback, for exactly this reason. This is that rule
            # applied to every other fault instead of the one that was noticed.
            PrintStyle.error(f"API error: {format_error(e)}")
            return Response(response=error_text(e), status=500, mimetype="text/plain")

    # get context to run rezztor eagent in
    def use_context(self, ctxid: str, create_if_not_exists: bool = True):
        from helpers.context_utils import use_context as _use_context
        return _use_context(self.thread_lock, ctxid, create_if_not_exists)


from helpers.network import is_loopback_address


def is_safe_next_url(value: str | None) -> bool:
    """Return True when value is a safe same-origin redirect target."""
    if not value:
        return False
    if "\r" in value or "\n" in value:
        return False
    # Reject raw backslashes (browsers normalize `/\host` to `//host` -> external).
    if "\\" in value:
        return False

    # Decode percent-escapes so encoded backslashes (e.g. `%5C`) are caught too.
    decoded = unquote(value)
    if "\\" in decoded:
        return False

    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc:
        return False

    # Require an absolute path within this origin, but reject protocol-relative URLs.
    return parsed.path.startswith("/") and not parsed.path.startswith("//")


def get_safe_next_url(value: str | None, fallback: str | None = None) -> str | None:
    """Return value if it is a safe next URL, otherwise return a safe fallback."""
    if is_safe_next_url(value):
        return value
    if is_safe_next_url(fallback):
        return fallback
    return None


def get_current_request_next_url() -> str:
    """Return the current request path/query as a safe relative redirect target."""
    next_url = request.full_path if request.query_string else request.path
    return get_safe_next_url(next_url, url_for("serve_index")) or url_for("serve_index")


def _same_secret(given, expected) -> bool:
    """Compare a presented key with the real one in constant time."""
    return same_secret(given, expected)


def requires_api_key(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        from helpers.settings import get_settings

        valid_api_key = get_settings()["mcp_server_token"]

        if api_key := request.headers.get("X-API-KEY"):
            if not _same_secret(api_key, valid_api_key):
                return Response("Invalid API key", 401)
        elif request.json and request.json.get("api_key"):
            api_key = request.json.get("api_key")
            if not _same_secret(api_key, valid_api_key):
                return Response("Invalid API key", 401)
        else:
            return Response("API key required", 401)
        return await f(*args, **kwargs)

    return decorated


def requires_loopback(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        if not is_loopback_address(str(request.remote_addr)):
            return Response("Access denied.", 403, {})
        return await f(*args, **kwargs)

    return decorated


def _wants_json_response() -> bool:
    """True for API/XHR callers, which need a status code rather than a redirect."""
    if request.path.startswith("/api/"):
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


def _deny(status: int, message: str):
    if _wants_json_response():
        return Response(
            response=json.dumps({"ok": False, "error": message}),
            status=status,
            mimetype="application/json",
        )
    if status == 401:
        return redirect(url_for("login_handler", next=get_current_request_next_url()))
    return Response(response=message, status=status, mimetype="text/plain")


def requires_auth(f):
    """Reject unauthenticated requests.

    This fails CLOSED. Upstream returned the handler unguarded whenever no
    credentials were configured, which left every endpoint -- including code
    execution -- open to anyone who could reach the port.
    """

    @wraps(f)
    async def decorated(*args, **kwargs):
        from helpers import login

        if not login.is_authenticated():
            return _deny(401, "Authentication required.")
        return await f(*args, **kwargs)

    return decorated


# Endpoints that must stay reachable while an account is pending a forced
# password change, or the user could not complete it.
PASSWORD_CHANGE_EXEMPT_ENDPOINTS = frozenset(
    # "bootstrap" is exempt for the same reason as "whoami": a user who must
    # change their password still has to load the interface far enough to be
    # shown the change-password screen.
    {"change_password", "whoami", "logout", "csrf_token", "health", "bootstrap"}
)


def requires_password_change_complete(f):
    """Block API access until a pending forced password change is done.

    Applied only to /api/ endpoints. Page routes stay reachable so the WebUI can
    render the change-password interstitial.
    """

    @wraps(f)
    async def decorated(*args, **kwargs):
        from helpers import login

        if login.must_change_password():
            return _deny(403, "password_change_required")
        return await f(*args, **kwargs)

    return decorated


def requires_permission(permission: str):
    """Gate a handler on an RBAC permission. Assumes requires_auth ran first."""

    def decorator(f):
        @wraps(f)
        async def decorated(*args, **kwargs):
            from helpers import login

            if not login.is_authenticated():
                return _deny(401, "Authentication required.")
            if not login.has_permission(permission):
                return _deny(
                    403,
                    f"Your role does not grant the required permission: {permission}.",
                )
            return await f(*args, **kwargs)

        return decorated

    return decorator


def csrf_protect(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        # The header, and only the header. The csrf_token_<runtime> cookie
        # used to pass on its own, and a cookie is exactly what the browser
        # attaches to a request the page did not write -- so it proved nothing
        # a forged request could not also show. The cookie stays for the
        # WebSocket handshake (helpers/ws.py), which checks it alongside the
        # token the client sends, never instead of it.
        token = session.get("csrf_token")
        sent = request.headers.get("X-CSRF-Token")
        if not _same_secret(sent or "", token):
            return Response("CSRF token missing or invalid", 403)
        return await f(*args, **kwargs)

    return decorated


def register_api_route(app: Flask, lock: ThreadLockType) -> None:
    from helpers.modules import load_classes_from_file
    from helpers import modules, plugins

    async def _dispatch(path: str) -> BaseResponse:
        # One spelling per endpoint. The permission table and the forced
        # password change both look the path up as a string, while the handler
        # is found through the file system -- so `./settings_get` reached the
        # same file as `settings_get` and matched no entry in either, which
        # handed every administrative endpoint to the lowest role.
        if any(segment in ("", ".", "..") for segment in path.split("/")):
            return Response(f"API endpoint not found: {path}", 404)

        # Return cached wrapped handler if available
        cached = cache.get(CACHE_AREA, path)
        if cached is not None:
            return await cached()

        # Resolve file path for the handler
        # Try built-in and plugin api folders before the user fallback
        handler_cls: type[ApiHandler] | None = None

        # Check built-in python/api/<path>.py
        builtin_file = files.get_abs_path(f"api/{path}.py")
        if files.is_in_dir(builtin_file, files.get_abs_path("api")) and modules.find(
            builtin_file
        ):
            classes = load_classes_from_file(builtin_file, ApiHandler)
            if classes:
                handler_cls = classes[0]

        # Check plugin api folders: path format plugins/<plugin_name>/<handler>
        if handler_cls is None and path.startswith("plugins/"):
            parts = path.split("/", 2)
            if len(parts) == 3:
                _, plugin_name, handler_name = parts
                plugin_dir = plugins.find_plugin_dir(plugin_name)
                if plugin_dir:
                    plugin_file = Path(plugin_dir) / "api" / f"{handler_name}.py"
                    if modules.find(str(plugin_file)):
                        classes = load_classes_from_file(str(plugin_file), ApiHandler)
                        if classes:
                            handler_cls = classes[0]

        # Check user api/<path>.py
        if handler_cls is None:
            user_api_dir = files.get_abs_path(files.USER_DIR, files.API_DIR)
            user_file = files.get_abs_path(user_api_dir, f"{path}.py")
            if files.is_in_dir(user_file, user_api_dir) and modules.find(user_file):
                classes = load_classes_from_file(user_file, ApiHandler)
                if classes:
                    handler_cls = classes[0]

        if handler_cls is None:
            return Response(f"API endpoint not found: {path}", 404)

        # A file holding only a base class is not an endpoint. ApiHandler is a
        # plain class, not an ABC, so @abstractmethod does not stop `process`
        # from being instantiated and called -- it just returns None. That made
        # plugins/_ea_connector/api/v1/base.py answer /api/.../v1/base with
        # `null` and HTTP 200, unauthenticated, because the base it defines
        # declares requires_auth() False for its subclasses to inherit.
        # Reject anything that never implemented process, so dropping a base or
        # mixin into an api/ directory cannot publish an endpoint by accident.
        if getattr(handler_cls.process, "__isabstractmethod__", False):
            return Response(f"API endpoint not found: {path}", 404)

        # Build handler call, wrapping with security decorators as required.
        # The method check lives inside it: the wrapped handler is cached, and
        # a check made only on the way to the cache stopped applying after the
        # first request, so a POST-only endpoint then answered GET as well.
        async def call_handler() -> BaseResponse:
            if request.method not in handler_cls.get_methods():
                return Response(f"Method {request.method} not allowed for: {path}", 405)
            # Asked on every call, not once on the way to the cache: the
            # licence state changes while the wrapped handler stays cached.
            if path in api_policy.REFUSED_WHEN_RESTRICTED:
                from helpers.license_entitlements import restricted_refusal

                refusal = restricted_refusal()
                if refusal:
                    return Response(
                        response=json.dumps({"ok": False, "error": refusal}),
                        status=403,
                        mimetype="application/json",
                    )
            instance = handler_cls(app, lock)
            return await instance.handle_request(request=request)

        handler_fn = call_handler
        if handler_cls.requires_csrf():
            handler_fn = csrf_protect(handler_fn)
        if handler_cls.requires_api_key():
            handler_fn = requires_api_key(handler_fn)
        # Handler-declared permission wins; otherwise consult the central policy
        # table so sensitive endpoints cannot be left ungated by omission.
        from helpers import api_policy

        required_permission = (
            handler_cls.required_permission() or api_policy.required_permission_for(path)
        )
        if required_permission:
            handler_fn = requires_permission(required_permission)(handler_fn)
        if path.strip("/") not in PASSWORD_CHANGE_EXEMPT_ENDPOINTS:
            handler_fn = requires_password_change_complete(handler_fn)
        if handler_cls.requires_auth():
            handler_fn = requires_auth(handler_fn)
        if handler_cls.requires_loopback():
            handler_fn = requires_loopback(handler_fn)

        cache.add(CACHE_AREA, path, handler_fn)
        return await handler_fn()

    app.add_url_rule(
        "/api/<path:path>",
        "api_dispatch",
        _dispatch,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )


def register_watchdogs():
    from helpers import watchdog
    from helpers.ws import CACHE_AREA as WS_CACHE_AREA


    def on_api_change(items: list[watchdog.WatchItem]):
        PrintStyle.debug("API endpoint watchdog triggered:", items)
        cache.clear(CACHE_AREA)
        cache.clear(WS_CACHE_AREA)

    watchdog.add_watchdog(
        "api_handlers",
        roots=[
            files.get_abs_path(files.API_DIR),
            files.get_abs_path(files.USER_DIR, files.API_DIR),
        ],
        patterns=["*.py"],
        handler=on_api_change,
    )
