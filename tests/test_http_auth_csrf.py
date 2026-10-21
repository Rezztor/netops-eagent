from __future__ import annotations

from flask import Flask, Response

import pytest

from helpers import runtime


def _make_app() -> Flask:
    app = Flask("test_http_auth_csrf")
    app.secret_key = "test-secret"

    @app.get("/login")
    def login_handler():
        return Response("login", status=200)

    # requires_auth builds its redirect fallback with url_for("serve_index"),
    # so the endpoint must exist or the redirect raises BuildError and the
    # handler 500s instead of redirecting.
    @app.get("/")
    def serve_index():
        return Response("index", status=200)

    return app


def _set_session(client, **values) -> None:
    with client.session_transaction() as sess:
        for key, value in values.items():
            sess[key] = value


def _set_csrf_cookie(client, token: str) -> None:
    cookie_name = f"csrf_token_{runtime.get_runtime_id()}"
    client.set_cookie(cookie_name, token)


def test_http_auth_enforced_for_anonymous_request(monkeypatch) -> None:
    from run_ui import csrf_protect, requires_auth

    monkeypatch.setattr("helpers.login.is_authenticated", lambda: False)

    app = _make_app()

    @app.get("/secure")
    @requires_auth
    @csrf_protect
    async def secure():
        return Response("ok", status=200)

    client = app.test_client()
    response = client.get("/secure")
    assert response.status_code == 302


def test_http_csrf_required_for_authenticated_session(monkeypatch) -> None:
    from run_ui import csrf_protect, requires_auth

    monkeypatch.setattr("helpers.login.is_authenticated", lambda: True)

    app = _make_app()

    @app.get("/secure")
    @requires_auth
    @csrf_protect
    async def secure():
        return Response("ok", status=200)

    client = app.test_client()
    _set_session(client, csrf_token="csrf-1")
    response = client.get("/secure")
    assert response.status_code == 403


def test_http_csrf_rejects_missing_token(monkeypatch) -> None:
    from run_ui import csrf_protect, requires_auth

    monkeypatch.setattr("helpers.login.is_authenticated", lambda: True)

    app = _make_app()

    @app.get("/secure")
    @requires_auth
    @csrf_protect
    async def secure():
        return Response("ok", status=200)

    client = app.test_client()
    _set_session(client, authentication="hash", csrf_token="csrf-2")
    response = client.get("/secure")
    assert response.status_code == 403


def test_http_csrf_accepts_valid_header_without_cookie(monkeypatch) -> None:
    from run_ui import csrf_protect, requires_auth

    monkeypatch.setattr("helpers.login.is_authenticated", lambda: True)

    app = _make_app()

    @app.get("/secure")
    @requires_auth
    @csrf_protect
    async def secure():
        return Response("ok", status=200)

    client = app.test_client()
    _set_session(client, authentication="hash", csrf_token="csrf-3")
    response = client.get("/secure", headers={"X-CSRF-Token": "csrf-3"})
    assert response.status_code == 200


def test_http_csrf_rejects_the_cookie_alone(monkeypatch) -> None:
    """A cookie rides along on a request the page never wrote; it is not proof."""
    from run_ui import csrf_protect, requires_auth

    monkeypatch.setattr("helpers.login.is_authenticated", lambda: True)

    app = _make_app()

    @app.post("/secure")
    @requires_auth
    @csrf_protect
    async def secure():
        return Response("ok", status=200)

    client = app.test_client()
    _set_session(client, authentication="hash", csrf_token="csrf-4")
    _set_csrf_cookie(client, "csrf-4")
    response = client.post("/secure")
    assert response.status_code == 403
    assert client.post("/secure", headers={"X-CSRF-Token": "csrf-4"}).status_code == 200


def test_get_logout_does_not_sign_out_and_the_api_sign_out_needs_the_token() -> None:
    """A GET is what any other site can make the browser send."""
    from api.logout import ApiLogout
    from helpers.ui_server import UiRouteHandlers

    assert ApiLogout.requires_csrf() and ApiLogout.get_methods() == ["POST"]

    app = _make_app()
    app.add_url_rule("/app", "serve_spa_root", lambda: "")
    handlers = UiRouteHandlers.__new__(UiRouteHandlers)
    app.add_url_rule("/logout", "logout_handler", handlers.logout_handler)
    client = app.test_client()
    _set_session(client, user_id="u1")

    assert client.get("/logout").status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("user_id") == "u1"


def test_safe_next_url_accepts_plugin_page_path() -> None:
    from helpers.api import get_safe_next_url, is_safe_next_url

    target = "/plugins/ea_voqualizer/webui/voqualizer.html"
    assert is_safe_next_url(target)
    assert get_safe_next_url(target, "/") == target


def test_safe_next_url_preserves_query_string() -> None:
    from helpers.api import get_safe_next_url

    target = "/plugins/ea_voqualizer/webui/voqualizer.html?context=rlO1iMV7"
    assert get_safe_next_url(target, "/") == target


def test_safe_next_url_rejects_external_and_protocol_relative_urls() -> None:
    from helpers.api import get_safe_next_url, is_safe_next_url

    fallback = "/"
    for value in [
        "https://evil.example/plugins/ea_voqualizer/webui/voqualizer.html",
        "//evil.example/plugins/ea_voqualizer/webui/voqualizer.html",
        "javascript:alert(1)",
        "/safe\nLocation: https://evil.example",
    ]:
        assert not is_safe_next_url(value)
        assert get_safe_next_url(value, fallback) == fallback


def test_auth_redirect_includes_original_path_and_query(monkeypatch) -> None:
    from run_ui import requires_auth

    monkeypatch.setattr("helpers.login.is_authenticated", lambda: False)

    app = _make_app()

    @app.get("/plugins/ea_voqualizer/webui/voqualizer.html")
    @requires_auth
    async def voqualizer_page():
        return Response("ok", status=200)

    client = app.test_client()
    response = client.get("/plugins/ea_voqualizer/webui/voqualizer.html?context=rlO1iMV7")
    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("/login?next=")

    # Assert the contract -- next round-trips to the original path and query --
    # rather than one exact percent-encoding. Slashes are legal unencoded in a
    # query value, so the literal form varies with the URL-building library.
    from urllib.parse import parse_qs, urlsplit

    next_value = parse_qs(urlsplit(location).query)["next"][0]
    assert next_value == "/plugins/ea_voqualizer/webui/voqualizer.html?context=rlO1iMV7"


def test_is_safe_next_url_rejects_backslash_open_redirects() -> None:
    from helpers.api import is_safe_next_url

    # Raw backslash forms
    assert is_safe_next_url("/\\evil.example") is False
    assert is_safe_next_url("\\/evil.example") is False
    assert is_safe_next_url("/path\\evil") is False

    # Percent-encoded backslash forms
    assert is_safe_next_url("/%5Cevil.example") is False
    assert is_safe_next_url("%5C/evil.example") is False
    assert is_safe_next_url("/%5cevil.example") is False  # lowercase hex

    # Mixed / double-encoded edge
    assert is_safe_next_url("/path/%5Cevil") is False

    # Sanity: a legitimate relative path still passes
    assert is_safe_next_url("/plugins/ea_voqualizer/webui/voqualizer.html") is True
