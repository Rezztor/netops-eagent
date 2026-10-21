# login_page.py DOX

## Purpose

- Own the `login_page.py` helper module.
- This module renders the three server-side, unauthenticated HTML pages: sign-in, "forgot password", and "choose a new password".
- Keep this file-level DOX profile synchronized with `login_page.py` because this directory is intentionally flat.

## Ownership

- `login_page.py` owns the runtime implementation.
- `login_page.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
  - `_page(title: str, body: str) -> str`: assemble one page from the shared `_HEAD`/`_HEAD_REST`/`_FOOT` template pieces and a page-specific Jinja body.
- Module-level data: `LOGIN_PAGE`, `FORGOT_PAGE`, `RESET_PAGE` — pre-built Jinja template strings consumed via Flask's `render_template_string`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- `FORGOT_PAGE` takes `login_form` (the hidden form token), `notice` and `error`.
- Consumed exclusively by `helpers/ui_server.py`, which renders `LOGIN_PAGE` (`/login`), `FORGOT_PAGE` (`/forgot-password`), and `RESET_PAGE` (`/reset-password`) via `render_template_string` — the escaping this gives matters because `next` and `token` come from the URL.
- Self-contained by design: no external stylesheet, image, or script reference other than the self-hosted `/app/fonts/plus-jakarta-sans-latin.woff2` (served unauthenticated by `helpers/spa.py:serve_font`), which is a progressive enhancement — the CSS falls back to the system font stack if the build is missing.
- The CSS custom-property palette (`--bg`, `--accent`, etc.) is a **second, literal copy** of `frontend/src/styles/tokens.css`, because this page renders before the frontend bundle can load and cannot import from it. `tests/test_brand_palette_consistency.py` fails if the two diverge — update both files in the same change.
- `LOGIN_PAGE` expects a `login_form` value (the session's form token, `helpers.login.login_form_token()`) and posts it back in a hidden field; a form rendered without it cannot sign in.
- Renders before a session exists, so it cannot honor a user's stored theme preference; only `prefers-color-scheme` decides light vs. dark.
- Observed side-effect areas: none — the module is pure (builds and returns template strings; no filesystem, network, or state access).
- Imported dependency areas include: none beyond `__future__`.

## Key Concepts

- Important called helpers/classes observed in the source: `_HEAD.format`, string concatenation of `_HEAD` + `_HEAD_REST` + body + `_FOOT`.
- The three pages deliberately share one stylesheet rather than each carrying its own copy, because a second copy of the palette is already one too many; a third and fourth would compound the drift risk the module's docstring calls out.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Any change to `frontend/src/styles/tokens.css` colors must be mirrored here in the same commit, or sign-in visibly drifts from the application shell.
- `--ink-3` (muted text) is held to WCAG AA, 4.5:1, on every surface in both themes; `tests/test_brand_palette_consistency.py` checks the token file, and this copy follows it.
- Keep this page's brand mark, radii, focus treatment, and font stack matching `frontend/src/routes/shell.module.css`.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_brand_palette_consistency.py`
  - `tests/test_member_mail.py`
  - `tests/test_spa_serving.py`

## Child DOX Index

No child DOX files.

- The page carries its tab icon inline (`_HEAD_REST`), so a browser does not ask for `/favicon.ico`, which nobody serves.
