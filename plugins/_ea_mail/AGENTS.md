# Member Email Plugin DOX

## Purpose

- Own the agent's ability to email people who hold accounts on this instance.

## Ownership

- `helpers/policy.py` owns who may email whom, and the per-sender rate limit.
- `tools/send_user_email.py` owns the tool the agent calls.
- `prompts/` owns what the agent is told about it.
- The transport, and the rule that a recipient is an account, live in core
  `helpers/mailer.py`. Password reset is core too (`helpers/password_reset.py`)
  and is deliberately not reachable from here.

## Local Contracts

- A recipient is named by username or user id. Never by address. The moment an
  address can be passed in, a sentence in a document the agent was asked to read
  can name a mailbox outside the organisation, and the platform sends it under
  its own name.
- Every path to SMTP goes through `mailer.send_to_user`, which resolves the
  address from the user store.
- An agent may email the account whose conversation it is. Any other account
  requires the operator to hold `users.manage`, which already implies access to
  every account's address.
- The provenance footer is added by the tool and cannot be supplied or
  suppressed by the agent. A recipient must be able to see which person a message
  was sent on behalf of.
- The rate limit is recorded before the send, not after: a mail server that fails
  slowly must not let a loop run uncounted.
- Nothing here issues, sees or emails a credential. Password reset is
  self-service, from the sign-in page.

## Verification

- `pytest tests/test_member_mail.py`
- With SMTP configured, ask the agent to email your own account, then ask it to
  email a colleague from a standard-user account and confirm it is refused.

## Child DOX Index

No child DOX files.
