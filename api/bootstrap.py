"""Everything a freshly loaded frontend needs, in one request.

Today `webui/index.html` is not a static file: `UiRouteHandlers.serve_index`
renders it, substituted server-side values into it -- version, runtime id, development
flag, whether the caller is signed in, and the user's display preferences. A
frontend built separately and served as a static bundle cannot receive any of
that, because nothing renders it.

That interface has since been removed, so this is no longer a bridge between two
of them: it is simply how the application starts.

This endpoint is the replacement. It returns the same values over the API, plus
the identity and permission data the frontend needs to decide what to render at
all, so a cold start is one request rather than a template substitution followed
by several.

It is deliberately callable while signed out, because the first question a cold
frontend has to ask is whether it should show the application or the login page,
and it cannot ask that from behind an authentication wall. An anonymous caller
gets only `authenticated: false` and the version -- the same version already
public on `/api/health` -- and nothing about the instance's configuration or
users. Everything else appears only once there is a session.

It is also exempt from the forced-password-change gate. A user who must change
their password still has to load the interface far enough to be shown the
change-password screen.
"""

from typing import Any

from pydantic import BaseModel, Field

from helpers import login, rbac, runtime, settings as settings_helper
from helpers.api import ApiHandler, Input, Output, Request
from helpers.print_style import PrintStyle


class BootstrapVersion(BaseModel):
    version: str = Field(description="Release string, e.g. 'v2.10+31'.")
    commit_time: str = Field(description="Commit timestamp, or empty if unavailable.")


class BootstrapUser(BaseModel):
    id: str
    username: str
    display_name: str
    email: str
    role: str = Field(description="One of: admin, power, user.")
    role_title: str = Field(description="Human-readable role name.")
    must_change_password: bool = Field(
        description="When true the frontend must show the change-password screen "
        "before anything else; every other API endpoint will refuse until it is done."
    )


class BootstrapRuntime(BaseModel):
    runtime_id: str = Field(
        description="Also names the CSRF cookie (csrf_token_<runtime_id>), so a "
        "client cannot complete an authenticated request without it."
    )
    is_development: bool
    is_dockerized: bool


class BootstrapPreferences(BaseModel):
    timezone: str
    time_format: str = Field(description="'12h' or '24h'.")
    ui_control_visibility: dict[str, Any] = Field(
        description="Per-control mobile/desktop visibility flags."
    )


class BootstrapLicence(BaseModel):
    """The licence verdict every signed-in user can see.

    The full entitlements payload (seats, customer, features) stays behind
    `settings.manage` on /api/license_status; this summary carries only what
    the app-wide banner and the agent-run gate need, so the rest of the
    interface can explain itself without leaking commercial terms to every
    account. Bootstrap is the one endpoint guaranteed reachable in a
    restricted state, which is exactly when the banner matters.
    """

    state: str = Field(description="One of: community, licensed, grace1, grace2, restricted.")
    agent_runs_allowed: bool = Field(description="False when state is 'restricted'.")
    grace_days_remaining: int | None = Field(
        default=None,
        description="Days until the installation is restricted, in grace1 and grace2; "
        "null in every other state.",
    )
    reason: str | None = Field(
        default=None,
        description="One plain sentence when there is something to say: a grace, a "
        "revocation notice, or why the installation is restricted.",
    )


class BootstrapUpdate(BaseModel):
    """An update of the appliance that is not finished (ADR 0080): checked, being installed, or waiting
    for a restart. The app-wide banner needs only this; the rest is on /api/update_status."""

    phase: str = Field(description="One of: checked, installing, staged, restarting.")
    version: str = Field(description="The version the update brings.")


class BootstrapResponse(BaseModel):
    """Startup payload. Fields after `version` are present only when authenticated."""

    ok: bool
    authenticated: bool
    version: BootstrapVersion
    user: BootstrapUser | None = None
    permissions: list[str] | None = Field(
        default=None, description="Permissions granted by the user's role."
    )
    runtime: BootstrapRuntime | None = None
    csrf_token: str | None = Field(
        default=None,
        description="Same token GET /api/csrf_token returns. Included so a cold "
        "start does not need a second request before it can call anything.",
    )
    preferences: BootstrapPreferences | None = None
    licence: BootstrapLicence | None = Field(
        default=None,
        description="Licence summary for the app-wide banner. Present only when "
        "authenticated; null if the verdict could not be computed.",
    )
    update: BootstrapUpdate | None = Field(
        default=None,
        description="Present only for holders of settings.manage; null when this "
        "installation has no update service or no update waits.",
    )
    updates_available: int | None = Field(
        default=None,
        description="How many packages Rezztor's update service listed after the running "
        "version when the host last asked (ADR 0091). Present only for holders of "
        "settings.manage; null when there is no update service or no list yet.",
    )


