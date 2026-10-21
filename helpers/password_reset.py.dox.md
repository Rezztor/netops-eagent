# password_reset.py DOX

## Purpose

- Own the `password_reset.py` helper module.
- This module issues, consumes, and revokes single-use password-reset tokens for the self-service "Forgot password?" flow, and drives the send/complete flow shared by the JSON API endpoints and the server-rendered pages.
- Keep this file-level DOX profile synchronized with `password_reset.py` because this directory is intentionally flat.

## Ownership

- `password_reset.py` owns the runtime implementation.
- `password_reset.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Exceptions:
- `ResetError` (`Exception`).
- Top-level functions:
  - `address_may_request(address: str | None, forwarded_for: str | None = None) -> bool`: counts one request from a source address and returns False once it has made `ADDRESS_REQUESTS` (5) in `ADDRESS_WINDOW_SECONDS` (15 min), whatever it named. In memory, capped at 4096 addresses. The key (`_address_key`) is the peer; an IPv6 address counts as its /64. Behind an unnamed proxy on this host (`login_throttle._is_unnamed_proxy`: loopback or the Docker gateway with `FORWARDED_ALLOW_IPS` empty) it is the last `X-Forwarded-For` hop, the one that proxy appended, and with no usable hop the limit is not applied. A private LAN peer is limited like any other. Both front doors call it before `send_link_later`; `audit_refused(via)` records the refusal as `password_reset.requested outcome=address_rate_limited`. `forget_addresses()` is for tests.
  - `recently_requested(user_id: str) -> bool`: whether this account was sent a link within `REQUEST_INTERVAL_SECONDS`; checked before sending so a flood costs one message, not one per request.
  - `issue(user_id: str) -> tuple[str, int]`: mint a fresh token, discarding any outstanding one for the account, and return `(token, lifetime_seconds)`.
  - `consume(token: str) -> str`: spend a token under a row lock (`FOR UPDATE`) and return the owning user id; raises `ResetError` for an invalid, already-used, or expired token.
  - `revoke_for_user(user_id: str) -> int`: drop every outstanding link for an account; called after any other successful password change.
  - `send_link(identifier: str, base_url: str) -> str`: the whole send flow — never raises, returns an outcome string for the audit log (`"empty"`, `"no_such_account"`, `"disabled"`, `"rate_limited"`, `"no_address"`, `"no_base_url"`, `"send_failed"`, `"sent"`).
  - `complete(token: str, new_password: str) -> tuple[bool, str]`: validate password length, spend the token, update the account's password, and revoke any remaining links.
  - `_now`, `_digest`, `_connection`, `_prune`: internal helpers.
- Constants: `STORE_PATH` (legacy name, storage is now the `password_resets` table), `TOKEN_LIFETIME_SECONDS` (30 min), `REQUEST_INTERVAL_SECONDS` (60s), `MAX_RECORDS` (500), `ADDRESS_REQUESTS` (5), `ADDRESS_WINDOW_SECONDS` (15 min).

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Deliberately not reachable by the agent — only the "Forgot password?" web flow issues tokens, so an agent whose context was poisoned by an untrusted document cannot mint a credential-bearing link.
- Only the SHA-256 digest of a token is stored (`password_resets.digest`); the raw 32-byte token is shown once, in one email, and cannot be recovered from the store.
- A token is marked consumed inside the same locked transaction that reads it (`SELECT ... FOR UPDATE`), so two requests racing the same link cannot both succeed.
- `send_link` **always returns to its caller without varying its behavior** based on whether the account exists, has an address, or has mail configured — callers (the API handlers, the server-rendered page) must not let their response to an unauthenticated caller differ by outcome, or the endpoint becomes a way to discover who holds an account. The real outcome is for the audit log only.
- `password_reset_enabled` in settings can disable the flow instance-wide, except for administrators — administrators are exempt so the setting can never lock out the person who controls it (mirrors the last-administrator guard in `helpers/users.py:update_user`). The check happens after the account lookup specifically so the caller-visible outcome constant does not change shape.
- `send_link_later(identifier, base_url, via)` queues `send_link` and its `password_reset.requested` audit event on a two-worker pool (`_sender`) and returns at once. Both front doors call it, so a registered identifier (token insert, SMTP delivery) does not answer measurably later than an unknown one (one lookup). The queue is unbounded; the workers are not, so a flood costs queue memory and never more than two database connections. `tests/test_password_reset_timing.py` covers this.
- `complete()` validates the new password with `users.check_password` (too short, and too long for the hash) *before* consuming the token, so a password that cannot be set does not cost the user the link.
- `_connection()` uses `helpers.db.transaction(None)`, i.e. the ambient scope — password-reset rows are not owner-scoped in practice since the caller is unauthenticated, but the module goes through the normal transaction helper rather than the owner role.
- Observed side-effect areas: settings/state persistence (`password_resets` table), network calls (via `helpers.mailer.send_to_user`), secret handling (tokens, digests).
- Imported dependency areas include: `hashlib`, `json`, `os`, `secrets`, `time`, `typing`, `helpers.files`, `helpers.db` (deferred), `helpers.mailer` (deferred), `helpers.rbac` (deferred), `helpers.settings` (deferred), `helpers.users` (deferred), `helpers.print_style` (deferred).

## Key Concepts

- Important called helpers/classes observed in the source: `hashlib.sha256(...).hexdigest`, `secrets.token_urlsafe`, `mailer.resolve_recipient`, `mailer.send_to_user`, `users.get_user`, `users.get_user_by_email`, `users.update_user`, `rbac.is_admin`, `settings.get_settings`.
- `_prune` is belt-and-braces cleanup (drop spent/expired rows, then anything past `MAX_RECORDS`), run at the start of `issue()`; it is not the mechanism that stops a token from being reused — the row lock in `consume()` is.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Request handlers call `send_link_later`, not `send_link`, so their timing does not vary either.
- Any new caller of `send_link` must return the same response regardless of the outcome string; never branch the HTTP/page response on it.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_member_mail.py`

## Child DOX Index

No child DOX files.
