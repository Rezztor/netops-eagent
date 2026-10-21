# Email Integration Plugin DOX

## Purpose

- Own communicating with Rezztor EAgent through email inboxes and SMTP replies.

## Ownership

- `helpers/handler.py` owns mailbox polling, state, dispatch, routing, and reply flow.
- `helpers/imap_client.py` and `helpers/smtp_client.py` own inbound and outbound mail transports.
- `helpers/dispatcher.py`, `prompts/`, and attachment helpers own routing decisions and message formatting.
- `api/`, `default_config.yaml`, `plugin.yaml`, `extensions/`, and `webui/` own tests, settings, metadata, hooks, and config UI.

## Local Contracts

- Treat mailbox credentials and downloaded attachments as sensitive.
- Preserve UID/state tracking so restarts do not duplicate old mail.
- A mail is taken once on both transports: IMAP marks it `\Seen` and keeps the highest UID; Exchange keeps no
  position, so `fetch_unread_exchange` marks each item read as it takes it and skips one it cannot mark.
- Keep SMTP replies threaded and safe around user-visible errors.
- A reply that fails to send is logged to the chat through `helpers.errors.log_failure` (`EmailSendError`): one plain sentence, the error text only for an administrator. `smtp_client.send_reply` prints the traceback to the server log and returns only the exception's class and message, which is what the agent is told.

- SMTP uses a verified TLS context. A reply attaches only files that pass `helpers.workdir.confine_path`, so a handler with no account in scope attaches nothing. The dispatcher may continue only a conversation the sender already has. The dispatcher's context is held in `handler._dispatcher_ctx`; never find a context by its display name, which any account can set. The sender whitelist matches the `From` header and is documented as not authentication (README, `default_config.yaml`).

## Work Guidance

- Coordinate polling, dispatch prompts, and WebUI settings when adding account or routing behavior.

## Verification

- Smoke-test connection checks, polling, attachment handling, dispatch routing, and SMTP replies when practical.

## Child DOX Index

No child DOX files.
