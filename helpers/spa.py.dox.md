# spa.py DOX

## Purpose

- Own the `spa.py` helper module.
- This module serves the built React frontend (`frontend/dist`) from the backend at `/app`, same-origin with the API, including client-side-routing fallback to `index.html` and an unauthenticated font route.
- Keep this file-level DOX profile synchronized with `spa.py` because this directory is intentionally flat.

## Ownership

- `spa.py` owns the runtime implementation.
- `spa.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
  - `dist_path() -> Path`: absolute path to `frontend/dist`.
  - `is_built() -> bool`: whether `frontend/dist/index.html` exists.
  - `_index_response() -> Response`: the shell HTML, always `Cache-Control: no-store`.
  - `serve_asset(asset_path: str = "") -> Response`: serve one built file, falling back to `index.html` for anything that does not look like a file (client-side routes); 404s for a path with a file extension that does not resolve; 503 with an operator message if the build is missing.
  - `serve_font(asset_path: str = "") -> Response`: serve one `.woff2` under `dist/fonts` without requiring a session; 404 for anything else.
- Constants: `DIST_DIR`, `MOUNT_PATH` (`/app`), `FONTS_PATH` (`/app/fonts`), `_FONT_SUFFIXES`, `_IMMUTABLE_CACHE`, `_NO_CACHE`, `SHELL_CONTENT_SECURITY_POLICY`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- `MOUNT_PATH` is the single source of truth for where the frontend lives, and three separate files must agree on it: this constant, vite's `base` in `frontend/vite.config.ts`, and the `BrowserRouter` `basename` in `main.tsx`. `tests/test_spa_serving.py` checks they still agree.
- `serve_asset` and `serve_font` both do a path-containment check (`root == candidate or root in candidate.parents`) **before** any filesystem access, because `asset_path` comes straight from the URL — this is the security boundary that stops a crafted path from escaping `frontend/dist`.
- `FONTS_PATH` is deliberately nested *under* `MOUNT_PATH` (`/app/fonts`, not a sibling route) so there is exactly one URL per font file; Werkzeug ranks a rule with more static segments above one ending in a converter, so `/app/fonts` wins routing precedence over `/app/<path:asset_path>` — `tests/test_spa_serving.py` pins this because the whole unauthenticated-font design depends on it and the precedence is not obvious from reading the route table.
- `serve_font` only ever answers for `.woff2` files under `dist/fonts`; anything else 404s rather than falling through to serving an arbitrary file under the same root.
- A missing build (`is_built()` false) is a whole-product failure now — there is no second (legacy `webui/`) interface to fall back to — so `serve_asset` returns 503 with instructions (`npm run build`) rather than a silent switch.
- Hashed asset filenames under `/assets/` get `_IMMUTABLE_CACHE` (`public, max-age=31536000, immutable`); everything else, including `index.html` and fonts served via `serve_asset`, gets `_NO_CACHE` — `index.html` must never be cached hard, or a deploy leaves browsers on the previous bundle. (`serve_font` itself always sets `_IMMUTABLE_CACHE`, since font files are content-addressed by name.)
- The shell (`index.html`, including the client-routing fallback) is sent with `SHELL_CONTENT_SECURITY_POLICY`: scripts only from `'self'` (the build has no inline script, `tests/test_spa_serving.py` checks), inline styles allowed (React `style` props), images from `'self'`, `data:` (favicon) and `blob:` (file previews), `connect-src 'self'` (API and same-origin Socket.IO), `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`. A new third-party origin, inline script, worker or frame needs this policy changed in the same change.
- Observed side-effect areas: filesystem reads (the built bundle).
- Imported dependency areas include: `mimetypes`, `pathlib`, `flask`, `helpers.files`, `helpers.print_style`.

## Key Concepts

- Important called helpers/classes observed in the source: `send_file`, `mimetypes.guess_type`, `Path.resolve`, `candidate.is_file`, `Path(asset_path).suffix`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Never weaken the containment check in `serve_asset`/`serve_font`, and never move it after a filesystem call.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_spa_serving.py`

## Child DOX Index

No child DOX files.
