"""Serving the built frontend from the backend (stage 6).

The cutover is one environment variable. What these pin is that it cannot fail
in the two ways that matter: taking the root down when the build is absent, and
serving the application shell to something that asked for a script.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import spa  # noqa: E402


def _executable_lines(path: Path) -> str:
    """Source with comments and docstrings stripped.

    These assertions are about what the code does, and prose explaining *why*
    something was removed necessarily names the thing removed. Checking the raw
    text makes an accurate comment fail the test.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return ast.unparse(tree)


@pytest.fixture
def request_context():
    """send_file needs one; these are unit tests, not route tests."""
    app = Flask("spa-test")
    with app.test_request_context("/app"):
        yield


@pytest.fixture
def built(tmp_path, monkeypatch):
    """A minimal build tree, so these do not depend on npm having run."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>shell</title>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(spa, "dist_path", lambda: dist)
    return dist


def test_unknown_paths_return_the_shell_so_client_routing_works(built):
    # /app/users is a route the router knows and the server does not.
    response = spa.serve_asset("users")
    assert response.status_code == 200
    assert "shell" in response.get_data(as_text=True)


def test_a_missing_asset_404s_rather_than_returning_html(built):
    """Returning the shell for a missing script is the worst failure mode here:
    the browser parses HTML as JavaScript and the error names neither."""
    response = spa.serve_asset("assets/index-does-not-exist.js")
    assert response.status_code == 404


def test_real_assets_are_served(built, request_context):
    response = spa.serve_asset("assets/index-abc123.js")
    assert response.status_code == 200


def test_hashed_assets_are_cached_hard_and_the_shell_is_not(built, request_context):
    """index.html must never be cached, or a deploy leaves browsers on the
    previous bundle indefinitely. Hashed assets are content-addressed and safe."""
    asset = spa.serve_asset("assets/index-abc123.js")
    assert "immutable" in asset.headers["Cache-Control"]

    shell = spa.serve_asset("users")
    assert shell.headers["Cache-Control"] == "no-store"


def test_path_traversal_out_of_the_build_directory_is_refused(built, tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text("API_KEY=leaked", encoding="utf-8")

    for attempt in ("../secret.env", "../../secret.env", "assets/../../secret.env"):
        response = spa.serve_asset(attempt)
        body = response.get_data(as_text=True)
        assert "leaked" not in body, attempt


def test_serving_nothing_is_reported_when_there_is_no_build(tmp_path, monkeypatch):
    monkeypatch.setattr(spa, "dist_path", lambda: tmp_path / "absent")
    response = spa.serve_asset("")
    assert response.status_code == 503
    assert "npm run build" in response.get_data(as_text=True)


def test_the_image_builds_the_frontend():
    """The default is only real if the image contains frontend/dist, which is
    gitignored. Without a build step the container starts, warns, and serves the
    inherited interface -- a deployment that looks like it worked."""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "build_frontend.sh" in dockerfile
    script = PROJECT_ROOT / "docker" / "run" / "fs" / "ins" / "build_frontend.sh"
    assert script.is_file()
    body = script.read_text(encoding="utf-8")
    assert "npm ci" in body, "use the lockfile; a build that resolves differently is not reproducible"
    assert "npm run build" in body


@pytest.mark.parametrize("handler", ["redirect_to_app", "serve_spa"])
def test_every_frontend_entry_point_requires_authentication(handler):
    """Both routes into the application must be gated, not just the last one.

    Checked per handler rather than by scanning backwards from one of them:
    inserting a new handler above an existing one silently adopts its decorator
    and leaves the original bare, which is a hole that reads as a diff moving one
    blank line. That happened while adding the root redirect, and this is the
    shape of assertion that catches it.
    """
    source = (PROJECT_ROOT / "helpers" / "ui_server.py").read_text(encoding="utf-8")
    index = source.index(f"async def {handler}(")
    preceding = source[max(0, index - 120) : index]
    assert "@requires_auth" in preceding, f"{handler} is not behind @requires_auth"


def test_the_frontend_is_the_only_interface():
    """The REZZTOR_UI flag chose between two interfaces. The inherited one was
    removed at stage 7, and a switch with one position is only a way to break
    the product."""
    source = _executable_lines(PROJECT_ROOT / "helpers" / "spa.py")
    assert "REZZTOR_UI" not in source
    assert "selected_ui" not in source

    server = (PROJECT_ROOT / "helpers" / "ui_server.py").read_text(encoding="utf-8")
    assert '"/legacy"' not in server, "the inherited interface still has a route"
    # Executable lines only: a comment explaining why the directory is gone is
    # not a reference to it.
    code = _executable_lines(PROJECT_ROOT / "helpers" / "ui_server.py")
    assert "webui/" not in code, "ui_server still reads from the removed directory"


def test_the_root_redirects_to_the_mount_instead_of_serving_a_second_copy():
    """Serving the shell at "/" produces a blank page, not a working app.

    The router is mounted at /app. Handed a location outside its basename it
    matches no route and renders nothing -- the bundle loaded, the CSS applied,
    and the page is empty, which reads as a broken build rather than a routing
    mismatch. The root therefore redirects rather than serving its own copy.
    """
    server = (PROJECT_ROOT / "helpers" / "ui_server.py").read_text(encoding="utf-8")
    for rule in ('"/"', '"/app"', '"/app/<path:asset_path>"'):
        assert rule in server, rule
    assert "handlers.redirect_to_app" in server, "the root no longer redirects"
    # Only /app and /app/<path> serve the shell.
    assert server.count("handlers.serve_spa") == 2


def test_the_mount_point_agrees_across_all_three_places_that_declare_it():
    """MOUNT_PATH, vite's base and the router basename must be the same string.

    Nothing links these three files. Change one and the application still builds,
    still serves, and still returns 200 -- it just renders an empty page, because
    the router is looking for a prefix the server never sends it. This is the
    check that turns that into a test failure instead of a support ticket.
    """
    import re

    from helpers import spa

    vite = (PROJECT_ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    main = (PROJECT_ROOT / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")

    base = re.search(r'base:\s*"([^"]+)"', vite)
    basename = re.search(r'basename="([^"]+)"', main)
    assert base and basename, "could not find vite base or router basename"

    # vite's base carries a trailing slash by convention; the other two do not.
    assert base.group(1).rstrip("/") == spa.MOUNT_PATH, (
        f"vite base {base.group(1)!r} does not match MOUNT_PATH {spa.MOUNT_PATH!r}"
    )
    assert basename.group(1).rstrip("/") == spa.MOUNT_PATH, (
        f"router basename {basename.group(1)!r} does not match "
        f"MOUNT_PATH {spa.MOUNT_PATH!r}"
    )


def test_a_missing_build_is_now_a_plain_failure():
    """There is no second interface to degrade to, so the message has to say
    what to do rather than quietly switching."""
    source = (PROJECT_ROOT / "helpers" / "spa.py").read_text(encoding="utf-8")
    assert "npm run build" in source
    assert "rebuild the container image" in source


def test_fonts_are_served_without_a_session():
    """The sign-in page needs the brand face before a session exists.

    /app is behind @requires_auth, so the font route has to be reachable without
    one. It sits under /app anyway -- vite rewrites url() against `base`, so the
    bundle asks for /app/fonts/... regardless, and a second path outside the
    mount would mean the same file cached twice by anyone who signs in.

    That only works because Werkzeug ranks a rule with more static segments above
    one ending in a converter, so /app/fonts/<path> wins over /app/<path>. Route
    precedence is not obvious by reading, and the whole arrangement rests on it,
    so it is asserted against a real URL map rather than assumed.
    """
    from helpers import spa

    app = Flask(__name__, static_folder=None)
    app.add_url_rule("/app/<path:asset_path>", "serve_spa_asset", lambda **k: "")
    app.add_url_rule(f"{spa.FONTS_PATH}/<path:asset_path>", "serve_font", lambda **k: "")

    adapter = app.url_map.bind("localhost")
    endpoint, _ = adapter.match("/app/fonts/plus-jakarta-sans-latin.woff2")
    assert endpoint == "serve_font", (
        "the gated /app rule is shadowing the font route; every sign-in would "
        "fall back to a system typeface"
    )
    # And an ordinary asset still reaches the gated handler.
    endpoint, _ = adapter.match("/app/assets/index-abc123.css")
    assert endpoint == "serve_spa_asset"


def test_the_font_route_serves_nothing_but_fonts():
    """It is unauthenticated, so its reach is limited by extension, not by trust
    in the path. Anything else under the build must 404 rather than be handed
    out to an anonymous caller."""
    source = (PROJECT_ROOT / "helpers" / "spa.py").read_text(encoding="utf-8")
    assert "_FONT_SUFFIXES" in source
    assert ".woff2" in source

    from helpers import spa

    for path in ("../assets/index.js", "..%2f..%2fusr%2f.env", "index.html", "OFL.txt"):
        response = spa.serve_font(path)
        assert response.status_code == 404, f"{path} was served by the font route"


def test_no_shipped_page_fetches_a_font_from_a_third_party():
    """Verification test 3 runs with egress blocked to everything but the LLM
    provider and the IdP. A stylesheet from a font CDN is a failed outbound
    request in the logs, and a page that silently loses its typeface."""
    for path in (
        PROJECT_ROOT / "frontend" / "index.html",
        PROJECT_ROOT / "frontend" / "src" / "styles" / "fonts.css",
        PROJECT_ROOT / "helpers" / "login_page.py",
    ):
        body = path.read_text(encoding="utf-8")
        for host in ("fonts.googleapis.com", "fonts.gstatic.com", "use.typekit", "cdn.jsdelivr"):
            assert host not in body, f"{path.name} still fetches a font from {host}"


def test_the_bundled_fonts_ship_with_their_licence():
    """Both families are SIL OFL 1.1, whose single obligation is that the notice
    travels with the files. Shipping the woff2 without it is the one licensing
    mistake this directory can make."""
    fonts = PROJECT_ROOT / "frontend" / "public" / "fonts"
    faces = sorted(p.name for p in fonts.glob("*.woff2"))
    assert faces, "no fonts are bundled"

    notice = (fonts / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in notice
    for face in faces:
        assert face in notice, f"{face} is not covered by the bundled notice"


@pytest.mark.parametrize("path", ["/app", "/app/", "/app/users", "/app/chat"])
def test_every_form_of_the_mount_url_reaches_the_shell(path):
    """Including the trailing slash, which no converter rule covers.

    `/app/<path:asset_path>` cannot match `/app/` because the path converter
    requires at least one character, so the trailing-slash form needs
    strict_slashes=False on the bare rule. Without it, `/app/` is a 404 sitting
    in the middle of the happy path -- it is where the sign-in redirect lands.
    """
    app = Flask(__name__, static_folder=None)
    app.add_url_rule("/app", "serve_spa_root", lambda **k: "", strict_slashes=False)
    app.add_url_rule("/app/<path:asset_path>", "serve_spa_asset", lambda **k: "")

    adapter = app.url_map.bind("localhost")
    endpoint, _ = adapter.match(path)
    assert endpoint in {"serve_spa_root", "serve_spa_asset"}


def test_the_signin_redirect_target_is_a_url_the_server_answers():
    """The redirect after sign-in and the rule that serves it must agree.

    They are declared in different places, and a mismatch is invisible until a
    real user signs in and lands on a 404 -- which is not something the unit
    tests around either half would notice on their own.
    """
    source = (PROJECT_ROOT / "helpers" / "ui_server.py").read_text(encoding="utf-8")
    assert 'fallback_url = url_for("serve_spa_root")' in source, (
        "sign-in no longer redirects to the mount's own endpoint"
    )
    assert "strict_slashes=False" in source, (
        "without this, the trailing-slash form of the mount is a 404"
    )


def test_the_sign_in_page_and_the_application_carry_their_tab_icon_inline():
    """A browser asks for /favicon.ico when a page names no icon. Nobody serves that file, so every customer's
    browser logged a 404 on the sign-in page (seen in the test lab's browser walk, 2026-10-11)."""
    from pathlib import Path

    from helpers import login_page

    root = Path(__file__).resolve().parents[1]
    assert '<link rel="icon" href="data:image/svg+xml,' in login_page._HEAD_REST
    assert '<link rel="icon" href="data:image/svg+xml,' in (root / "frontend" / "index.html").read_text()


def test_a_compressed_file_is_sent_with_a_length_that_is_true(tmp_path):
    """Found on the lab appliance, 2026-10-11: with `a2wsgi` gone from the requirements (nothing imports it by
    name) uvicorn used its own older bridge, compression kept the uncompressed Content-Length, and the browser got
    the first ten bytes of every font, script and stylesheet. No test saw it: a real server holds a response to
    its declared length and a test client does not, so the length is checked here."""
    import asyncio
    import gzip

    from flask import Flask, send_from_directory
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from helpers import ui_server

    assert ui_server.WSGIMiddleware.__module__.startswith("a2wsgi")

    body = b"body { color: red }\n" * 4000
    (tmp_path / "a.css").write_bytes(body)
    web = Flask(__name__)
    web.add_url_rule("/<path:name>", view_func=lambda name: send_from_directory(str(tmp_path), name))
    # The same three layers, in the same order, as ServerRuntime.build_asgi_app.
    app = ui_server.GZipMiddleware(
        Starlette(routes=[Mount("/", app=ui_server.WSGIMiddleware(web))]),
        minimum_size=ui_server.GZIP_MINIMUM_RESPONSE_BYTES, compresslevel=ui_server.GZIP_COMPRESSION_LEVEL,
    )
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/a.css", "raw_path": b"/a.css", "root_path": "", "query_string": b"",
        "headers": [(b"host", b"appliance"), (b"accept-encoding", b"gzip")],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 80),
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    headers = {name.decode().lower(): value.decode() for name, value in sent[0]["headers"]}
    data = b"".join(message.get("body", b"") for message in sent[1:])
    assert sent[0]["status"] == 200 and headers["content-encoding"] == "gzip"
    assert "content-length" not in headers or int(headers["content-length"]) == len(data)
    assert gzip.decompress(data) == body


def test_the_shell_runs_only_its_own_scripts(built):
    """No inline or third-party script may run in the shell, and every response
    says nosniff and keeps its URL out of other sites' referrer logs."""
    from helpers import ui_server

    policy = spa.serve_asset("users").headers["Content-Security-Policy"]
    for directive in ("script-src 'self'", "object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'",
                      "img-src 'self' data: blob:"):
        assert directive in policy
    assert "'unsafe-eval'" not in policy and "script-src 'self' 'unsafe-inline'" not in policy

    app = Flask("headers", static_folder=None)
    ui_server.refuse_to_be_framed(app)
    app.add_url_rule("/app", "shell", lambda: spa.serve_asset("users"))
    response = app.test_client().get("/app")
    assert response.headers["Content-Security-Policy"] == policy  # the shell's own, not replaced
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_the_built_shell_has_no_inline_script():
    """The policy forbids inline script, so the shell must not need one."""
    import re

    index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if not index.is_file():
        index = PROJECT_ROOT / "frontend" / "index.html"
    for tag in re.findall(r"<script\b[^>]*>(.*?)</script>", index.read_text(encoding="utf-8"), re.S):
        assert not tag.strip()
