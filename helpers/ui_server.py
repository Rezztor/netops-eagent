from dataclasses import dataclass, field
from datetime import timedelta
import asyncio
import gzip
import json
import logging
import os
import secrets
import threading
import time
from typing import Any

from flask import (
    Flask,
    Response,
    has_request_context,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from flask.sessions import SecureCookieSessionInterface
from socketio import ASGIApp
from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.routing import Mount
from a2wsgi import WSGIMiddleware  # by name: uvicorn's own bridge is what it quietly uses when this is missing
from werkzeug.wrappers.request import Request as WerkzeugRequest
import socketio  # type: ignore[import-untyped]

from helpers import dotenv, fasta2a_server, files, git, login, mcp_server, runtime
from helpers.api import get_safe_next_url, register_api_route, requires_auth
from helpers.extension import extensible
from helpers.files import get_abs_path
from helpers.print_style import PrintStyle
from helpers.server_startup import StartupMonitor
from helpers import settings as settings_helper
from helpers.ws import register_ws_namespace, validate_ws_origin
from helpers.ws_manager import WsManager, set_shared_ws_manager


UPLOAD_LIMIT_BYTES = 5 * 1024 * 1024 * 1024
SOCKETIO_PING_INTERVAL_SECONDS = 45
SOCKETIO_PING_TIMEOUT_SECONDS = 120
GZIP_MINIMUM_RESPONSE_BYTES = 1024
GZIP_COMPRESSION_LEVEL = 6


#: What one request may make the server hold in memory: a JSON body, a form,
#: the text fields of an upload. Files are not held; they are written to disk
#: as they arrive and `UPLOAD_LIMIT_BYTES` is their bound.
MEMORY_BODY_LIMIT_BYTES = 50 * 1024 * 1024
#: The same for a caller who has shown neither a session nor the API key. The
#: sign-in form, the password reset and the search for a key in the body are
#: all read before anybody has proved anything.
ANONYMOUS_BODY_LIMIT_BYTES = 1024 * 1024


def _holds_the_api_key() -> bool:
    from helpers.api import _same_secret

    try:
        expected = settings_helper.get_settings()["mcp_server_token"]
    except Exception:  # noqa: BLE001 - no settings, no key
        return False
    return _same_secret(request.headers.get("X-API-KEY", ""), expected)


def refuse_to_be_framed(webapp: Flask) -> None:
    """Tell the browser never to draw a response of this server inside a frame.

    The session cookie is `SameSite=Lax`, which keeps it out of a frame on an unrelated site, but a page on another
    host of the same organisation is the same site: it could show the interface under its own picture and have a
    signed-in engineer press a button on it. The interface frames nothing of its own. A response that sets a policy
    of its own (the image and attachment endpoints, the application shell) keeps it.

    Every response also says `nosniff`, so a file served as text is never run
    as script, and `Referrer-Policy: same-origin`, so a link out of the
    interface does not carry the page's URL (a chat or task id) to another site.
    """

    @webapp.after_request
    def _no_frames(response):
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


def bound_request_bodies(webapp: Flask) -> None:
    """Refuse a body larger than its sender may ask the server to hold.

    The upload limit used to be the only bound and it applied to everything,
    so a five-gigabyte JSON document or form was read whole into memory, from a
    caller who had not signed in as readily as from one who had. Decided on
    the declared length, before a byte is read; a body with no declared length
    is read as empty by Werkzeug.
    """
    # Werkzeug's own bound on a form's non-file fields. Flask 3.0 reads the
    # class attribute and Flask 3.1 the setting, so both say it.
    WerkzeugRequest.max_form_memory_size = MEMORY_BODY_LIMIT_BYTES
    webapp.config["MAX_FORM_MEMORY_SIZE"] = MEMORY_BODY_LIMIT_BYTES

    @webapp.before_request
    def _refuse_a_body_too_large_to_hold():
        length = request.content_length or 0
        if length <= ANONYMOUS_BODY_LIMIT_BYTES:
            return None
        # The cookie is signed, so a user id in it was put there by a sign-in.
        # Whether that account may still do anything is `requires_auth`'s
        # question; this one is only how much is read before it is asked.
        if session.get(login.SESSION_USER_ID):
            if request.mimetype == "multipart/form-data":
                return None
        elif not _holds_the_api_key():
            return Response("Request body too large.", 413)
        if length > MEMORY_BODY_LIMIT_BYTES:
            return Response("Request body too large.", 413)
        return None


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class HttpsSessionInterface(SecureCookieSessionInterface):
    """The session cookie is `Secure` whenever the browser reached us over HTTPS.

    TLS ends at a proxy in front of the application -- nginx on the
    appliance, the customer's own elsewhere -- so what arrives here is plain
    HTTP, and uvicorn believes forwarded headers only from the addresses in
    `FORWARDED_ALLOW_IPS`. That is empty unless a deployment names its proxy
    (the appliance does: its port is published on 127.0.0.1, so only the host
    can be that address), because believing a proxy also means believing its
    `X-Forwarded-For`, and the loopback-only endpoints (`requires_loopback`)
    decide on the client address it sets. Here the header can only add
    `Secure`, and a browser will not store a `Secure` cookie from a plain-HTTP
    page, so a forged one breaks nothing but the forger's own sign-in.

    `SESSION_COOKIE_SECURE=true` forces the flag, for a proxy that does not send
    the header.
    """

    def get_cookie_secure(self, app: Flask) -> bool:
        if app.config["SESSION_COOKIE_SECURE"]:
            return True
        if not has_request_context():
            return False
        forwarded = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
        return request.is_secure or forwarded == "https"


def configure_process_environment() -> None:
    logging.getLogger().setLevel(logging.WARNING)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from helpers.localization import Localization

    Localization.get().apply_process_timezone()


@dataclass
class UiServerRuntime:
    webapp: Flask
    socketio_server: socketio.AsyncServer
    ws_manager: WsManager
    lock: threading.RLock
    settings_snapshot: dict[str, Any]
    _routes_registered: bool = False
    _transport_registered: bool = False
    _route_handlers: "UiRouteHandlers | None" = field(default=None, init=False)

    @classmethod
    def create(cls) -> "UiServerRuntime":
        # No static_folder: the inherited webui/ directory it pointed at is gone,
        # and the built frontend is served from /app by helpers.spa, which does
        # its own containment checks rather than exposing a directory at the root.
        webapp = Flask("app", static_folder=None)
        webapp.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
        webapp.session_interface = HttpsSessionInterface()

        bound_request_bodies(webapp)
        refuse_to_be_framed(webapp)
        webapp.config.update(
            JSON_SORT_KEYS=False,
            # Persistent, not per-process: see runtime.get_session_cookie_id.
            # Naming this after the process id logged every user out on every
            # restart.
            SESSION_COOKIE_NAME="session_" + runtime.get_session_cookie_id(),
            SESSION_COOKIE_SAMESITE="Lax",
            SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes"),
            SESSION_PERMANENT=True,
            PERMANENT_SESSION_LIFETIME=timedelta(days=1),
            MAX_CONTENT_LENGTH=int(
                os.getenv("FLASK_MAX_CONTENT_LENGTH", str(UPLOAD_LIMIT_BYTES))
            ),

        )

        lock = threading.RLock()
        socketio_server = socketio.AsyncServer(
            async_mode="asgi",
            namespaces="*",
            cors_allowed_origins=lambda _origin, environ: validate_ws_origin(environ)[0],
            logger=False,
            engineio_logger=False,
            ping_interval=_positive_int_env(
                "REZZTOR_SOCKETIO_PING_INTERVAL_SECONDS",
                SOCKETIO_PING_INTERVAL_SECONDS,
            ),
            ping_timeout=_positive_int_env(
                "REZZTOR_SOCKETIO_PING_TIMEOUT_SECONDS",
                SOCKETIO_PING_TIMEOUT_SECONDS,
            ),
            max_http_buffer_size=50 * 1024 * 1024,
        )

        ws_manager = WsManager(socketio_server, lock)
        set_shared_ws_manager(ws_manager)

        server_runtime = cls(
            webapp=webapp,
            socketio_server=socketio_server,
            ws_manager=ws_manager,
            lock=lock,
            settings_snapshot={},
        )
        server_runtime.refresh_runtime_settings()
        return server_runtime

    def refresh_runtime_settings(self) -> None:
        self.settings_snapshot = settings_helper.get_settings()
        settings_helper.set_runtime_settings_snapshot(self.settings_snapshot)
        self.ws_manager.set_server_restart_broadcast(
            self.settings_snapshot.get("websocket_server_restart_enabled", True)
        )

    def register_http_routes(self) -> None:
        if self._routes_registered:
            return

        from helpers import spa

        handlers = UiRouteHandlers(self)
        self._route_handlers = handlers
        self.webapp.add_url_rule(
            "/login",
            "login_handler",
            handlers.login_handler,
            methods=["GET", "POST"],
        )
        # Reachable without a session, necessarily: the people who need them are
        # the people who cannot sign in.
        self.webapp.add_url_rule(
            "/forgot-password",
            "forgot_password_handler",
            handlers.forgot_password_handler,
            methods=["GET", "POST"],
        )
        self.webapp.add_url_rule(
            "/reset-password",
            "reset_password_handler",
            handlers.reset_password_handler,
            methods=["GET", "POST"],
        )
        self.webapp.add_url_rule(
            "/logout",
            "logout_handler",
            handlers.logout_handler,
            methods=["GET"],
        )
        # The identity provider redirects the browser back here with an
        # authorization code. Unauthenticated by necessity -- it is how a session
        # begins -- and safe because the code is worthless without the `state`
        # and PKCE verifier held server-side in this browser's session.
        self.webapp.add_url_rule(
            "/auth/callback",
            "oidc_callback_handler",
            handlers.oidc_callback_handler,
            methods=["GET"],
        )
        # The built frontend. The router is mounted at /app -- vite's `base` and
        # the BrowserRouter basename both say so -- so /app is the only place the
        # shell can actually route. The root redirects there rather than serving
        # the shell itself: served at "/", the bundle loads, finds a location
        # outside its basename, matches no route and paints an empty page. That
        # looked exactly like a broken build and was not one.
        self.webapp.add_url_rule(
            "/",
            "serve_index",
            handlers.redirect_to_app,
            methods=["GET"],
        )
        # strict_slashes=False so /app and /app/ both land here. The
        # <path:asset_path> rule below cannot cover the trailing-slash form --
        # that converter needs at least one character -- so without this, /app/
        # is a 404 in the middle of the happy path, which is where a signed-in
        # user is sent.
        self.webapp.add_url_rule(
            "/app",
            "serve_spa_root",
            handlers.serve_spa,
            methods=["GET"],
            strict_slashes=False,
        )
        self.webapp.add_url_rule(
            "/app/<path:asset_path>",
            "serve_spa_asset",
            handlers.serve_spa,
            methods=["GET"],
        )
        # Outside the mount and outside authentication, because the sign-in page
        # renders before a session exists and still has to be in the brand face.
        # spa.serve_font answers only for woff2 files under dist/fonts.
        self.webapp.add_url_rule(
            f"{spa.FONTS_PATH}/<path:asset_path>",
            "serve_font",
            handlers.serve_font,
            methods=["GET"],
        )
        self._routes_registered = True

    def register_transport_handlers(self) -> None:
        if self._transport_registered:
            return
        register_api_route(self.webapp, self.lock)
        register_ws_namespace(
            self.socketio_server,
            self.webapp,
            self.lock,
            manager=self.ws_manager,
        )
        self._transport_registered = True

    def build_asgi_app(self, startup_monitor: StartupMonitor):
        with startup_monitor.stage("wsgi.middleware.create"):
            wsgi_app = WSGIMiddleware(self.webapp)

        with startup_monitor.stage("mcp.proxy.init"):
            mcp_app = mcp_server.DynamicMcpProxy.get_instance()

        with startup_monitor.stage("a2a.proxy.init"):
            a2a_app = fasta2a_server.DynamicA2AProxy.get_instance()

        with startup_monitor.stage("starlette.app.create"):
            starlette_app = Starlette(
                routes=[
                    Mount("/mcp", app=mcp_app),
                    Mount("/a2a", app=a2a_app),
                    Mount("/", app=wsgi_app),
                ],
                lifespan=startup_monitor.lifespan(),
            )
            compressed_http_app = GZipMiddleware(
                starlette_app,
                minimum_size=GZIP_MINIMUM_RESPONSE_BYTES,
                compresslevel=GZIP_COMPRESSION_LEVEL,
            )

        with startup_monitor.stage("socketio.asgi.create"):
            return ASGIApp(self.socketio_server, other_asgi_app=compressed_http_app)

    def access_log_enabled(self) -> bool:
        return self.settings_snapshot.get("uvicorn_access_logs_enabled", True)


def _audit_emit(event: str, **fields):
    """Emit an audit event without letting a logging failure break sign-in."""
    try:
        from plugins._audit.helpers import audit

        audit.emit(event, **fields)
    except Exception:
        pass


def _login_page(**context):
    from helpers.login_page import LOGIN_PAGE

    return render_template_string(LOGIN_PAGE, login_form=login.login_form_token(), **context)


class UiRouteHandlers:
    def __init__(self, runtime_state: UiServerRuntime) -> None:
        self.runtime = runtime_state

    @extensible
    async def forgot_password_handler(self):
        """Ask for a reset link.

        The reply never varies. Telling a caller that an address is unknown
        turns this page into a way to find out who holds an account here, and
        the page is reachable by anyone who can see the sign-in screen.
        """
        from helpers import mailer, password_reset
        from helpers.login_page import FORGOT_PAGE

        notice = error = None
        if request.method == "POST" and not login.login_form_token_valid(request.form.get("login_form")):
            # The sign-in form's rule: a form this browser was not given is
            # another site's, and nothing is sent for it.
            error = "This page had expired. Please try again."
        elif request.method == "POST" and not password_reset.address_may_request(
            request.remote_addr, request.headers.get("X-Forwarded-For")
        ):
            password_reset.audit_refused("page")
            error = "Too many reset requests from this address. Try again in a few minutes."
        elif request.method == "POST":
            password_reset.send_link_later(
                request.form.get("identifier", ""), mailer.config()["base_url"], via="page"
            )
            notice = (
                "If that account exists and has an email address on file, a reset "
                "link is on its way. It expires in half an hour."
            )

        return render_template_string(
            FORGOT_PAGE, notice=notice, error=error, login_form=login.login_form_token()
        )

    async def reset_password_handler(self):
        """Spend a link and set a new password.

        The token travels in the query string on the way in and in a hidden
        field on the way back, so a mistyped password does not cost the user
        their link -- only a spent token does that, and it is spent once.
        """
        from helpers import password_reset, users
        from helpers.login_page import RESET_PAGE

        def page(**kwargs):
            return render_template_string(
                RESET_PAGE,
                min_length=users.MIN_PASSWORD_LENGTH,
                done=False,
                error=None,
                notice=None,
                **kwargs,
            )

        if request.method == "GET":
            return page(token=request.args.get("token", ""))

        token = request.form.get("token", "")
        new_password = request.form.get("new_password", "")
        if new_password != request.form.get("confirm_password", ""):
            # Checked here rather than in `complete`, which has no second field
            # to compare against and would have to spend the token to find out.
            return page(token=token, error="Those two passwords are not the same.")

        ok, message = password_reset.complete(token, new_password)
        try:
            from plugins._audit.helpers import audit

            audit.emit(
                "password_reset.completed" if ok else "password_reset.rejected", via="page"
            )
        except Exception:
            pass

        if ok:
            return page(token="", done=True, notice=message)
        return page(token=token, error=message)

    async def login_handler(self):
        error = None
        # The canonical mount, not "/": "/" only redirects here anyway, and
        # sending a freshly signed-in user through an extra hop is noise.
        fallback_url = url_for("serve_spa_root")
        next_url = get_safe_next_url(
            request.form.get("next") if request.method == "POST" else request.args.get("next"),
            fallback_url,
        )

        from helpers import oidc

        # With an identity provider configured, the local form is not the way
        # in. It stays reachable only for the break-glass account, which is
        # explicitly opt-in via ?local=1 so it cannot be arrived at by accident.
        if oidc.is_configured() and request.method == "GET" and not request.args.get("local"):
            try:
                return redirect(oidc.begin_login(session, next_url))
            except oidc.OidcError as exc:
                # A misconfigured or unreachable provider must not lock everyone
                # out silently: fall through to the local form and say why.
                PrintStyle.error(f"OIDC sign-in unavailable: {exc}")
                error = str(exc)

        if request.method == "POST" and not login.login_form_token_valid(request.form.get("login_form")):
            # A form this browser was not given: another site's, or one from
            # before the session ended. Nothing is checked or counted; the
            # fresh form below carries a token that works.
            error = "Your sign-in page had expired. Please sign in again."
        elif request.method == "POST":
            from helpers import login_throttle, users

            submitted_username = str(request.form.get("username", ""))[:64]
            caller = str(request.remote_addr) if request.remote_addr else None

            # Before authenticating, so a blocked caller costs a dictionary
            # lookup rather than a password hash -- bcrypt is deliberately slow,
            # which makes an unthrottled form a way to spend the server's CPU as
            # well as a way to guess.
            wait = login_throttle.retry_after(caller, submitted_username)
            if wait:
                _audit_emit(
                    "auth.signin",
                    outcome="throttled",
                    username=submitted_username,
                )
                minutes = max(1, round(wait / 60))
                error = (
                    "Too many failed attempts. Try again in "
                    f"{minutes} minute{'s' if minutes != 1 else ''}."
                )
                return _login_page(error=error, next=next_url)

            authenticated = users.authenticate(
                request.form.get("username", ""),
                request.form.get("password", ""),
            )
            if authenticated:
                login_throttle.record_success(caller, submitted_username)
                login.login_session(authenticated)
                _audit_emit(
                    "auth.signin",
                    outcome="success",
                    username=authenticated["username"],
                    role=authenticated["role"],
                )
                return redirect(next_url or fallback_url)
            else:
                # The attempted username is recorded; the password never is, not
                # even masked, because a near-miss reveals the real one.
                _audit_emit(
                    "auth.signin",
                    outcome="failure",
                    username=submitted_username,
                )
                login_throttle.record_failure(caller, submitted_username)
                # Kept alongside the counter above. The delay is what makes a
                # single sequential guesser slow; the counter is what makes a
                # parallel one stop, and neither does the other's job.
                await asyncio.sleep(1)
                # Deliberately does not distinguish unknown user from wrong
                # password, disabled from deleted.
                error = "Invalid credentials. Please try again."

        return _login_page(error=error, next=next_url)

    @extensible
    async def oidc_callback_handler(self):
        """Complete an identity-provider sign-in.

        Everything that can fail here fails to the login page with a short
        message. The detail goes to the log: a callback error can name the
        client id, the redirect URI or which group was missing, none of which
        belongs in a browser.
        """
        from helpers import login, oidc, users

        if error := request.args.get("error"):
            _audit_emit("auth.signin", outcome="failure", provider="oidc", reason=error[:64])
            PrintStyle.error(f"OIDC provider returned an error: {error} {request.args.get('error_description', '')}")
            return _login_page(error="Sign-in was cancelled or refused.", next="")

        try:
            identity = oidc.complete_login(
                session, request.args.get("code", ""), request.args.get("state", "")
            )
            user = users.upsert_federated_user(
                subject=identity.subject,
                username=identity.username,
                email=identity.email,
                display_name=identity.display_name,
                role=identity.role,
                issuer=oidc.discovery()["issuer"],
            )
            if user.get("disabled"):
                # Locally suspended. The provider still vouches for them; this
                # deployment does not.
                raise oidc.OidcError("This account has been disabled.")
        except oidc.OidcError as exc:
            _audit_emit("auth.signin", outcome="failure", provider="oidc", reason=str(exc)[:120])
            return _login_page(error=str(exc), next="")

        login.login_session(user)
        _audit_emit(
            "auth.signin",
            outcome="success",
            provider="oidc",
            username=user["username"],
            role=user["role"],
        )

        target = get_safe_next_url(session.pop(oidc.SESSION_NEXT, ""), url_for("serve_spa_root"))
        return redirect(target or url_for("serve_spa_root"))

    @extensible
    async def logout_handler(self):
        """GET /logout no longer signs out; POST /api/logout does.

        A GET is what an image tag or a link on any other site can make the
        browser send, so a sign-out here was one anybody could trigger. The
        route stays so an old bookmark lands somewhere: the application, or
        the sign-in page it redirects to when there is no session.
        """
        return redirect(url_for("serve_spa_root"))


    @requires_auth
    async def redirect_to_app(self):
        """Send the root to the frontend's real mount point.

        One canonical location for the application. A bookmark or a link to "/"
        still works, it just arrives somewhere the router can resolve.
        """
        from helpers import spa

        return redirect(spa.MOUNT_PATH)

    async def serve_font(self, asset_path: str = ""):
        """Font files, deliberately not behind @requires_auth.

        The sign-in page is unauthenticated by definition and needs the same
        typeface as the application, or the first screen a user sees is the one
        screen off-brand. Static font files carry nothing to protect.
        """
        from helpers import spa

        return spa.serve_font(asset_path)

    @requires_auth
    async def serve_spa(self, asset_path: str = ""):
        """Serve the built frontend from /app."""
        from helpers import spa

        return spa.serve_asset(asset_path)


