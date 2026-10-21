"""The local sign-in form only signs in a browser it was given to.

`POST /login` sits outside the API dispatcher, so `csrf_protect` never sees it.
A page on any site could submit it with its own account's password and sign the
visitor in as that account (login CSRF).
"""

import re
from types import SimpleNamespace

from flask import Flask
import pytest

from helpers import login, login_throttle, oidc, users
from helpers.ui_server import UiRouteHandlers


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(oidc, "is_configured", lambda: False)
    signed_in = []
    monkeypatch.setattr(
        users, "authenticate",
        lambda username, password: {"id": "u1", "username": username, "role": "user"}
        if password == "right" else None,
    )
    monkeypatch.setattr(users, "get_user_by_id", lambda uid: {"id": uid, "password_hash": "h"})
    real_login_session = login.login_session

    def login_session(user):
        signed_in.append(user)
        real_login_session(user)

    monkeypatch.setattr(login, "login_session", login_session)
    login_throttle.reset()

    app = Flask("test_login_form_csrf")
    app.secret_key = "test-secret"
    handlers = UiRouteHandlers(SimpleNamespace())
    app.add_url_rule("/login", "login_handler", handlers.login_handler, methods=["GET", "POST"])
    app.add_url_rule("/app/", "serve_spa_root", lambda: "app")
    test_client = app.test_client()
    test_client.signed_in = signed_in
    return test_client


def form_token(client) -> str:
    page = client.get("/login").get_data(as_text=True)
    return re.search(r'name="login_form" value="([^"]+)"', page).group(1)


def test_a_post_without_the_forms_token_signs_nobody_in(client) -> None:
    response = client.post("/login", data={"username": "attacker", "password": "right"})

    assert response.status_code == 200
    assert "had expired" in response.get_data(as_text=True)
    assert client.signed_in == []
    # The page that answered carries a token that works.
    assert 'name="login_form" value="' in response.get_data(as_text=True)


def test_a_token_from_another_browser_is_refused(client) -> None:
    other = client.application.test_client()
    stolen = form_token(other)
    form_token(client)

    client.post("/login", data={"username": "attacker", "password": "right", "login_form": stolen})

    assert client.signed_in == []


def test_the_form_this_browser_was_given_signs_in(client) -> None:
    token = form_token(client)

    response = client.post("/login", data={"username": "alice", "password": "right", "login_form": token})

    assert response.status_code == 302
    assert [user["username"] for user in client.signed_in] == ["alice"]
    with client.session_transaction() as sess:
        assert login.SESSION_LOGIN_FORM_TOKEN not in sess
