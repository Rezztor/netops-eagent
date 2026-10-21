# update_status.py DOX

## Purpose

- Own the `update_status.py` API endpoint.
- Serves the state of the appliance's update service to the administrator's Updates page: `GET/POST /api/update_status` → the payload of `helpers.update_spool.status()`.

## Ownership

- `UpdateStatus` — read-only.

## Runtime Contracts

- Administrator-only: `required_permission()` returns `rbac.PERM_SETTINGS_MANAGE`, and the endpoint is listed in `helpers/api_policy.py`; auth and CSRF are applied centrally by `helpers/api.py` dispatch.
- No side effects. The answer is the host tool's state file as it is: `available`, `phase` (`idle`, `downloading`, `checked`, `installing`, `staged`, `restarting`), `installed`, `update_key`, `keys`, `package`, `steps`, `error`, `result`, `history`, `request`, `at`, `bundles`, `bundle_error`, and from the update service (ADR 0091) `updates` (`checked_at`, `server`, `plan`, `licence`, `core`, `available` oldest first without what is installed, `blocked` when the next package is in a format the tool cannot read, `error` `{code, text}`), `progress` and `download`.
- Plus `daily_check`: the instance setting `update_daily_check` (`helpers.update_spool.daily_check_enabled`).
- `{"available": false}` when this installation has no update service.
- It works in every licence state, including restricted: a fix may be what makes the licence work.
- While the host restarts the stack this endpoint does not answer; the page treats that as "still restarting".

## Verification

- `pytest tests/test_update_api.py`.

## Child DOX Index

No child DOX files.
