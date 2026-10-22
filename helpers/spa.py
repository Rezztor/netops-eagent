"""Serve the built frontend from the backend, same origin.

Authentication is a Flask session
cookie with SameSite=Lax plus a CSRF token, which only works when the page and
the API share an origin. The most direct way to guarantee that is for the
backend to serve the bundle itself -- there is then no proxy to misconfigure and
no second origin to reason about. A reverse proxy still sits in front for TLS,
but it no longer has to split routes to keep authentication working.

Mounted at /app rather than / because Flask already serves the inherited
`webui/` directory as its static folder at the root, and because both interfaces
have to work at once. Stage 7 removes the old one; until then, replacing it would
delete far more capability than the new frontend has yet replaced.

There is one interface. The inherited `webui/` directory was removed at stage 7
once the port reached parity, and with it the REZZTOR_UI flag that chose between
them -- a switch with one position is just a way to break the product.

MOUNT_PATH is the single source of truth for where the application lives, and
three things have to agree on it: this constant, vite's `base` in
frontend/vite.config.ts, and the BrowserRouter `basename` in main.tsx. They are
separate files with no link between them, so
tests/test_spa_serving.py checks that they still say the same thing.

Serving the shell anywhere else does not work and does not look like it failed.
The bundle loads, the router finds a location outside its basename, matches no
route, and paints an empty page -- indistinguishable from a broken build. So the
root redirects here instead of serving a copy.

The build is load-bearing: `frontend/dist` is gitignored and produced by
`npm run build`, which the image does in `docker/run/fs/ins/build_frontend.sh`.
There is no longer a second interface to fall back to, so a missing build is
now a plain failure with an explanatory message rather than a silent switch.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from flask import Response, send_file

from helpers import files
from helpers.print_style import PrintStyle

# Where `npm run build` writes, and the path the /app routes serve from.
DIST_DIR = "frontend/dist"

MOUNT_PATH = "/app"

# Fonts are the one unauthenticated slice of the build. The sign-in page renders
# before a session exists and needs the same typeface as the application, or the
# first screen a user sees is the one screen off-brand.
#
# It sits *under* the mount rather than beside it so there is a single URL for
# each file: vite rewrites url() against `base`, so the bundle asks for
# /app/fonts/... regardless, and a second path outside /app would mean the same
# font downloaded and cached twice by anyone who signs in. Werkzeug ranks a rule
# with more static segments above one ending in a converter, so this wins over
# /app/<path:asset_path> -- tests/test_spa_serving.py pins that, because the
# whole design rests on it and route precedence is not obvious by reading.
FONTS_PATH = "/app/fonts"
_FONT_SUFFIXES = frozenset({".woff2"})

# Hashed asset filenames are content-addressed, so they can be cached hard.
# index.html must not be, or a deploy leaves browsers on the previous bundle.
_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
_NO_CACHE = "no-store"

# The shell loads only its own bundle: no inline or third-party script, so an
# HTML sink added later, or a compromised dependency, cannot load more. Inline
# styles are React `style` props; blob: images are the file previews
# (`apiObjectUrl`); data: is the favicon. connect-src 'self' covers the
# same-origin Socket.IO connection, ws: and wss: included.
SHELL_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def dist_path() -> Path:
    return Path(files.get_abs_path(DIST_DIR))


def is_built() -> bool:
    """True when a production build exists to serve."""
    return (dist_path() / "index.html").is_file()


def _index_response() -> Response:
    index = dist_path() / "index.html"
    return Response(
        index.read_text(encoding="utf-8"),
        content_type="text/html; charset=utf-8",
        headers={"Cache-Control": _NO_CACHE, "Content-Security-Policy": SHELL_CONTENT_SECURITY_POLICY},
    )


def serve_asset(asset_path: str = "") -> Response:
    """Serve one file from the build, falling back to index.html.

    The fallback is what makes client-side routing work: /app/users is a route
    the browser knows about and the server does not, so an unknown path returns
    the shell and lets the router resolve it. Only paths that look like files are
    allowed to 404, so a genuinely missing asset still reports as missing instead
    of silently returning HTML to something expecting JavaScript.
    """
    if not is_built():
        # There is no second interface to fall back to any more, so this is the
        # whole product failing. Say what to do rather than returning a bare 503.
        PrintStyle.error(
            f"{DIST_DIR}/index.html is missing: the web interface cannot be served. "
            "Run `npm run build` in frontend/, or rebuild the image, which does it."
        )
        return Response(
            "The web interface has not been built.\n\n"
            "Run `npm run build` in frontend/, or rebuild the container image.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    root = dist_path().resolve()
    if asset_path:
        candidate = (root / asset_path).resolve()
        # Containment check before any filesystem access: asset_path comes
        # straight from the URL.
        if root == candidate or root in candidate.parents:
            if candidate.is_file():
                guessed, _ = mimetypes.guess_type(candidate.name)
                response = send_file(candidate, mimetype=guessed or "application/octet-stream")
                response.headers["Cache-Control"] = (
                    _IMMUTABLE_CACHE if "/assets/" in f"/{asset_path}" else _NO_CACHE
                )
                return response

        # A request that names a file extension and did not resolve is a missing
        # asset, not a client-side route.
        if Path(asset_path).suffix:
            return Response("Not found", status=404, content_type="text/plain; charset=utf-8")

    return _index_response()


def serve_font(asset_path: str = "") -> Response:
    """Serve one font file, without requiring a session.

    Deliberately narrow. It answers only for `.woff2` under `dist/fonts`, so
    pointing it at anything else in the build returns 404 rather than leaking a
    file that happens to live under the same root. The containment check runs
    before any filesystem access because `asset_path` comes straight from the
    URL.

    A 404 here is cosmetic, never fatal: every stylesheet that names these
    families also names a full system fallback stack, so a missing font changes
    the typeface and nothing else. That is the difference between this and the
    inherited login page, which lost its entire stylesheet with its static
    folder.
    """
    if Path(asset_path).suffix.lower() not in _FONT_SUFFIXES:
        return Response("Not found", status=404, content_type="text/plain; charset=utf-8")

    root = (dist_path() / "fonts").resolve()
    candidate = (root / asset_path).resolve()
    if root != candidate and root not in candidate.parents:
        return Response("Not found", status=404, content_type="text/plain; charset=utf-8")
    if not candidate.is_file():
        return Response("Not found", status=404, content_type="text/plain; charset=utf-8")

    response = send_file(candidate, mimetype="font/woff2")
    # Content-addressed by name and effectively immutable: a new cut of a face
    # ships under a new filename.
    response.headers["Cache-Control"] = _IMMUTABLE_CACHE
    return response
