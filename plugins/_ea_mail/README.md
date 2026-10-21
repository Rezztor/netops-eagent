# Member Email

Lets the agent email people who hold accounts on this instance — reminders,
updates, follow-ups, handovers.

It cannot email anyone else. That is the feature, not a setting.

## Why a username and not an address

The agent writes these messages, and it writes them out of whatever reached its
context: a web page it read, a document it was handed, the output of a tool. If
the tool took an email address, a sentence buried in any of those could name a
mailbox outside the organisation and the platform would send it — with the
platform's name in the From line.

So the tool takes the **username of an account here**. `helpers/mailer.py` reads
the address off that account. There is no code path from the agent's arguments to
an arbitrary mailbox, and a test asserts that an argument containing `@` is
refused rather than helpfully interpreted.

The worst an injected instruction can do is mail the wrong colleague. That is
bad, bounded, rate limited, and in the audit log.

## Who may reach whom

| Operator | May email |
|---|---|
| Anyone | The account the conversation belongs to |
| Holder of `users.manage` | Any account on the instance |

`users.manage` already reads every address, creates accounts and resets
passwords, so an administrator's agent reaching another member adds no capability
its operator lacks. A standard user's agent reaching a colleague would.

Set `allow_admin_to_email_others: false` to make the tool self-only for
everybody.

## An administrator's switch on self-service reset

`password_reset_enabled` in Settings turns off the "Forgot password?" flow for
everyone **except administrators**. Administrators are exempt on purpose: this
is the one self-service recovery path on the platform, and a switch that can
lock the person who controls it out of their own account turns a
misconfiguration into an outage with no way back in through the UI.

The page's reply is unchanged either way -- the same generic "if that account
exists..." message whether the account is real, unregistered, or reset is
switched off entirely. Making the message depend on the outcome would leak
which accounts hold the admin role to anyone who can see the sign-in screen,
since an admin's request would silently succeed while everyone else's showed
different words.

## What it will not do

Send a password, a token, an API key or a reset link. Password reset is
self-service — "Forgot password?" on the sign-in page — and nothing in a
conversation can start it. An agent able to mint a credential-bearing link would
be one poisoned web page away from an account takeover.

## Configuration

SMTP lives in **Settings → Email**, not here. The password is kept in the
environment; the settings file holds a placeholder.

`default_config.yaml` carries the two things that are this plugin's own: the
hourly cap per sender, and whether administrators may reach other accounts.

## Where things live

```
helpers/mailer.py                 core: transport, and "a recipient is an account"
helpers/password_reset.py         core: reset tokens, not reachable from here
plugins/_ea_mail/helpers/policy.py  who may email whom, and how often
usr/mail_sent.json                the rate-limit window, per sender
```
