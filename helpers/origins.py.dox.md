# origins.py DOX

## Purpose

- Own the `origins.py` helper module.
- This module normalizes a URL or `Origin` header value to a bare `scheme://host[:port]` string, shared by the CSRF origin check and (per the module docstring) the WebSocket origin validator.
- Keep this file-level DOX profile synchronized with `origins.py` because this directory is intentionally flat.

## Ownership

- `origins.py` owns the runtime implementation.
- `origins.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
  - `origin_from_url(value) -> str | None`: parse a URL/Origin header into `scheme://host[:port]`, lower-cased, with the port omitted when it matches the scheme's default (80 for `http`/`ws`, 443 for `https`/`wss`); returns `None` for anything that does not parse to a scheme and hostname, or whose port fails to parse.
- Module-level data: `_DEFAULT_PORTS`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Currently imported by `api/csrf_token.py` (`GetCsrfToken.is_allowed_origin`), which uses it to normalize both the request's `Origin` header and each entry in the configured allowlist before comparing them — the normalization is what makes `https://example.com` and `https://example.com:443` compare equal.
- A non-URL, non-string, or unparsable value returns `None` rather than raising, so a malformed `Origin` header degrades to "does not match any allowed origin" instead of crashing the request.
- Observed side-effect areas: none — the module is pure (string parsing only).
- Imported dependency areas include: `urllib.parse`.

## Key Concepts

- Important called helpers/classes observed in the source: `urlparse`, `parsed.hostname`, `parsed.port` (guarded by `try`/`except ValueError` for a malformed port).
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Any change to default-port handling or scheme normalization affects CSRF origin checking directly; treat it as security-sensitive even though the module itself is small and pure.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- No dedicated test file was found by name search for `origins.py` itself; its behavior is exercised indirectly through `api/csrf_token.py`'s origin-allowlist check. Add a focused test alongside any behavioral change here.

## Child DOX Index

No child DOX files.
