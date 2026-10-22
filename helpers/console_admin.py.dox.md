# helpers/console_admin.py

## Purpose

- Let the person at the appliance's console give a product administrator a new password, from the setup menu
  (`deploy/iso/host/rezztor-setup`, entry `admin`). With one administrator and no mail, a forgotten password had
  no way back; the console is the higher trust, and whoever sits there could reinstall the machine anyway.

## Contracts

- Run on the host, never imported by the web application:
  `docker exec rezztor-eagent sh -c 'cd /eagent && exec /opt/venv-eagent/bin/python -c "from helpers.console_admin import main; main()" "$@"' sh <command>`.
- `list` prints one line per account the console may reset, fields separated by a tab: user name, display name,
  last sign-in (`YYYY-MM-DD HH:MM` UTC, or `never`). Control characters are blanked and each field is at most 60
  characters, because the line ends up on a terminal.
- `reset USERNAME` prints the new password on one line and nothing else on stdout. Exit status 1 with the reason
  on stderr when the name is not such an account.
- `administrators()` is the rule for both: role Administrator, not disabled, provider `local`. An identity
  provider's account has no password here to set.
- `reset(username)` uses `users.generate_password()` (20 characters) and
  `users.update_user(..., password=..., must_change_password=True, actor="console")`, so every rule of the
  account store applies, `updated_by` says `console`, and a mailed reset link stops working.

## Side Effects

- The account's password hash changes, so every session of it ends (`helpers/login.py` binds a session to the
  hash it was issued under) and the next sign-in in the browser asks for a new password.
- One audit event, `account.update` with `password_changed`, `password_generated` and `via: console`, written to
  `PRODUCT_LOG` (`/proc/1/fd/1`, the container's own output, which is the product's log) and not to the caller's
  console. When that cannot be opened it goes to stderr. A failure to audit never fails the reset.
- The password is printed once and kept nowhere: not in a file, not in a log, not in the audit event.

## Not Covered

- The sign-in throttle (`helpers/login_throttle.py`) lives in the web process's memory; a separate process cannot
  clear it. It forgets an address after 15 minutes, or at a restart.

## Verification

- `pytest tests/test_console_admin.py` (needs the test database).
- On an installed machine: menu entry `admin`, choose the account, the old password stops working and the next
  sign-in asks for a new one.