class Bootstrap(ApiHandler):
    """Startup payload for a cold frontend: session state, identity, preferences."""

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["GET"]

    @classmethod
    def output_model(cls) -> type | None:
        return BootstrapResponse

    @classmethod
    def requires_auth(cls) -> bool:
        # See the module docstring: the frontend must be able to ask whether it
        # has a session before it can know to render the login page. The payload
        # is scoped to the caller instead.
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    async def process(self, input: Input, request: Request) -> Output:
        payload: dict[str, Any] = {
            "ok": True,
            "authenticated": False,
            "version": _version(),
        }

        user = login.current_user()
        if user is None:
            return payload

        role = rbac.normalize_role(user.get("role"))
        payload["authenticated"] = True
        payload["user"] = {
            "id": user["id"],
            "username": user["username"],
            "display_name": user.get("display_name") or user["username"],
            "email": user.get("email") or "",
            "role": role,
            "role_title": rbac.ROLES[role]["title"],
            "must_change_password": bool(user.get("must_change_password")),
        }
        payload["permissions"] = sorted(rbac.get_permissions(role))
        payload["runtime"] = {
            "runtime_id": runtime.get_runtime_id(),
            "is_development": runtime.is_development(),
            "is_dockerized": runtime.is_dockerized(),
        }
        payload["csrf_token"] = _csrf_token()
        payload["preferences"] = _preferences()
        payload["licence"] = _licence()
        if rbac.PERM_SETTINGS_MANAGE in payload["permissions"]:
            payload["update"] = _update()
            payload["updates_available"] = _updates_available()
        return payload


def _version() -> dict[str, str]:
    try:
        from helpers import git

        info = git.get_git_info()
        return {
            "version": str(info.get("version") or ""),
            "commit_time": str(info.get("commit_time") or ""),
        }
    except Exception as exc:  # noqa: BLE001 - never fail a cold start on version info
        PrintStyle.warning(f"bootstrap: version unavailable: {exc}")
        return {"version": "", "commit_time": ""}


def _csrf_token() -> str:
    """Issue or reuse the session's CSRF token.

    Identical to what GET /api/csrf_token returns, and issued the same way, so
    the two cannot diverge. Returning it here removes the extra round trip a cold
    frontend would otherwise need before its first authenticated call -- and
    /api/csrf_token itself requires a session, so an anonymous caller could not
    obtain one anyway. Only reached once the caller is authenticated.
    """
    import secrets

    from flask import session

    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return str(session["csrf_token"])


def _update() -> dict[str, str] | None:
    """An update that waits for the administrator (ADR 0080), or None. One small file read, no network."""
    try:
        from helpers import update_spool

        return update_spool.summary()
    except Exception as exc:  # noqa: BLE001 - never fail a cold start on this
        PrintStyle.warning(f"bootstrap: update state unavailable: {exc}")
        return None


def _updates_available() -> int | None:
    """How many packages wait, from the same state file; never fails a cold start."""
    from helpers import update_spool

    return update_spool.waiting_count()


def _licence() -> dict[str, Any] | None:
    """The minimal licence verdict, or None if even that fails.

    Bootstrap must not fail on a licence problem — the restricted state is
    precisely when the frontend needs to load and explain itself — so any
    error here yields null, and the banner simply does not appear. The
    check is local: one row and a few Ed25519 verifications, no network.
    """
    try:
        from helpers.license_entitlements import licence_summary

        return licence_summary()
    except Exception as exc:  # noqa: BLE001 - never fail a cold start on this
        PrintStyle.warning(f"bootstrap: licence unavailable: {exc}")
        return None


def _preferences() -> dict[str, Any]:
    """The per-user display settings index.html currently substitutes inline.

    Each falls back independently: a broken settings file should degrade one
    preference, not blank the whole startup payload.
    """
    prefs: dict[str, Any] = {
        "timezone": "UTC",
        "time_format": "12h",
        "ui_control_visibility": settings_helper.UI_CONTROL_VISIBILITY_DEFAULTS,
    }
    try:
        from helpers.localization import Localization

        prefs["timezone"] = Localization.get().get_timezone()
    except Exception:  # noqa: BLE001
        pass
    try:
        current = settings_helper.get_settings()
    except Exception as exc:  # noqa: BLE001
        PrintStyle.warning(f"bootstrap: settings unavailable: {exc}")
        return prefs
    prefs["time_format"] = str(current.get("time_format", "12h"))
    visibility = current.get("ui_control_visibility")
    if isinstance(visibility, dict):
        prefs["ui_control_visibility"] = visibility
    return prefs
