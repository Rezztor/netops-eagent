# Email Integration

Communicate with Rezztor EAgent through email inboxes and send replies back over SMTP.

## What It Does

This plugin polls configured mailboxes, downloads incoming messages and attachments, decides whether a message should continue an existing chat or start a new one, and sends replies back by email.

It supports both:

- **IMAP inbox polling**
- **Exchange inbox polling**
- **SMTP replies**

## Who may write to it

A handler answers **only** the senders its `sender_whitelist` names. An empty or
missing list accepts nothing and the handler says so in the log rather than
polling -- inbound mail reaches an agent that executes code, so absence of
configuration is not consent.

Entries are fnmatch patterns: `"someone@example.com"` is one person,
`"*@example.com"` a domain, and `"*"` anyone at all. The address inside angle
brackets is the one matched, so a display name claiming to be an allowed sender
does not get in on their behalf.

**The whitelist is not authentication.** It matches the `From` header, which the
sender writes; this plugin checks no SPF, DKIM, DMARC or `Authentication-Results`.
A mail with a forged `From` that your mail server accepts starts an agent run (with
no account behind it), and the agent's answer is sent to the forged address. Name
individual addresses rather than domains or `*`, and have the receiving mail server
reject mail that fails DMARC for every whitelisted domain.

## Main Behavior

- **Mailbox polling**
  - Tracks per-handler mailbox state in `usr/email/state.json`.
  - Uses UID tracking for IMAP accounts so only new mail is processed after initialization.
- **Attachment handling**
  - Downloads attachments into `usr/email/attachments`.
- **Dispatcher workflow**
  - Keeps its own background `Email Dispatcher` context, held by reference and never looked up by name (any account can name a chat that).
  - Uses model prompts to decide whether an email belongs to an existing chat or should open a new one.
  - Supports handler-level model presets for new chats, in addition to the dispatcher's utility/chat routing mode.
- **Thread routing**
  - Can continue an existing chat by thread ID found in the email subject.
  - Falls back to model-based dispatch if no direct thread match is available.
  - Email replies inside an existing Rezztor EAgent thread can use `/project`, `/config`, and `/send` control commands.
- **Notifications and persistence**
  - Saves chats after routing and emits notifications about new or continued conversations.

## Key Files

- **Core orchestration**
  - `helpers/handler.py` manages polling, state persistence, dispatching, and reply flow.
- **Mail helpers**
  - `helpers/imap_client.py` handles IMAP and Exchange fetching.
  - `helpers/smtp_client.py` handles outbound replies.
  - `helpers/dispatcher.py` formats chat summaries and parses dispatcher decisions.
- **API and extensions**
  - `api/` and `extensions/` connect the polling loop and UI-facing operations into the main app.

## Configuration Scope

- **Settings section**: `external`
- **Per-project config**: `false`
- **Per-agent config**: `false`

## Plugin Metadata

- **Name**: `_email_integration`
- **Title**: `Email Integration`
- **Description**: Communicate with Rezztor EAgent via email. Supports IMAP/Exchange inbox polling with SMTP replies.
