# mailer.py DOX

## Purpose

- Own the `mailer.py` helper module.
- This module sends mail to accounts on this instance over SMTP, addressed only by user id, never by a caller-supplied address.
- Keep this file-level DOX profile synchronized with `mailer.py` because this directory is intentionally flat.

## Ownership

- `mailer.py` owns the runtime implementation.
- `mailer.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Exceptions:
- `MailError` (`Exception`) — something the caller should see: not configured, no address, refused.
- `NotConfigured` (`MailError`).
- Classes:
- `Recipient` (`@dataclass(slots=True)`) — `user_id`, `username`, `display_name`, `address`.
- Top-level functions:
  - `is_address_shaped(value: str) -> bool`: shape-only address check (one `@`, a dot in the domain); not RFC conformance.
  - `config() -> dict`: current SMTP configuration, with the password read from `helpers.dotenv` (never from `settings.json`).
  - `is_available() -> tuple[bool, str]`: whether mail can be sent, and if not, which field is missing.
  - `resolve_recipient(user_id: str) -> Recipient`: the only way to turn a user id into an address; raises `MailError` if the account is missing, disabled, or has no valid address.
  - `send_to_user(user_id: str, *, subject: str, body: str, footer: str = "") -> Recipient`: the only public route to SMTP that takes a body; validates length and delegates to `_send`.
  - `_send(address: str, *, subject: str, body: str) -> None`: private; the only function in the module that takes a raw address rather than a user id.
  - `_connect(settings: dict)`: open an `smtplib.SMTP`/`SMTP_SSL` connection per the configured security mode.
- Constants: `MAX_SUBJECT`, `MAX_BODY`.

## Runtime Contracts

- Helper modules own reusable framework APIs and must preserve public callers unless all callers, tests, and docs are updated together.
- Update this file whenever public functions, classes, persistence behavior, path/security assumptions, side effects, or cross-module contracts change.
- Every public entry point except `_send` takes a **user id**, never an address. The reason is the agent: since the agent composes message bodies from whatever is in its context (a web page, an uploaded document, a tool result), a caller that could pass a free-form address would let an injected instruction mail outside the organisation under the platform's name.
- `_send` is private specifically to preserve that boundary; the one caller that needs a bare address (`helpers/password_reset.py`, for a locked-out account that cannot be looked up any other way while signed out) goes through `send_to_user`/`resolve_recipient` like everything else — password reset resolves the recipient by user id first.
- SMTP credentials: the password comes from the environment via `helpers.dotenv.get_dotenv_value(dotenv.KEY_SMTP_PASSWORD)`, never from the persisted settings document, which holds only a placeholder.
- `security` defaults to `starttls`; `none` exists only for a trusted internal relay and must be chosen deliberately, since getting it by accident sends credentials in the clear.
- Sent messages carry `Auto-Submitted: auto-generated` to avoid an out-of-office reply loop against a monitored mailbox.
- Observed side-effect areas: network calls (SMTP), secret handling.
- Imported dependency areas include: `re`, `smtplib`, `ssl`, `dataclasses`, `email.message`, `email.utils`, `helpers.users`, `helpers.print_style`, `helpers.dotenv` (deferred), `helpers.settings` (deferred).

## Key Concepts

- Important called helpers/classes observed in the source: `users.get_user_by_id`, `bcrypt`-independent address regex `_ADDRESS.match`, `EmailMessage`, `formataddr`, `make_msgid`, `server.login`, `server.send_message`, `ssl.create_default_context`.
- Keep request/response, tool, or helper semantics documented here at the same time as source changes.

## Work Guidance

- Preserve public helper APIs used by core code and plugins unless every caller is updated.
- Keep path, auth, secret, persistence, network, and subprocess behavior explicit and bounded.
- Never add a public function that accepts a raw destination address; route everything through a user id and `resolve_recipient`.

## Verification

- Run targeted tests for changed helper behavior; run security regressions for auth, filesystem, WebSocket, tunnel, upload, or secret-handling helpers.
- Related tests observed by source search:
  - `tests/test_member_mail.py`

## Child DOX Index

No child DOX files.
