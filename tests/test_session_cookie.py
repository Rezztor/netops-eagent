"""The session cookie is `Secure` whenever the browser is on HTTPS.

TLS ends at a proxy in front of the application, so every request the
application sees is plain HTTP. On the first appliance served over HTTPS the
session cookie arrived without `Secure`. The flag cannot simply be on either:
local development signs in over plain HTTP.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from flask import Flask, session
from flask.sessions import SecureCookieSessionInterface

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from helpers.ui_server import HttpsSessionInterface  # noqa: E402

SERVER = REPO / "helpers" / "ui_server.py"


def _app(forced: bool = False) -> Flask:
    app = Flask("test_session_cookie")
    app.secret_key = "not-a-secret"
    app.config.update(SESSION_COOKIE_NAME="session_test", SESSION_COOKIE_SECURE=forced)
    app.session_interface = HttpsSessionInterface()

    @app.post("/login")
    def sign_in():
        session["authentication"] = "yes"
        return "ok"

    return app


def _cookie(app: Flask, **request) -> str:
    [cookie] = app.test_client().post("/login", **request).headers.getlist("Set-Cookie")
    return cookie


def _secure(cookie: str) -> bool:
    return "secure" in (part.strip().lower() for part in cookie.split(";"))


def test_plain_http_still_signs_in_for_local_development():
    assert not _secure(_cookie(_app()))


@pytest.mark.parametrize("request_kwargs", [
    {"base_url": "https://eagent.example"},
    {"headers": {"X-Forwarded-Proto": "https"}},
    # A chain of proxies appends; the first value is what the browser used.
    {"headers": {"X-Forwarded-Proto": "HTTPS, http"}},
])
def test_https_in_front_of_the_application_makes_the_cookie_secure(request_kwargs):
    assert _secure(_cookie(_app(), **request_kwargs))


def test_a_proxy_that_says_plain_http_is_believed():
    assert not _secure(_cookie(_app(), headers={"X-Forwarded-Proto": "http"}))


def test_the_flag_can_be_forced_for_a_proxy_that_sends_no_header():
    assert _secure(_cookie(_app(forced=True)))


def test_the_session_cookie_uses_standard_flask_signing():
    """HttpsSessionInterface must sign exactly as Flask's plain
    SecureCookieSessionInterface does, so any code that decodes with the stock
    serializer still accepts cookies this app set."""
    app = _app()
    value = _cookie(app, base_url="https://eagent.example").split(";")[0].split("=", 1)[1]
    serializer = SecureCookieSessionInterface().get_signing_serializer(app)
    assert serializer.loads(value)["authentication"] == "yes"


def test_the_application_is_wired_to_it():
    source = SERVER.read_text(encoding="utf-8")
    create = source[source.index("    def create(cls)"):source.index("    def refresh_runtime_settings")]
    assert "webapp.session_interface = HttpsSessionInterface()" in create
    assert re.search(r'SESSION_COOKIE_SECURE=os\.getenv\("SESSION_COOKIE_SECURE"', create), (
        "a proxy that sends no X-Forwarded-Proto has no way to force the flag"
    )
