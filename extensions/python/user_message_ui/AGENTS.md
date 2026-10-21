# User Message UI Extensions DOX

## Purpose

- Own backend behavior triggered around user-visible UI messages.

## Ownership

- Ordered Python files own update-check messaging and future user-message UI hooks.
- `_10_redact_typed_secrets.py` replaces a password-like value typed into chat with `[REDACTED:<type>]` before the message is logged, stored or sent to the model, and logs a warning item pointing to the secure credential form. It fails open (logs, passes the message) and never records the value.

## Local Contracts

- Keep proactive UI messages relevant, non-spammy, and safe for display.
- Keep update-available notifications visible until the user dismisses them or opens the updater.
- Do not expose local diagnostics or update data that should stay internal.

## Work Guidance

- Gate recurring messages so they do not repeat unnecessarily across chats or tabs.

## Verification

- Smoke-test UI message rendering after changes.

## Child DOX Index

No child DOX files.
