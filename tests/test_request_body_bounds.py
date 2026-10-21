"""How much of a request the server will hold in memory, and for whom.

The only bound was the upload limit, five gigabytes, and it applied to every
body alike: a JSON document and a form are read whole into memory, by handlers
that a caller reaches without signing in (the sign-in form, the password reset,
the API-key check that looks for the key in the body). One request could ask the
server to hold more than the machine has.
"""

import io
import threading

import pytest
from flask import Flask, request

from helpers import cache, files, ui_server
from helpers.api import CACHE_AREA, register_api_route

MIB = 1024 * 1024


class _Counted(io.RawIOBase):
    """A request body of `size` bytes that remembers how many were asked for."""

    def __init__(self, size: int, fill: bytes = b" ") -> None:
        self.left, self.read_bytes, self.fill = size, 0, fill

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        count = min(len(buffer), self.left)
        buffer[:count] = self.fill * count
        self.left -= count
        self.read_bytes += count
        return count


KEYED = """from helpers.api import ApiHandler


class Keyed(ApiHandler):
    @classmethod
    def requires_auth(cls):
        return False

    @classmethod
    def requires_csrf(cls):
        return False

    @classmethod
    def requires_api_key(cls):
        return True

    async def process(self, input, request):
        return {"size": len(str(input))}
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_base_dir", str(tmp_path))
    (tmp_path / "usr" / "api").mkdir(parents=True)
    (tmp_path / "usr" / "api" / "keyed.py").write_text(KEYED, encoding="utf-8")
    from helpers import settings

    monkeypatch.setattr(settings, "get_settings", lambda: {"mcp_server_token": "the-key"})

    app = Flask("bounds", static_folder=None)
    app.secret_key = "test-secret"
    app.config["MAX_CONTENT_LENGTH"] = ui_server.UPLOAD_LIMIT_BYTES
    ui_server.bound_request_bodies(app)

    @app.route("/form", methods=["POST"])
    def form():
        return str(len(request.form.get("username", "")))

    cache.clear(CACHE_AREA)
    register_api_route(app, threading.RLock())
    try:
        yield app.test_client()
    finally:
        cache.clear(CACHE_AREA)


def _post(client, path, size, content_type, headers=None):
    body = _Counted(size)
    response = client.post(
        path,
        headers=headers or {},
        environ_overrides={
            "wsgi.input": io.BufferedReader(body),
            "CONTENT_LENGTH": str(size),
            "CONTENT_TYPE": content_type,
        },
    )
    return response, body.read_bytes


def test_a_caller_with_no_key_cannot_make_the_key_check_read_a_large_body(client):
    """`requires_api_key` looks for `api_key` in the JSON body when the header is
    absent, which parsed whatever was sent before anything had been proved."""
    response, read = _post(client, "/api/keyed", 8 * MIB, "application/json")
    assert response.status_code == 413
    assert read == 0


def test_the_sign_in_form_is_not_read_past_the_anonymous_bound(client):
    response, read = _post(client, "/form", 8 * MIB, "application/x-www-form-urlencoded")
    assert response.status_code == 413
    assert read == 0


def test_an_anonymous_upload_is_refused_before_it_is_spooled(client):
    response, read = _post(client, "/form", 8 * MIB, "multipart/form-data; boundary=x")
    assert response.status_code == 413
    assert read == 0


def test_a_small_anonymous_body_is_still_read(client):
    response = client.post("/form", data={"username": "ada"})
    assert (response.status_code, response.data) == (200, b"3")


def test_the_right_key_may_send_more_but_not_without_end(client):
    key = {"X-API-KEY": "the-key"}
    response, read = _post(client, "/api/keyed", 8 * MIB, "application/json", key)
    assert response.status_code == 200 and read == 8 * MIB

    too_much = ui_server.MEMORY_BODY_LIMIT_BYTES + 1
    response, read = _post(client, "/api/keyed", too_much, "application/json", key)
    assert response.status_code == 413 and read == 0


def test_a_wrong_key_buys_nothing(client):
    response, read = _post(client, "/api/keyed", 8 * MIB, "application/json", {"X-API-KEY": "guess"})
    assert response.status_code == 413
    assert read == 0


def test_a_signed_in_account_is_bounded_in_memory_and_keeps_its_uploads(client, monkeypatch):
    monkeypatch.setattr(ui_server, "MEMORY_BODY_LIMIT_BYTES", 2 * MIB)
    with client.session_transaction() as session:
        session["user_id"] = "u1"

    response, read = _post(client, "/form", 3 * MIB, "application/json")
    assert response.status_code == 413 and read == 0

    # A file goes to disk, not to memory, and the upload limit is its bound.
    upload = {"username": "ada", "file": (io.BytesIO(b"x" * (3 * MIB)), "big.bin")}
    response = client.post("/form", data=upload, content_type="multipart/form-data")
    assert (response.status_code, response.data) == (200, b"3")


def test_the_text_fields_of_an_upload_are_bounded_too(client):
    """Werkzeug keeps a form's non-file fields in memory up to this."""
    from werkzeug.wrappers import Request

    assert Request.max_form_memory_size == ui_server.MEMORY_BODY_LIMIT_BYTES
    assert ui_server.ANONYMOUS_BODY_LIMIT_BYTES < ui_server.MEMORY_BODY_LIMIT_BYTES < ui_server.UPLOAD_LIMIT_BYTES


def test_no_page_of_the_product_can_be_put_inside_another_sites_frame():
    """Nothing told a browser not to draw the interface inside a frame, so a page on any other site of the same
    organisation could show it under its own picture and have a signed-in engineer press Approve on a change.
    The interface frames nothing of its own, so the answer is the same for every response."""
    app = Flask("frames", static_folder=None)
    ui_server.refuse_to_be_framed(app)

    @app.route("/app")
    def shell():
        return "<!doctype html>"

    @app.route("/svg")
    def own_policy():
        return "", 200, {"Content-Security-Policy": "default-src 'none'"}

    page = app.test_client().get("/app")
    assert page.headers["X-Frame-Options"] == "DENY"
    assert page.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    stricter = app.test_client().get("/svg")
    assert stricter.headers["Content-Security-Policy"] == "default-src 'none'" and stricter.headers["X-Frame-Options"] == "DENY"
    assert "refuse_to_be_framed(webapp)" in open(ui_server.__file__, encoding="utf-8").read()
